"""隔离 SQLite + 真实 LangGraph，验证执行检查点、失败与恢复；不调用外部模型或服务。"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession

from agentchat.settings import app_settings

if not app_settings.database:
    app_settings.database = {"url": "sqlite:///:memory:"}

from agentchat.api.services.conversation import ConversationService
from agentchat.database.dao.agent_run import RunConflict, RunDao, history_id
from agentchat.database.dao.conversation import ConversationDao
from agentchat.database.dao.history import HistoryDao
from agentchat.database.models.agent import AgentTable
from agentchat.database.models.agent_run import AgentRun, RunToolCall, utcnow
from agentchat.database.models.tool import ToolTable
from agentchat.schemas.completion import CompletionReq
from agentchat.services import agent_runs
from agentchat.services.agent_runs import RunService


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = (tmp_path / "runs.db").as_posix()
    sync_engine = create_engine("sqlite:///" + path)
    SQLModel.metadata.create_all(sync_engine)
    async_engine = create_async_engine("sqlite+aiosqlite:///" + path)
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    @contextmanager
    def sync_session():
        with Session(sync_engine) as session:
            yield session

    @asynccontextmanager
    async def async_session():
        async with maker() as session:
            yield session

    for module in ("agent_run", "conversation", "history", "tool", "research", "context", "experience", "sandbox"):
        monkeypatch.setattr(f"agentchat.database.dao.{module}.async_session_getter", async_session)
    for module in ("agent", "tool"):
        monkeypatch.setattr(f"agentchat.database.dao.{module}.session_getter", sync_session)
    monkeypatch.setattr(agent_runs, "checkpoint_path", lambda: str(tmp_path / "checkpoints.sqlite"))
    from agentchat.services.sandbox import SandboxService
    from tests.sandbox_support import FakeDockerBackend
    monkeypatch.setenv('AGENTCHAT_SANDBOX_ROOT', str(tmp_path / 'sandbox'))
    monkeypatch.setattr(SandboxService, 'backend_factory', FakeDockerBackend)
    FakeDockerBackend.calls = 0
    monkeypatch.setattr(ConversationService, "queue_conversation_title", lambda *args: None)
    with Session(sync_engine) as session:
        session.add(ToolTable(tool_id="weather", name="get_weather", display_name="天气", user_id="user", description="天气查询", logo_url=""))
        session.add(ToolTable(tool_id="private", name="send_email", display_name="邮件", user_id="other", description="私人邮件", logo_url=""))
        session.add(AgentTable(id="papers", logo_url="", name="论文检索", user_id="user", tool_ids=["weather"], system_prompt="论文检索策略", enable_memory=False))
        session.commit()
    yield SimpleNamespace(sync_engine=sync_engine, async_engine=async_engine)
    sync_engine.dispose()


def scenario(monkeypatch, *, weather_failures=0, model_failure=False, paper_gate=None, writing=False):
    from langchain.agents import create_agent
    from agentchat.core.agents.general_agent import AgentConfig, GeneralAgent, EmitEventAgentMiddleware, StreamAgentState
    stats = {"weather": 0, "paper": 0, "email": 0, "weather_failures": weather_failures,
             "model_failure": model_failure, "config": None}

    @tool(description="查询天气")
    async def get_weather(city: str):
        stats["weather"] += 1
        if stats["weather_failures"]:
            stats["weather_failures"] -= 1
            raise TimeoutError("temporary timeout")
        return "晴，20度"

    @tool(description="检索论文")
    async def get_arxiv(query: str):
        stats["paper"] += 1
        if paper_gate:
            await paper_gate.wait()
        return "论文结果"

    @tool(description="模拟外部写操作，不发送真实邮件")
    async def send_email(receiver: str):
        stats["email"] += 1
        raise TimeoutError("sent remotely but reply lost")

    class ScriptedModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "test-run-model"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            last = messages[-1]
            if isinstance(last, HumanMessage):
                call = {"name": "send_email", "args": {"receiver": "test@example.invalid"}, "id": "mail-call"} if writing else {
                    "name": "get_weather", "args": {"city": "杭州"}, "id": "weather-call"}
                answer = AIMessage(content="", tool_calls=[call])
            elif isinstance(last, ToolMessage) and last.name == "get_weather" and last.status != "error":
                if stats["model_failure"]:
                    stats["model_failure"] = False
                    raise RuntimeError("model stopped after weather")
                answer = AIMessage(content="", tool_calls=[{"name": "get_arxiv", "args": {"query": "天气"}, "id": "paper-call"}])
            else:
                answer = AIMessage(content="报告完成" if not writing else "已说明邮件执行结果")
            return ChatResult(generations=[ChatGeneration(message=answer)])

    class LocalAgent(GeneralAgent):
        async def init_agent(self):
            self.run_context.read_only_tools.update({"get_weather", "get_arxiv"})
            self.react_agent = create_agent(ScriptedModel(), [get_weather, get_arxiv, send_email],
                middleware=[EmitEventAgentMiddleware(lambda name: ("工具", name), self.run_context)],
                checkpointer=self.checkpointer, state_schema=StreamAgentState)

    def factory(config, saver, context):
        stats["config"] = config
        return LocalAgent(AgentConfig(**config), checkpointer=saver, run_context=context)
    monkeypatch.setattr(RunService, "agent_factory", staticmethod(factory))
    return stats


async def create(request_id="request-1", agent_id=None):
    conversation = await ConversationService.create_conversation("新会话", agent_id or "general", "Agent", "user")
    request = CompletionReq(conversation_id=conversation["conversation_id"], user_input="查天气并检索论文，生成报告", agent_id=agent_id)
    run = await RunService.create(request, "user", request_id)
    return run, request


async def complete(run_id):
    task = RunService.workers.get(run_id)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 15)
    return await RunDao.get(run_id)


async def cleanup(database):
    await RunService.shutdown()
    await ConversationService.stop_conversation_title_tasks()
    await database.async_engine.dispose()


def test_real_graph_retries_tools_and_keeps_user_tools_isolated(database, monkeypatch):
    stats = scenario(monkeypatch, weather_failures=2)
    async def check():
        try:
            run, request = await create()
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["weather"] == 3 and stats["paper"] == 1
            assert stats["config"]["tool_ids"] == ["weather"]
            repeated = await RunService.create(request, "user", "request-1")
            assert repeated.id == run.id and stats["weather"] == 3
            rows = await HistoryDao.get_conversation_history(run.conversation_id)
            assert [row.role for row in rows] == ["user", "assistant"]
            assert rows[-1].content == "报告完成"
            assert (await RunDao.tool(run.id, "weather-call")).attempts == 3
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_resume_uses_checkpoint_without_repeating_completed_weather(database, monkeypatch):
    stats = scenario(monkeypatch, model_failure=True)
    async def check():
        try:
            run, _ = await create()
            result = await complete(run.id)
            assert result.status == "failed" and stats["weather"] == 1
            await RunService.resume(run.id, "user", {})
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["weather"] == 1 and stats["paper"] == 1
            rows = await HistoryDao.get_conversation_history(run.conversation_id)
            assert len(rows) == 2 and rows[-1].content == "报告完成"
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_unknown_external_write_needs_explicit_decision_and_can_be_skipped(database, monkeypatch):
    stats = scenario(monkeypatch, writing=True)
    async def check():
        try:
            run, _ = await create()
            result = await complete(run.id)
            assert result.status == "awaiting_input", result.error
            assert stats["email"] == 1
            with pytest.raises(RunConflict):
                await RunService.resume(run.id, "user", {})
            await RunService.resume(run.id, "user", {"mail-call": "skip"})
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["email"] == 1
            assert (await RunDao.tool(run.id, "mail-call")).status == "skipped"
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_retry_unknown_write_does_not_retry_repeatedly_without_new_approval(database, monkeypatch):
    stats = scenario(monkeypatch, writing=True)
    async def check():
        try:
            run, _ = await create()
            await complete(run.id)
            await RunService.resume(run.id, "user", {"mail-call": "retry"})
            result = await complete(run.id)
            assert result.status == "awaiting_input", result.error
            assert stats["email"] == 2
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_disconnect_does_not_cancel_task_and_pause_keeps_same_run(database, monkeypatch):
    async def check():
        gate = asyncio.Event()
        stats = scenario(monkeypatch, paper_gate=gate)
        try:
            run, _ = await create()
            for _ in range(200):
                if stats["paper"]:
                    break
                await asyncio.sleep(0.02)
            assert stats["paper"] == 1
            stream = RunService.events(run.id)
            assert "data:" in await anext(stream)
            await stream.aclose()
            assert (await RunDao.get(run.id)).status == "running"
            await RunService.control(run.id, "user", "pause")
            gate.set()
            assert (await complete(run.id)).status == "paused"
            await RunService.resume(run.id, "user", {})
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["paper"] == 1 and stats["weather"] == 1
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_specialist_selection_and_permission_checks_survive_task_api(database, monkeypatch):
    stats = scenario(monkeypatch)
    async def check():
        try:
            run, _ = await create(agent_id="papers")
            assert (await complete(run.id)).status == "succeeded"
            assert stats["config"]["id"] == "papers"
            assert stats["config"]["system_prompt"] == "论文检索策略"
            with pytest.raises(ValueError):
                await RunService.authorized(run.id, "other")
            with pytest.raises(ValueError):
                await RunService.resume(run.id, "other", {})
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_expired_lease_is_recoverable_and_old_worker_cannot_write(database):
    async def check():
        try:
            conversation = await ConversationDao.create_conversation("会话", "general", "Agent", "user")
            run = await RunDao.create(AgentRun(id="expired", conversation_id=conversation.conversation_id,
                active_conversation_id=conversation.conversation_id, user_id="user", status="running", owner="old-worker",
                lease_until=utcnow() - timedelta(seconds=1)))
            await RunDao.expire_leases()
            assert (await RunDao.get(run.id)).status == "interrupted"
            assert not await RunDao.update(run.id, owner="old-worker", answer="late answer")
            with pytest.raises(RunConflict):
                await RunDao.save_tool(run.id, "call", owner="old-worker", name="send_email")
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_tool_switch_filters_general_agent_and_blocks_resuming_disabled_tool(database, monkeypatch):
    from agentchat.database.dao.tool import ToolDao
    stats = scenario(monkeypatch, model_failure=True)
    async def check():
        try:
            run, _ = await create()
            assert (await complete(run.id)).status == "failed"
            await ToolDao.set_enabled("weather", False)
            config = await ConversationService.get_agent_config("general", "user")
            assert config["tool_ids"] == []
            with pytest.raises(ValueError, match="停用"):
                await RunService.resume(run.id, "user", {})
            await ToolDao.set_enabled("weather", True)
            await RunService.resume(run.id, "user", {})
            assert (await complete(run.id)).status == "succeeded"
            assert stats["weather"] == 1
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_http_task_api_replays_exports_and_checks_owner(database, monkeypatch):
    import json
    import httpx
    from fastapi import FastAPI
    from agentchat.api.v1.runs import router, get_login_user
    scenario(monkeypatch)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_login_user] = lambda: SimpleNamespace(user_id="user")
    async def check():
        try:
            conversation = await ConversationService.create_conversation("新会话", "general", "Agent", "user")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                request = {"conversation_id": conversation["conversation_id"], "user_input": "查询天气", "request_id": "http-request"}
                created = await client.post("/api/v1/runs", json=request)
                assert created.status_code == 200, created.text
                run_id = created.json()["data"]["id"]
                assert (await complete(run_id)).status == "succeeded"
                duplicate = await client.post("/api/v1/runs", json=request)
                assert duplicate.json()["data"]["id"] == run_id
                result = await client.get(f"/api/v1/runs/{run_id}")
                assert result.json()["data"]["answer"] == "报告完成"
                stream = await client.get(f"/api/v1/runs/{run_id}/events")
                assert stream.status_code == 200 and '"run_status"' in stream.text
                exported = await client.get(f"/api/v1/runs/{run_id}/export")
                records = [json.loads(line) for line in exported.text.splitlines()]
                assert records and all(row["run_id"] == run_id for row in records)
                cursor = max(row["sequence"] for row in records)
                replay = await client.get(f"/api/v1/runs/{run_id}/events?after={cursor}")
                assert '"response_chunk"' not in replay.text and '"run_status"' in replay.text
                active = await client.get("/api/v1/runs/active", params={"conversation_id": conversation["conversation_id"]})
                assert active.json()["data"] is None
                assert (await client.post(f"/api/v1/runs/{run_id}/resume", json={})).status_code == 409
                app.dependency_overrides[get_login_user] = lambda: SimpleNamespace(user_id="other")
                for suffix in ("", "/events", "/export"):
                    assert (await client.get(f"/api/v1/runs/{run_id}" + suffix)).status_code == 404
        finally:
            await cleanup(database)
    asyncio.run(check())
