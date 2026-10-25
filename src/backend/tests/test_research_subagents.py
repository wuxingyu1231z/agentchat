import asyncio
from copy import deepcopy

from tests.test_agent_runs import database, cleanup, complete
from tests.test_paper_research import scenario, create
from agentchat.core.agents.research_subagent import delegation_policy, next_question_index
from agentchat.database.dao.research import ResearchDao
from agentchat.database.dao.agent_run import RunDao
from agentchat.services.agent_runs import RunService


def plan():
    return {"questions": [
        {"query": "reinforcement learning policy", "independent": True, "depends_on": [], "workload": "substantial"},
        {"query": "benchmark datasets evaluation", "independent": True, "depends_on": [], "workload": "substantial"},
        {"query": "checkpoint runtime recovery", "independent": True, "depends_on": [], "workload": "substantial"}]}


def test_only_substantial_independent_distinct_work_is_delegated():
    normal = plan()
    decision = delegation_policy(normal)
    assert decision["mode"] == "parallel" and len(decision["question_indices"]) == decision["max_concurrency"] == 2
    small = deepcopy(normal)
    for question in small["questions"]:
        question["workload"] = "small"
    assert delegation_policy(small)["mode"] == "serial"
    dependent = deepcopy(normal)
    dependent["questions"][1]["depends_on"] = [0]
    dependent["questions"][2]["depends_on"] = [1]
    assert delegation_policy(dependent)["mode"] == "serial"
    duplicate = deepcopy(normal)
    for question in duplicate["questions"]:
        question["query"] = "same overlapping search"
    assert delegation_policy(duplicate)["mode"] == "serial"
    assert delegation_policy({"questions": normal["questions"][:2]})["mode"] == "serial"


def test_parent_respects_dependency_order():
    import pytest
    dependency = {"questions": [{"depends_on": [1]}, {"depends_on": []}, {"depends_on": [0]}]}
    assert next_question_index(dependency, []) == 1
    assert next_question_index(dependency, [1]) == 0
    assert next_question_index(dependency, [1, 0]) == 2
    with pytest.raises(ValueError, match="循环"):
        next_question_index({"questions": [{"depends_on": [1]}, {"depends_on": [0]}]}, [])


def test_parallel_agents_have_private_context_and_parent_verifies_report(database, monkeypatch):
    stats = scenario(monkeypatch, parallel=True)
    async def check():
        try:
            run = await create()
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            data = await ResearchDao.get(run.id)
            assert stats["max_parallel"] == 2 and stats["child_calls"] == 2, str([(row.get("status"), row.get("error")) for row in data.get("agents", {}).values()])
            assert stats["arxiv"] == 3 and stats["read"] == 1
            assert len({payload["assigned_question"]["question"] for payload in stats["child_payloads"]}) == 2
            assert all("conversation_context" not in payload and "questions" not in payload for payload in stats["child_payloads"])
            data = await ResearchDao.get(run.id)
            assert len(data["agents"]) == 2 and all(row["status"] == "completed" for row in data["agents"].values())
            assert all(row["parent_run_id"] == run.id for row in data["agents"].values())
            assert "r0:agent:0:assess" in data["steps"] and "r0:agent:1:assess" in data["steps"]
            assert data["view"]["delegation"]["mode"] == "parallel"
            assert "虚构的实验效果" not in result.answer and len(data["verified"]) == 4
            events = await RunDao.events(run.id)
            assert any(row.event["type"] == "research_agent" for row in events)
            public = await RunService.public(result)
            assert len(public["research"]["agents"]) == 2
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_pause_parallel_tasks_and_resume_without_repeating_completed_searches(database, monkeypatch):
    async def check():
        gate = asyncio.Event()
        stats = scenario(monkeypatch, parallel=True, search_gate=gate)
        try:
            run = await create()
            for _ in range(300):
                if stats["active_searches"] == 2:
                    break
                await asyncio.sleep(0.02)
            assert stats["active_searches"] == 2
            await RunService.control(run.id, "user", "pause")
            gate.set()
            paused = await complete(run.id)
            assert paused.status == "paused", paused.error
            data = await ResearchDao.get(run.id)
            assert all(row["status"] == "paused" for row in data["agents"].values())
            await RunService.resume(run.id, "user", {})
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert stats["arxiv"] == 3 and stats["child_calls"] == 2
            assert all(row["status"] == "completed" for row in (await ResearchDao.get(run.id))["agents"].values())
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_failed_child_is_taken_over_by_parent_without_repeating_network_work(database, monkeypatch):
    stats = scenario(monkeypatch, parallel=True, child_failure=True)
    async def check():
        try:
            run = await create()
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            data = await ResearchDao.get(run.id)
            assert sorted(row["status"] for row in data["agents"].values()) == ["completed", "failed"]
            assert "主 Agent 使用已有检索记录接管" in result.answer
            assert stats["arxiv"] == 3 and stats["child_calls"] == 2
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_simple_research_does_not_create_subagents_or_extra_model_calls(database, monkeypatch):
    stats = scenario(monkeypatch)
    async def check():
        try:
            run = await create()
            assert (await complete(run.id)).status == "succeeded"
            data = await ResearchDao.get(run.id)
            assert not data.get("agents") and stats["child_calls"] == 0
            assert data["view"]["delegation"]["mode"] == "serial"
        finally:
            await cleanup(database)
    asyncio.run(check())
