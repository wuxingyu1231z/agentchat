"""真实 SQLite 检查点 + 专用图；模型和远端来源隔离替换，不使用用户密钥。"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tests.test_agent_runs import database, cleanup, complete

from agentchat.api.services.conversation import ConversationService
from agentchat.core.agents.paper_research_agent import PaperResearchAgent, ResearchPlan, SourceSelection, PaperComparison, Verification
from agentchat.database.dao.agent_run import RunDao, RunConflict
from agentchat.database.dao.history import HistoryDao
from agentchat.database.dao.research import ResearchDao
from agentchat.schemas.completion import CompletionReq
from agentchat.services.agent_runs import RunService
from agentchat.services.research_sources import checked_url, arxiv_items, crossref_item, parse_pdf, PublicResolver
from agentchat.utils.research_agent import RESEARCH_AGENT_ID

TEXT = "Our method uses checkpoint replay. The dataset is AgentBench. Accuracy improved by 5 percent. Evaluation is limited to simulated tasks."
SOURCE = {"id": "S123456789abc", "kind": "paper", "provider": "arxiv", "title": "Reliable Agent Research",
          "url": "https://arxiv.org/abs/2401.00001", "pdf_url": "https://arxiv.org/pdf/2401.00001",
          "arxiv_id": "2401.00001", "year": "2024", "authors": ["Test Author"], "abstract": TEXT,
          "raw_metadata": "original XML"}


def scenario(monkeypatch, *, fail_compare=False, gate=None, no_sources=False, parallel=False, search_gate=None, child_failure=False):
    parallel_ready = asyncio.Event()
    stats = {"arxiv": 0, "read": 0, "plan": 0, "compare": 0, "fail_compare": fail_compare, "gate": gate,
             "active_searches": 0, "max_parallel": 0, "child_calls": 0, "child_failure": child_failure, "child_payloads": []}

    class Model:
        def with_structured_output(self, schema, **kwargs):
            async def invoke(messages, **kwargs):
                payload = json.loads(messages[-1].content)
                if schema is ResearchPlan:
                    stats["plan"] += 1
                    if parallel:
                        return {"scope": payload["direction"], "questions": [
                            {"question": "训练方法", "query": "reinforcement policy optimization", "independent": True, "workload": "substantial"},
                            {"question": "评测数据集", "query": "benchmark evaluation datasets", "independent": True, "workload": "substantial"},
                            {"question": "复核恢复范围", "query": "checkpoint recovery limits", "independent": False, "workload": "small", "depends_on": [0, 1]}]}
                    return {"scope": payload["direction"], "questions": [{"question": "方法与训练策略", "query": "agent reinforcement learning"},
                                                                           {"question": "评测与局限性", "query": "agent evaluation benchmark"}]}
                if schema is SourceSelection:
                    return {"source_ids": [SOURCE["id"]], "rationale": "覆盖研究主题"}
                if schema is PaperComparison:
                    stats["compare"] += 1
                    if stats["fail_compare"]:
                        stats["fail_compare"] = False
                        raise RuntimeError("comparison process interrupted")
                    return {"claims": [
                        {"aspect": "method", "statement": "采用检查点重放方法", "quote": "Our method uses checkpoint replay.", "page": 1},
                        {"aspect": "datasets", "statement": "使用 AgentBench 数据集", "quote": "The dataset is AgentBench.", "page": 1},
                        {"aspect": "conclusions", "statement": "虚构的实验效果提升 99%", "quote": "This fabricated sentence is absent from the paper.", "page": 1},
                        {"aspect": "limitations", "statement": "评测仅包含模拟任务", "quote": "Evaluation is limited to simulated tasks.", "page": 1}], "missing": []}
                if schema is Verification:
                    return {"judgments": [{"claim_id": row["claim_id"], "supported": True, "reason": "原文直接支持"} for row in payload["claims"]]}
                from agentchat.core.agents.research_subagent import SearchSubAgentSelection
                if schema is SearchSubAgentSelection:
                    stats["child_calls"] += 1
                    stats["child_payloads"].append(payload)
                    if stats["child_failure"]:
                        stats["child_failure"] = False
                        raise RuntimeError("child model unavailable")
                    return {"source_ids": [SOURCE["id"]], "gaps": []}
                raise AssertionError(schema)
            return SimpleNamespace(ainvoke=invoke)

    class Sources:
        async def arxiv(self, query):
            stats["arxiv"] += 1
            stats["active_searches"] += 1
            stats["max_parallel"] = max(stats["max_parallel"], stats["active_searches"])
            try:
                if search_gate:
                    await search_gate.wait()
                elif parallel and stats["arxiv"] <= 2:
                    if stats["active_searches"] == 2:
                        parallel_ready.set()
                    # 用握手证明真正重叠，避免用短暂 sleep 依赖机器负载。
                    await asyncio.wait_for(parallel_ready.wait(), 3)
                second = {**SOURCE, "id": "S223456789abc", "title": "Another Reliable Agent Study", "arxiv_id": "2401.00002"}
                return {"sources": [] if no_sources else [SOURCE, second] if parallel else [SOURCE], "provenance": {"url": "https://export.arxiv.org/api/query", "retrieved_at": "2026-10-22", "sha256": "metadata-hash"}}
            finally:
                stats["active_searches"] -= 1
        async def crossref(self, query):
            return {"sources": []}
        async def web(self, query):
            return {"sources": [], "warning": "网页搜索未配置，继续学术检索"}
        async def read(self, source):
            stats["read"] += 1
            if gate:
                await gate.wait()
            return {"metadata_verified": True, "read_level": "pdf", "pages": [{"page": 1, "text": TEXT}],
                    "total_pages": 1, "warnings": [], "provenance": {"url": source["pdf_url"], "retrieved_at": "2026-10-22", "sha256": "pdf-hash"}}

    model, sources = Model(), Sources()
    monkeypatch.setattr(RunService, "agent_factory", staticmethod(lambda config, saver, context:
        PaperResearchAgent(config, checkpointer=saver, run_context=context, model=model, sources=sources)))
    return stats


async def create():
    conversation = await ConversationService.create_conversation("论文调研", RESEARCH_AGENT_ID, "Agent", "user")
    run = await RunService.create(CompletionReq(conversation_id=conversation["conversation_id"],
        agent_id=RESEARCH_AGENT_ID, user_input="研究工具调用失败与任务恢复"), "user", "paper-request")
    return run


def test_pipeline_keeps_raw_sources_and_rejects_fabricated_quote(database, monkeypatch):
    stats = scenario(monkeypatch)
    async def check():
        try:
            run = await create()
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert "采用检查点重放方法" in result.answer and "虚构的实验效果" not in result.answer
            assert "https://arxiv.org/abs/2401.00001" in result.answer
            data = await ResearchDao.get(run.id)
            assert data["view"]["verified_count"] == 3 and data["view"]["phase"] == "completed"
            assert data["steps"]["r0:search:0:arxiv"]["sources"][0]["raw_metadata"] == "original XML"
            assert data["steps"]["r0:read:" + SOURCE["id"]]["provenance"]["sha256"] == "pdf-hash"
            assert any(row["status"] == "unsupported" for row in data["verified"])
            assert stats["read"] == 1 and stats["arxiv"] == 2
            history = await HistoryDao.get_conversation_history(run.conversation_id)
            assert len(history) == 2 and history[-1].content == result.answer
            events = await RunDao.events(run.id)
            assert any(event.event["type"] == "research_progress" for event in events)
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_failed_comparison_resumes_without_repeating_search_or_pdf(database, monkeypatch):
    stats = scenario(monkeypatch, fail_compare=True)
    async def check():
        try:
            run = await create()
            assert (await complete(run.id)).status == "failed"
            await RunService.resume(run.id, "user", {})
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["arxiv"] == 2 and stats["read"] == 1 and stats["compare"] == 2
            assert len(await HistoryDao.get_conversation_history(run.conversation_id)) == 2
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_paused_research_can_change_direction_and_reuse_read_material(database, monkeypatch):
    async def check():
        gate = asyncio.Event()
        stats = scenario(monkeypatch, gate=gate)
        try:
            run = await create()
            for _ in range(300):
                if stats["read"]:
                    break
                await asyncio.sleep(0.02)
            assert stats["read"] == 1
            await RunService.control(run.id, "user", "pause")
            gate.set()
            assert (await complete(run.id)).status == "paused"
            with pytest.raises(ValueError):
                await RunService.resume(run.id, "other", {}, "其他研究")
            await RunService.resume(run.id, "user", {}, "聚焦多智能体强化学习的恢复评测")
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["plan"] == 2 and stats["arxiv"] == 4 and stats["read"] == 1
            assert "聚焦多智能体强化学习的恢复评测" in result.answer
            data = await ResearchDao.get(run.id)
            assert data["revision"] == 1 and data["directions"][0]["revision"] == 1
            assert "r0:plan" in data["steps"] and "r1:plan" in data["steps"]
            assert len(await HistoryDao.get_conversation_history(run.conversation_id)) == 2
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_no_sources_produces_honest_insufficient_evidence_report(database, monkeypatch):
    scenario(monkeypatch, no_sources=True)
    async def check():
        try:
            run = await create()
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert "没有取得可核实的关键结论" in result.answer
            assert "Reliable Agent Research" not in result.answer
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_pending_direction_revisions_do_not_reuse_old_version_after_startup_failure(database, monkeypatch):
    scenario(monkeypatch, fail_compare=True)
    async def check():
        try:
            run = await create()
            assert (await complete(run.id)).status == "failed"
            monkeypatch.setattr(RunService, "start", lambda *args: None)
            await RunService.resume(run.id, "user", {}, "方向一")
            await RunDao.update(run.id, status="failed")
            await RunService.resume(run.id, "user", {}, "方向二")
            data = await ResearchDao.get(run.id)
            assert data["pending_direction"] == {"direction": "方向二", "revision": 2}
            assert [row["revision"] for row in data["directions"]] == [1, 2]
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_research_exports_and_latest_api_are_private(database, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from agentchat.api.v1.runs import router, get_login_user
    scenario(monkeypatch)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_login_user] = lambda: SimpleNamespace(user_id="user")
    async def check():
        try:
            run = await create()
            assert (await complete(run.id)).status == "succeeded"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                root = f"/api/v1/runs/{run.id}/research"
                response = await client.get(root + "/export")
                assert response.status_code == 200, response.text
                assert "# 论文调研报告" in response.text
                data = await client.get(root + "/export?format=json")
                assert data.json()["steps"]["r0:search:0:arxiv"]["sources"][0]["raw_metadata"] == "original XML"
                latest = await client.get("/api/v1/runs/latest", params={"conversation_id": run.conversation_id})
                assert latest.json()["data"]["research"]["phase"] == "completed"
                app.dependency_overrides[get_login_user] = lambda: SimpleNamespace(user_id="other")
                assert (await client.get(root)).status_code == 404
                assert (await client.get(root + "/export?format=json")).status_code == 404
        finally:
            await cleanup(database)
    asyncio.run(check())


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1/x", "http://[::1]/", "http://10.0.0.1/", "https://localhost/", "https://host.internal/", "https://user:password@example.com/", "https://example.com:9000/"])
def test_source_reader_rejects_private_or_credential_urls(url):
    with pytest.raises(ValueError):
        checked_url(url)


def test_resolver_rejects_dns_to_private_network(monkeypatch):
    async def check():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "getaddrinfo", AsyncMock(return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]))
        with pytest.raises(ValueError, match="非公开"):
            await PublicResolver().resolve("public-looking.example", 443)
    asyncio.run(check())


def test_pdf_page_extraction_and_metadata_parsers():
    import fitz
    document = fitz.open()
    document.new_page().insert_text((60, 60), TEXT)
    result = parse_pdf(document.tobytes())
    document.close()
    assert result["pages"][0]["page"] == 1 and "checkpoint" in result["pages"][0]["text"]
    with pytest.raises(ValueError, match="PDF"):
        parse_pdf(b"<html>not a PDF</html>")
    body = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2401.00001v1</id><title>Test Paper</title><summary>Abstract</summary><published>2024-01-01</published><author><name>A</name></author></entry></feed>'''
    source = arxiv_items(body)[0]
    assert source["arxiv_id"] == "2401.00001v1" and source["authors"] == ["A"]
    row = crossref_item({"DOI": "10.1234/TEST", "title": ["Paper"], "published": {"date-parts": [[2024]]}})
    assert row["doi"] == "10.1234/test" and row["year"] == "2024"
    with pytest.raises(ValueError, match="实体"):
        arxiv_items(b'<!DOCTYPE foo><feed/>')


def test_pdf_from_wrong_paper_cannot_pass_source_identity(monkeypatch):
    import fitz
    from agentchat.services import research_sources
    document = fitz.open()
    document.new_page().insert_text((60, 60), "Unrelated Paper Title\n" + TEXT)
    body = document.tobytes()
    document.close()
    class FakeSandbox:
        async def run(self, operation, key, data):
            return {'parsed': parse_pdf(data), 'job_id': 'fake-parser-job'}
    sources = research_sources.ResearchSources(sandbox=FakeSandbox())
    monkeypatch.setattr(sources, "arxiv", AsyncMock(return_value={"sources": [SOURCE], "provenance": {"url": SOURCE["url"]}}))
    monkeypatch.setattr(research_sources, "fetch_public", AsyncMock(return_value=(body, {"url": SOURCE["pdf_url"]})))
    result = asyncio.run(sources.read(SOURCE))
    assert not result["metadata_verified"] and not result["pdf_identity_verified"]
    assert any("身份" in warning for warning in result["warnings"])
