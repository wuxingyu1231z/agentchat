import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from tests.test_agent_runs import database, cleanup, complete
from agentchat.api.services.conversation import ConversationService
from agentchat.database.dao.agent_run import RunDao
from agentchat.schemas.completion import CompletionReq
from agentchat.services.agent_runs import RunService
from agentchat.services.run_tools import RunContext
from agentchat.services.tool_arguments import prepare_tool_request


@pytest.fixture
def web_module(monkeypatch):
    # 单独载入工具，避免导入其他工具的存储、图片等运行依赖。
    path = Path(__file__).parents[1] / "agentchat/tools/web_search/tavily_search/action.py"
    spec = importlib.util.spec_from_file_location("isolated_web_search", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def search(self, **kwargs):
            calls.append(kwargs)
            return {"results": [{"url": "https://example.org/huaxi", "content": "华西医院的原始来源"}]}
    monkeypatch.setattr(module, "TavilyClient", Client)
    monkeypatch.setattr(module, "app_settings", SimpleNamespace(tools=SimpleNamespace(tavily={"api_key": "test-placeholder"})))
    return module, calls


@pytest.mark.parametrize("value", [None, "null", " NULL ", "None", "undefined", ""])
def test_optional_time_range_null_does_not_reach_sdk_as_string(web_module, value):
    module, calls = web_module
    result = module.tavily_search.invoke({"query": "四川大学华西医院", "topic": "general", "max_results": 8, "time_range": value})
    assert "华西医院" in result
    assert calls[0]["max_results"] == 8 and "time_range" not in calls[0]


def test_tool_defaults_and_invalid_time_range(web_module):
    module, calls = web_module
    module.tavily_search.invoke({"query": "华西医院"})
    assert calls[0]["topic"] == "general" and calls[0]["max_results"] == 5
    request = ToolCallRequest(tool_call={"name": "web_search", "args": {"query": "华西医院", "time_range": "quarter"}, "id": "bad", "type": "tool_call"},
                              tool=module.tavily_search, state={}, runtime=None)
    _, _, errors = prepare_tool_request(request)
    assert errors[0]["field"] == "time_range" and len(calls) == 1


def test_news_topic_omits_country_filter_and_preserves_valid_time_range(web_module):
    module, calls = web_module
    module.tavily_search.invoke({"query": "医院新闻", "topic": "news", "time_range": "week"})
    assert calls[0]["time_range"] == "week" and "country" not in calls[0]


def test_framework_does_not_erase_literal_null_in_optional_string():
    @tool(description="字符串标签")
    def label_tool(label: str | None = None):
        return label
    request = ToolCallRequest(tool_call={"name": "label_tool", "args": {"label": "null"}, "id": "literal", "type": "tool_call"}, tool=label_tool, state={}, runtime=None)
    repaired, corrections, errors = prepare_tool_request(request)
    assert repaired.tool_call["args"]["label"] == "null" and not corrections and not errors


def test_real_agent_corrects_validation_error_before_external_execution(database, monkeypatch, web_module):
    from langchain.agents import create_agent
    from agentchat.core.agents.general_agent import AgentConfig, GeneralAgent, EmitEventAgentMiddleware, StreamAgentState
    module, calls = web_module
    class Model(BaseChatModel):
        @property
        def _llm_type(self): return "argument-repair-test"
        def bind_tools(self, tools, **kwargs): return self
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if isinstance(messages[-1], HumanMessage):
                result = AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "四川大学华西医院", "time_range": "quarter"}, "id": "invalid"}])
            elif isinstance(messages[-1], ToolMessage) and messages[-1].status == "error":
                assert json.loads(messages[-1].content)["error"] == "invalid_tool_arguments"
                result = AIMessage(content="", tool_calls=[{"name": "web_search", "args": {"query": "四川大学华西医院", "topic": "general", "max_results": 8, "time_range": "null"}, "id": "corrected"}])
            else:
                result = AIMessage(content="已取得华西医院的来源")
            return ChatResult(generations=[ChatGeneration(message=result)])
    class Agent(GeneralAgent):
        async def init_agent(self):
            self.run_context.read_only_tools.add("web_search")
            self.react_agent = create_agent(Model(), [module.tavily_search], middleware=[EmitEventAgentMiddleware(lambda name:("工具", name), self.run_context)],
                state_schema=StreamAgentState, checkpointer=self.checkpointer)
    monkeypatch.setattr(RunService, "agent_factory", staticmethod(lambda config,saver,ctx: Agent(AgentConfig(**config),checkpointer=saver,run_context=ctx)))
    async def check():
        try:
            conversation = await ConversationService.create_conversation("参数纠错", "general", "Agent", "user")
            run = await RunService.create(CompletionReq(conversation_id=conversation["conversation_id"],user_input="查询华西医院"), "user", "repair-test")
            result = await complete(run.id)
            assert result.status == "succeeded", result.error
            assert len(calls) == 1 and "time_range" not in calls[0]
            bad = await RunDao.tool(run.id, "invalid")
            assert bad.status == "invalid_arguments" and bad.attempts == 0
            corrected = await RunDao.tool(run.id, "corrected")
            assert corrected.status == "succeeded" and corrected.arguments["time_range"] is None
            events = await RunDao.events(run.id)
            assert any("修正工具参数" in row.event.get("data", {}).get("title", "") for row in events)
        finally:
            await cleanup(database)
    asyncio.run(check())


def test_argument_repair_budget_blocks_repeated_bad_calls_and_write_body(database, web_module):
    module, calls = web_module
    async def check():
        try:
            conversation = await ConversationService.create_conversation("校验预算", "general", "Agent", "user")
            from agentchat.database.models.agent_run import AgentRun
            run = await RunDao.create(AgentRun(id="repair-budget", conversation_id=conversation["conversation_id"], user_id="user"))
            await RunDao.claim(run.id, "owner")
            context = RunContext(run.id, "owner")  # 未标记只读，模拟有副作用的工具同样先校验。
            async def handler(request):
                raise AssertionError("参数错误时不能进入工具执行体")
            for index in range(4):
                request = ToolCallRequest(tool_call={"name":"web_search","args":{"query":"医院","time_range":"quarter"},"id":str(index),"type":"tool_call"},
                    tool=module.tavily_search,state={},runtime=None)
                result = await context.execute_tool(request, handler, lambda event:None, "搜索")
                assert result.status == "error"
                if index >= 2:
                    assert json.loads(result.content)["error"] == "argument_correction_limit"
            rows = await RunDao.tools(run.id)
            assert all(row.attempts == 0 and row.status != "unknown" for row in rows) and not calls
        finally:
            await cleanup(database)
    asyncio.run(check())
