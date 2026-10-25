"""验证启动初始化只加载本地模板、可重复执行并补齐中断后的缺项。"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agentchat.settings import app_settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import Session, create_engine, select
from sqlmodel.ext.asyncio.session import AsyncSession

if not app_settings.database:
    app_settings.database = {
        "url": "sqlite:///:memory:",
    }

from agentchat.database import init_data
from agentchat.database.models.agent import AgentTable
from agentchat.database.models.agent_skill import AgentSkill
from agentchat.database.models.llm import LLMTable
from agentchat.database.models.mcp_server import MCPServerTable
from agentchat.database.models.tool import ToolTable


@pytest.fixture
def startup_database(tmp_path, monkeypatch):
    path = (tmp_path / "startup.db").as_posix()
    engine = create_engine("sqlite:///" + path)
    async_engine = create_async_engine("sqlite+aiosqlite:///" + path)

    @contextmanager
    def sessions():
        with Session(engine) as session:
            yield session

    @asynccontextmanager
    async def async_sessions():
        async with AsyncSession(async_engine) as session:
            yield session

    monkeypatch.setattr(init_data, "engine", engine)
    monkeypatch.setattr(init_data, "ensure_database", lambda: None)
    monkeypatch.setattr("agentchat.database.dao.conversation.async_session_getter", async_sessions)
    monkeypatch.setattr(
        init_data.app_settings,
        "multi_models",
        SimpleNamespace(chat_model=SimpleNamespace(
            model_name="qwen-plus", api_key="test-only", base_url="https://example.com/v1"
        )),
    )
    for module in ("agent", "agent_skill", "llm", "mcp_server", "tool"):
        module_path = "agentchat.database.dao." + module
        if module != "agent_skill":
            monkeypatch.setattr(module_path + ".session_getter", sessions)
        if module in ("agent_skill", "llm", "tool"):
            monkeypatch.setattr(module_path + ".async_session_getter", async_sessions)
    outbound = AsyncMock(side_effect=AssertionError("初始化不能发起外部请求"))
    monkeypatch.setattr("httpx.AsyncClient.send", outbound)
    yield engine, outbound
    asyncio.run(async_engine.dispose())
    engine.dispose()


def template_ids(engine):
    with Session(engine) as session:
        return [
            [getattr(row, key) for row in session.exec(select(model)).all()]
            for model, key in (
                (LLMTable, "llm_id"),
                (ToolTable, "tool_id"),
                (AgentSkill, "id"),
                (MCPServerTable, "mcp_server_id"),
                (AgentTable, "id"),
            )
        ]


def test_startup_is_local_repeatable_without_per_tool_agents(startup_database):
    engine, outbound = startup_database

    async def check():
        await init_data.init_agentchat_system()
        initial = template_ids(engine)
        assert all(initial[:4])
        assert initial[4] == []
        assert len(initial[3]) == 3
        configs = await init_data.load_json("mcp_server.json")
        with Session(engine) as session:
            servers = session.exec(select(MCPServerTable)).all()
            assert [(row.mcp_as_tool_name, row.description) for row in servers] == [
                (config["mcp_as_tool_name"], config["description"]) for config in configs
            ]
            for server, config in zip(servers, configs, strict=True):
                assert server.tools == config["tools"]
                assert server.params == config["params"]
                assert server.tools == [tool["name"] for tool in server.params]
                assert server.tools
        await init_data.init_agentchat_system()
        assert template_ids(engine) == initial
        assert template_ids(engine)[:4] == initial[:4]
        outbound.assert_not_called()

    asyncio.run(check())


def test_legacy_tool_agents_are_archived_but_specialists_and_history_survive(startup_database):
    from agentchat.database.models.agent_run import RetiredAgent
    from agentchat.database.models.conversation import ConversationTable
    from agentchat.database.models.history import HistoryTable
    engine, _ = startup_database

    async def check():
        await init_data.init_agentchat_system()
        with Session(engine) as session:
            tool = session.exec(select(ToolTable)).first()
            session.add(AgentTable(id="legacy", logo_url="", name=f"{tool.display_name}助手", tool_ids=[tool.tool_id], user_id="0", is_custom=False))
            session.add(AgentTable(id="papers", logo_url="", name="论文检索", user_id="0", is_custom=True, system_prompt="专用检索策略"))
            session.add(AgentTable(id="customized-model", logo_url="", name=f"{tool.display_name}助手", tool_ids=[tool.tool_id], user_id="0", is_custom=False, llm_id="custom-model"))
            session.add(ConversationTable(conversation_id="old-chat", name="旧会话", agent_id="legacy", user_id="user"))
            session.add(HistoryTable(role="assistant", conversation_id="old-chat", content="已有天气回复", events=[]))
            session.commit()
        await init_data.init_agentchat_system()
        await init_data.init_agentchat_system()
        with Session(engine) as session:
            assert session.get(AgentTable, "legacy") is None
            assert session.get(AgentTable, "papers") is not None
            assert session.get(AgentTable, "customized-model") is not None
            assert session.get(RetiredAgent, "legacy") is not None
            assert session.get(ConversationTable, "old-chat").agent_id == "general"
            reply = session.exec(select(HistoryTable)).first()
            assert reply.content == "已有天气回复"
            assert reply.events[0]["data"]["agent_id"] == "legacy"
    asyncio.run(check())


def test_invalid_mcp_metadata_stops_startup(startup_database, monkeypatch):
    engine, outbound = startup_database
    load_json = init_data.load_json

    async def missing_description(filename):
        configs = await load_json(filename)
        if filename == "mcp_server.json":
            configs[-1]["description"] = ""
        return configs

    monkeypatch.setattr(init_data, "load_json", missing_description)
    with pytest.raises(ValueError, match="description"):
        asyncio.run(init_data.init_agentchat_system())
    with Session(engine) as session:
        assert session.exec(select(MCPServerTable)).all() == []
    outbound.assert_not_called()
