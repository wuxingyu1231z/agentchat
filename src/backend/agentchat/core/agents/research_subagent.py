"""按需使用的只读检索子 Agent；独立上下文，不能再委派任务。"""
import re
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field


MAX_RESEARCH_SUBAGENTS = 2


class SubAgentPaused(Exception):
    pass


class SearchSubAgentSelection(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=6)
    gaps: list[str] = Field(default_factory=list, max_length=4)


class SearchSubAgentState(TypedDict, total=False):
    topic: str
    question: dict
    sources: list
    warnings: list
    recommended_ids: list
    gaps: list


def delegation_policy(plan):
    """至少三个子问题且有两项实质性独立工作，才支付子 Agent 的额外成本。"""
    questions = plan.get("questions", [])
    serial = {"mode": "serial", "question_indices": [], "max_concurrency": MAX_RESEARCH_SUBAGENTS}
    if len(questions) < 3:
        return {**serial, "reason": "子问题数量较少，由主 Agent 顺序检索，避免额外协调开销。"}
    chosen, token_sets = [], []
    for index, item in enumerate(questions):
        if item.get("independent") is not True or item.get("depends_on") or item.get("workload") != "substantial":
            continue
        tokens = set(re.findall(r"\w+", item.get("query", "").casefold()))
        if len(tokens) < 2 or any(len(tokens & other) / len(tokens | other) >= 0.8 for other in token_sets):
            continue
        chosen.append(index)
        token_sets.append(tokens)
        if len(chosen) == MAX_RESEARCH_SUBAGENTS:
            break
    if len(chosen) < 2:
        return {**serial, "reason": "独立工作量不足、存在前置依赖或检索范围高度重合，由主 Agent 处理。"}
    return {"mode": "parallel", "question_indices": chosen, "max_concurrency": MAX_RESEARCH_SUBAGENTS,
            "reason": "至少两个子问题无前置依赖、检索范围不同且需要多来源筛选，交给检索子 Agent 并行处理；主 Agent 统一核验。"}


def next_question_index(plan, completed):
    questions = plan["questions"]
    pending = [i for i in range(len(questions)) if i not in completed]
    if not pending:
        return len(questions)
    for index in pending:
        dependencies = questions[index].get("depends_on", [])
        if any(dependency < 0 or dependency >= len(questions) or dependency == index for dependency in dependencies):
            raise ValueError("研究计划包含无效前置依赖，请重新规划研究问题")
        if all(dependency in completed for dependency in dependencies):
            return index
    raise ValueError("研究问题存在循环依赖，请调整方向后重新规划")


class ResearchSearchSubAgent:
    """每个实例只看到分配的问题及其来源；小型状态图中的步骤由父任务账本持久化。"""
    def __init__(self, *, topic, question, fetch, assess, status):
        self.topic, self.question = topic, question
        self.fetch, self.assess, self.status = fetch, assess, status
        graph = StateGraph(SearchSubAgentState)
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("assess", self.evaluate)
        graph.add_edge(START, "retrieve")
        graph.add_edge("retrieve", "assess")
        graph.add_edge("assess", END)
        # 不在子图内动态中断；在步骤边界协作停止，父图统一生成可恢复的中断。
        self.graph = graph.compile(checkpointer=False)

    async def retrieve(self, state):
        await self.status("running", "retrieve")
        return await self.fetch()

    async def evaluate(self, state):
        await self.status("running", "assess", source_count=len(state["sources"]))
        result = await self.assess(state["sources"])
        allowed = {row["id"] for row in state["sources"]}
        return {"recommended_ids": [identifier for identifier in result["source_ids"] if identifier in allowed], "gaps": result["gaps"]}

    async def run(self):
        import asyncio
        from agentchat.services.run_tools import RunCancelled
        try:
            output = await self.graph.ainvoke({"topic": self.topic, "question": self.question},
                config={"recursion_limit": 6}, durability="async")
            await self.status("completed", "completed", source_count=len(output["sources"]))
            return output
        except SubAgentPaused:
            await self.status("paused", "waiting")
            raise
        except RunCancelled:
            await self.status("cancelled", "cancelled")
            raise
        except asyncio.CancelledError:
            await self.status("interrupted", "interrupted")
            raise
        except Exception as error:
            from agentchat.utils.logging import redact
            await self.status("failed", "failed", error_type=type(error).__name__, error=redact(str(error))[:500])
            raise
