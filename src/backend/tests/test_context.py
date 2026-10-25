"""真实隔离 SQLite / FTS5，模拟模型；验证信息保留、容量、权限、版本和恢复。"""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from tests.test_agent_runs import database, cleanup
from agentchat.database.dao.agent_run import RunDao
from agentchat.database.dao.context import ContextDao
from agentchat.database.dao.conversation import ConversationDao
from agentchat.database.dao.history import HistoryDao
from agentchat.database.models.agent_run import AgentRun
from agentchat.services.context import ContextManager, ContextPolicy, ContextBudgetExceeded, evidence_chunks, message_tokens, tokens
from agentchat.services.run_tools import RunContext


class MemoryModel:
    profile = None
    model_name = "context-test"

    def __init__(self, edits=None):
        self.edits = edits or []
        self.summaries = 0
        self.extractions = 0

    def with_structured_output(self, schema, **kwargs):
        async def invoke(prompt, **kwargs):
            self.extractions += 1
            return {"edits": self.edits}
        return SimpleNamespace(ainvoke=invoke)

    async def ainvoke(self, prompt, **kwargs):
        self.summaries += 1
        return AIMessage(content="历史工具已经成功执行，尚需生成报告；精确信息请回读原件。")


async def task(name="run-1", request="继续研究", conversation=None, model=None, user="user"):
    conversation = conversation or await ConversationDao.create_conversation("上下文", "general", "Agent", user)
    run = await RunDao.create(AgentRun(id=name, conversation_id=conversation.conversation_id, user_id=user,
        request={"user_input": request}, agent_config={"id": "general"}))
    assert await RunDao.claim(run.id, "owner")
    context = RunContext(run.id, "owner")
    manager = context.get_context(model or MemoryModel())
    return run, conversation, manager


def test_mixed_language_budget_and_model_capacity(monkeypatch):
    assert tokens("中文" * 100) > tokens("ab" * 100)
    monkeypatch.setenv("AGENTCHAT_CONTEXT_TOKENS", "16000")
    monkeypatch.setenv("AGENTCHAT_MODEL_CONTEXT_LIMITS", '{"small":8000}')
    policy = ContextPolicy.for_model(SimpleNamespace(model_name="small", profile={"max_input_tokens": 6000}))
    assert policy.context_tokens == 6000
    assert policy.input_tokens + policy.output_tokens < 6000


def test_new_goal_replaces_old_goal_without_dropping_constraints():
    from agentchat.services.context import MemoryEdit
    data = {"entries": [{"id": "old-goal", "category": "goal", "text": "旧主题", "active": True},
                        {"id": "constraint", "category": "constraint", "text": "保留来源", "active": True}]}
    ContextManager.apply_edits(data, "研究新主题", "source", "new", [
        MemoryEdit(category="goal", text="新主题", quote="研究新主题")], "completed")
    assert not data["entries"][0]["active"]
    assert data["entries"][1]["active"]
    assert data["entries"][2]["active"]


def test_offload_is_readable_exact_and_idempotent_after_restart(database):
    async def check():
        try:
            run, conversation, manager = await task()
            original = "结果开始\n" + "unrelated content\n" * 2400 + "唯一实验结果：准确率 83.17%，数据集 X。\n"
            result = ToolMessage(content=original, name="web_search", tool_call_id="search-1")
            projected = await manager.offload_tool(result)
            payload = json.loads(projected.content)
            assert projected.tool_call_id == "search-1" and projected.status == "success"
            assert tokens(projected.content) < tokens(original)
            recovered = RunContext(run.id, "owner").get_context(MemoryModel())
            hits = await recovered.retrieve("唯一实验结果", 3)
            assert hits and any("83.17%" in hit["text"] for hit in hits)
            hit = next(hit for hit in hits if "83.17%" in hit["text"])
            text = await recovered.read(hit["artifact_id"], hit["start"], 1000)
            assert text["text"] == original[text["start"]:text["end"]]
            repeat = await recovered.offload_tool(result)
            assert json.loads(repeat.content)["artifact_id"] == payload["artifact_id"]
            assert (await ContextDao.get(payload["artifact_id"], user_id="user", conversation_id=conversation.conversation_id)).content == original
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_context_is_isolated_between_users_and_conversations(database):
    async def check():
        try:
            run, conversation, manager = await task()
            secret = await manager.put(kind="tool_result", key="private", title="私有资料", content="private_result_sentinel")
            _, _, another = await task("run-other", user="other")
            _, _, same_user = await task("run-same-user")
            for other in (another, same_user):
                assert await other.retrieve("private_result_sentinel") == []
                with pytest.raises(ValueError, match="权限"):
                    await other.read(secret.id)
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_memory_correction_preserves_old_version_and_source_quote(database):
    async def check():
        try:
            first = MemoryModel([{"category": "constraint", "text": "只研究 2024 年论文", "quote": "只研究2024年的论文"}])
            run, conversation, manager = await task(request="只研究2024年的论文", model=first)
            initial = await manager.ensure_memory()
            old_id = initial.data["entries"][0]["id"]
            await RunDao.finish(run.id, "succeeded")
            second = MemoryModel([{"category": "constraint", "text": "年份改为 2025 年", "quote": "改为2025年",
                                   "supersedes": [old_id]}])
            _, _, revised_manager = await task("run-2", "年份改为2025年，其他条件不变", conversation=conversation, model=second)
            revised = await revised_manager.ensure_memory()
            active = [row for row in revised.data["entries"] if row["active"]]
            assert len(active) == 1 and "2025" in active[0]["text"]
            assert initial.data["entries"][0]["active"] is True
            assert revised.version > initial.version
            again = await RunContext("run-2", "owner").get_context(second).ensure_memory()
            assert again.version == revised.version and second.extractions == 1
            assert "2024" not in (await revised_manager.memory_message()).content
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_memory_does_not_accept_fabricated_quote(database):
    async def check():
        try:
            model = MemoryModel([{"category": "constraint", "text": "编造要求", "quote": "用户从未说过"}])
            _, _, manager = await task(request="只研究真实实验", model=model)
            memory = await manager.ensure_memory()
            assert "编造要求" not in json.dumps(memory.data, ensure_ascii=False)
            assert memory.data["entries"][0]["text"] == "只研究真实实验"
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_compaction_retains_tool_groups_constraints_and_originals(database, monkeypatch):
    monkeypatch.setenv("AGENTCHAT_CONTEXT_TOKENS", "12000")
    async def check():
        try:
            model = MemoryModel([{"category": "constraint", "text": "排除纯综述论文", "quote": "排除纯综述论文"}])
            _, conversation, manager = await task(request="排除纯综述论文，继续比较", model=model)
            messages = [SystemMessage(content="系统规则")]
            for index in range(18):
                messages += [HumanMessage(content=f"早期问题 {index}"), AIMessage(content="旧资料 " * 700)]
            messages += [HumanMessage(content="排除纯综述论文，继续比较"),
                AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "RL"}, "id": "call-new"}]),
                ToolMessage(content="newest_result_sentinel", name="web_search", tool_call_id="call-new")]
            request = ModelRequest(model=model, messages=messages, tools=[])
            projected = await manager.model_request(request)
            assert message_tokens(projected.messages) < manager.policy.input_tokens
            assert "排除纯综述论文" in "\n".join(str(m.content) for m in projected.messages)
            assert projected.messages[-1].tool_call_id == "call-new"
            assert projected.messages[-2].tool_calls[0]["id"] == "call-new"
            assert request.messages == messages  # Checkpoint source was never mutated.
            assert len(projected.messages) < len(messages)
            traces = [event.event["data"] for event in await RunDao.events(manager.context.run_id)
                      if event.event.get("type") == "context" and event.event["data"]["phase"] == "assemble"]
            assert traces[-1]["estimated_after"] < traces[-1]["estimated_before"]
            assert traces[-1]["estimated_after"] <= traces[-1]["input_limit"]
            repeat = await manager.model_request(request)
            assert repeat.messages == projected.messages and model.summaries == 1
            hits = await manager.retrieve("早期问题")
            assert any(hit["kind"] == "conversation_trace" for hit in hits)
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_large_current_request_is_rejected_without_silent_truncation(database, monkeypatch):
    monkeypatch.setenv("AGENTCHAT_CONTEXT_TOKENS", "8000")
    async def check():
        try:
            _, _, manager = await task(request="中" * 20000)
            request = ModelRequest(model=manager.model, messages=[HumanMessage(content="中" * 20000)], tools=[])
            with pytest.raises(ContextBudgetExceeded, match="超过上下文容量"):
                await manager.model_request(request)
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_history_backfill_is_retrievable_even_outside_recent_window(database):
    async def check():
        try:
            _, conversation, manager = await task()
            for index in range(45):
                await HistoryDao.create_history(content="old_constraint_sentinel" if index == 0 else f"普通消息 {index}",
                    role="user" if index % 2 == 0 else "assistant", events=[], conversation_id=conversation.conversation_id)
            await ContextDao.index_history(user_id="user", conversation_id=conversation.conversation_id)
            recent = await HistoryDao.select_history_from_time(conversation.conversation_id, 40)
            assert not any("old_constraint_sentinel" in row.content for row in recent)
            hits = await manager.retrieve("old_constraint_sentinel")
            assert hits and hits[0]["kind"] == "history"
            await ContextDao.index_history(user_id="user", conversation_id=conversation.conversation_id)
            assert len(await manager.retrieve("old_constraint_sentinel")) == 1
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_evidence_selection_reaches_late_pages_without_changing_quotes():
    pages = [{"page": index, "text": "background filler " * 600} for index in range(1, 45)]
    pages[1]["text"] = "Our method uses reinforcement learning. " * 50
    pages[25]["text"] = "The dataset is AgentBench. " * 50
    pages[35]["text"] = "Experimental evaluation results: accuracy is 83.17%. " * 40
    pages[43]["text"] = "Limitations: experiments only cover simulated environments. " * 30
    selected = evidence_chunks(pages, "reinforcement learning", 4500)
    assert tokens(selected) <= 4500
    assert {2, 26, 36, 44}.issubset({item["page"] for item in selected})
    for item in selected:
        assert pages[item["page"] - 1]["text"][item["start"]:item["end"]] == item["text"]


def test_pdf_retains_pages_after_twenty():
    import fitz
    from agentchat.services.research_sources import parse_pdf
    document = fitz.open()
    for index in range(25):
        document.new_page().insert_text((60, 60), "Limitations late_page_sentinel" if index == 24 else "Background")
    parsed = parse_pdf(document.tobytes())
    document.close()
    assert len(parsed["pages"]) == 25 and not parsed["truncated"]
    assert "late_page_sentinel" in parsed["pages"][-1]["text"]


def test_page_lookup_returns_exact_source_and_rejects_missing_page(database):
    async def check():
        try:
            _, _, manager = await task()
            text = "first_page\nlast_page_experiment\n"
            parts = [{"page": 1, "start": 0, "end": 10}, {"page": 50, "start": 11, "end": len(text) - 1}]
            artifact = await manager.put(kind="paper_evidence", key="source", title="论文", content=text, parts=parts,
                details={"source_id": "S1", "revision": 3, "parts": parts})
            hits = await manager.retrieve("last_page_experiment")
            assert hits[0]["page"] == 50 and hits[0]["revision"] == 3
            read = await manager.read(artifact.id, page=50)
            assert read["text"] == "last_page_experiment" and read["details"]["source_id"] == "S1"
            with pytest.raises(ValueError, match="没有此页"):
                await manager.read(artifact.id, page=22)
        finally:
            await cleanup(database)
    asyncio.run(check())


@pytest.mark.parametrize("failure", ["model", "offload"])
def test_real_graph_restart_recovers_offloaded_result_without_repeating_tool_or_leaking_memory(database, monkeypatch, failure):
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from tests.test_agent_runs import create, complete
    from agentchat.core.agents.general_agent import GeneralAgent, AgentConfig, EmitEventAgentMiddleware, StreamAgentState
    from agentchat.services.agent_runs import RunService
    stats = {"external_calls": 0, "fail": failure == "model", "offload_fail": failure == "offload"}
    original_put = ContextManager.put

    async def flaky_put(self, **kwargs):
        if kwargs.get("kind") == "tool_result" and stats["offload_fail"]:
            stats["offload_fail"] = False
            raise RuntimeError("simulated context storage failure after the external result was committed")
        return await original_put(self, **kwargs)
    monkeypatch.setattr(ContextManager, "put", flaky_put)

    @tool(description="读取模拟的大型结果")
    async def big_tool():
        stats["external_calls"] += 1
        return "background unrelated " * 4000 + " exact_marker_sentinel accuracy=83.17%"

    class InternalModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "internal-memory-test"

        def _generate(self, messages, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="INTERNAL_MEMORY_SENTINEL"))])

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "recover-offloaded-test"

        def bind_tools(self, tools, **kwargs):
            return self

        def with_structured_output(self, schema, **kwargs):
            async def invoke(prompt, **kwargs):
                await InternalModel().ainvoke(prompt, config=kwargs.get("config"))
                return {"edits": []}
            return SimpleNamespace(ainvoke=invoke)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            last = messages[-1]
            if isinstance(last, HumanMessage):
                answer = AIMessage(content="", tool_calls=[{"name": "big_tool", "args": {}, "id": "big"}])
            elif last.name == "big_tool":
                assert "artifact_id" in last.content and len(last.content) < 10000
                if stats["fail"]:
                    stats["fail"] = False
                    raise RuntimeError("interrupted after saving the large result")
                answer = AIMessage(content="", tool_calls=[{"name": "search_context", "args": {"query": "exact_marker_sentinel"}, "id": "recall"}])
            elif last.name == "search_context":
                hits = json.loads(last.content)
                hit = next(row for row in hits if "accuracy=83.17%" in row["text"])
                answer = AIMessage(content="", tool_calls=[{"name": "read_evidence", "args": {"artifact_id": hit["artifact_id"], "start": hit["start"]}, "id": "read"}])
            else:
                assert "accuracy=83.17%" in json.loads(last.content)["text"]
                answer = AIMessage(content="报告完成：原文准确率 83.17%。")
            return ChatResult(generations=[ChatGeneration(message=answer)])

    class LocalAgent(GeneralAgent):
        async def init_agent(self):
            model = Model()
            manager = self.run_context.get_context(model)
            self.run_context.read_only_tools.update({"big_tool", "search_context", "read_evidence"})
            self.react_agent = create_agent(model, [big_tool, *manager.tools()],
                middleware=[EmitEventAgentMiddleware(lambda name: ("工具", name), self.run_context)],
                checkpointer=self.checkpointer, state_schema=StreamAgentState)

    monkeypatch.setattr(RunService, "agent_factory", staticmethod(lambda config, saver, context:
        LocalAgent(AgentConfig(**config), checkpointer=saver, run_context=context)))
    async def check():
        try:
            run, _ = await create()
            interrupted = await complete(run.id)
            assert interrupted.status == "failed" and stats["external_calls"] == 1
            assert (await RunDao.tool(run.id, "big")).status == "succeeded"
            await RunService.resume(run.id, "user", {})
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["external_calls"] == 1 and "83.17%" in result.answer
            events = await RunDao.events(run.id)
            assert "INTERNAL_MEMORY_SENTINEL" not in json.dumps([e.event for e in events], ensure_ascii=False)
            assert len((await RunDao.tool(run.id, "big")).result["content"]) > 50000
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_delete_conversation_removes_context_artifacts_and_fts(database):
    from sqlmodel import select
    from sqlalchemy import text
    from agentchat.database.models.context import ContextArtifact, ContextMemory
    import agentchat.database.dao.context as dao_module
    async def check():
        try:
            _, conversation, manager = await task()
            await manager.ensure_memory()
            await manager.put(kind="tool_result", key="deleted", title="删除", content="deleted_context_sentinel")
            await ConversationDao.delete_conversation_by_id(conversation.conversation_id)
            async with dao_module.async_session_getter() as session:
                assert not (await session.exec(select(ContextArtifact))).all()
                assert not (await session.exec(select(ContextMemory))).all()
                assert not (await session.execute(text("SELECT chunk_id FROM context_chunk_fts"))).all()
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_direction_revision_supersedes_old_constraint_and_is_idempotent(database):
    async def check():
        try:
            model = MemoryModel([{"category": "constraint", "text": "只纳入2024年论文", "quote": "只纳入2024年论文"}])
            _, _, manager = await task(request="只纳入2024年论文", model=model)
            initial = await manager.ensure_memory()
            old_id = initial.data["entries"][0]["id"]
            model.edits = [{"category": "constraint", "text": "改为2025年论文", "quote": "改为2025年论文", "supersedes": [old_id]}]
            revised = await manager.direction_memory("改为2025年论文", 1)
            active = [item for item in revised.data["entries"] if item["active"]]
            assert len(active) == 1 and active[0]["text"] == "改为2025年论文"
            assert revised.data["current_direction"]["revision"] == 1
            repeated = await RunContext(manager.context.run_id, "owner").get_context(model).direction_memory("改为2025年论文", 1)
            assert repeated.version == revised.version and model.extractions == 2
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_keyword_fallback_without_fts5(database, monkeypatch):
    async def no_fts(session):
        return False
    monkeypatch.setattr(ContextDao, "_fts", staticmethod(no_fts))
    async def check():
        try:
            _, _, manager = await task()
            original = await manager.put(kind="paper_evidence", key="fallback", title="任务恢复论文", content="fallback evidence xyz")
            assert (await manager.retrieve("fallback"))[0]["artifact_id"] == original.id
            assert (await manager.retrieve("任务恢复"))[0]["artifact_id"] == original.id
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_provider_overflow_retries_only_model_with_smaller_budget(database):
    from agentchat.core.agents.general_agent import EmitEventAgentMiddleware
    async def check():
        try:
            _, _, manager = await task()
            request = ModelRequest(model=manager.model, messages=[HumanMessage(content="继续研究")], tools=[])
            stats = []
            async def handler(projected):
                stats.append(projected)
                if len(stats) == 1:
                    raise ValueError("maximum context length exceeded")
                return "ok"
            middleware = EmitEventAgentMiddleware(lambda name: ("工具", name), manager.context)
            assert await middleware.awrap_model_call(request, handler) == "ok"
            assert len(stats) == 2
            traces = [event.event["data"] for event in await RunDao.events(manager.context.run_id)
                      if event.event.get("type") == "context" and event.event["data"]["phase"] == "assemble"]
            assert traces[-1]["emergency"] and traces[-1]["input_limit"] < traces[-2]["input_limit"]
            assert await RunDao.tools(manager.context.run_id) == []
        finally:
            await cleanup(database)
    asyncio.run(check())
