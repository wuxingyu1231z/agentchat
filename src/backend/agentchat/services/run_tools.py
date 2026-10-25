"""可恢复工具调用：有界重试、结果复用和不确定写操作的人工处理。"""
import asyncio
from dataclasses import dataclass, field

from langchain_core.messages import ToolMessage
from langgraph.types import interrupt
from langgraph.errors import GraphBubbleUp

from agentchat.database.dao.agent_run import RunDao
from agentchat.utils.logging import redact
from agentchat.services.tool_arguments import prepare_tool_request, argument_error, MAX_ARGUMENT_FAILURES


def sandbox_replay(call):
    from agentchat.services.sandbox import SANDBOX_TOOLS
    return call.name in SANDBOX_TOOLS and call.arguments.get('_execution_policy') == 'sandbox-v1'

READ_ONLY_TOOLS = {
    "get_weather", "get_arxiv", "get_delivery_info", "web_search", "tavily_search", "bocha_search",
    "serply_search", "youcom_search", "youcom_research", "google_search", "image_to_text",
    "retrival_knowledge", "search_available_tools",
    "search_context", "read_evidence",
}


class RunCancelled(Exception):
    pass


class ToolOutcomeUnknown(Exception):
    pass


def transient(error):
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    return status in {408, 429, 500, 502, 503, 504} or isinstance(error, (TimeoutError, ConnectionError)) or type(error).__name__ in {
        "ConnectTimeout", "ReadTimeout", "ConnectionError", "ConnectError", "ReadError", "RemoteProtocolError",
    }


@dataclass
class RunContext:
    run_id: str
    owner: str
    decisions: dict = field(default_factory=dict)
    resume_reason: str = ""
    read_only_tools: set = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    context_manager: object = None

    def get_sandbox(self):
        from agentchat.services.sandbox import SandboxService
        return SandboxService(self)

    def get_context(self, model=None):
        if self.context_manager is None:
            from agentchat.services.context import ContextManager
            self.context_manager = ContextManager(self, model)
        elif model is not None:
            from agentchat.services.context import ContextPolicy
            self.context_manager.model = model
            self.context_manager.policy = ContextPolicy.for_model(model)
        return self.context_manager

    async def boundary(self):
        run = await self.active_run()
        if run.pause_requested or self.resume_reason == "paused":
            interrupt({"reason": "paused", "message": "任务已暂停，继续后从已保存的步骤执行"})
            self.resume_reason = ""

    async def active_run(self):
        run = await RunDao.get(self.run_id)
        if run is None or run.owner != self.owner or run.cancel_requested:
            raise RunCancelled()
        return run

    async def _unknown(self, request, name):
        call_id = request.tool_call["id"]
        decision = self.decisions.pop(call_id, None)
        if decision not in {"retry", "skip"}:
            # 让当前图步骤失败并持久化；恢复时从账本读取一次性的人工决定。
            # 避免在重放节点时重复消费旧 interrupt 值而再次发送外部操作。
            raise ToolOutcomeUnknown(f"{name} 的执行结果不确定，请核实后选择跳过或确认重试")
        return decision

    async def execute_tool(self, request, handler, writer, display_name):
        # 按调用串行执行，避免同一任务产生多个并行外部写入及重复恢复。
        async with self.lock:
            await self.boundary()
            name, call_id = request.tool_call["name"], request.tool_call["id"]
            from agentchat.services.sandbox import SANDBOX_TOOLS
            sandboxed = name in SANDBOX_TOOLS
            row = await RunDao.tool(self.run_id, call_id)
            read_only = name in self.read_only_tools
            if row and row.status in {"succeeded", "failed", "skipped", "invalid_arguments", "argument_limit"} and row.result:
                return await self.get_context().offload_tool(ToolMessage(**row.result))
            if row and row.status in {"running", "unknown"} and not read_only and not sandbox_replay(row):
                decision = await self._unknown(request, display_name)
                if decision == "skip":
                    result = ToolMessage(content="用户跳过了结果不确定的操作；不得将其表述为已成功。", name=name,
                                         tool_call_id=call_id, status="error")
                    await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status="skipped", result=result.model_dump(mode="json"))
                    return await self.get_context().offload_tool(result)
            calls = await RunDao.tools(self.run_id)
            if not row and len(calls) >= 20:
                return ToolMessage(content="本任务已达到工具调用上限，请总结已取得的结果。", name=name, tool_call_id=call_id, status="error")
            failures = sum(call.name == name and call.status == "invalid_arguments" for call in calls)
            prepared, corrections, errors = prepare_tool_request(request)
            exhausted = failures >= MAX_ARGUMENT_FAILURES
            if errors or exhausted:
                result = argument_error(request.tool_call, errors, exhausted=exhausted or bool(errors) and failures + 1 >= MAX_ARGUMENT_FAILURES)
                await RunDao.save_tool(self.run_id, call_id, owner=self.owner, name=name,
                    arguments=redact(request.tool_call.get("args", {})), read_only=read_only, attempts=0,
                    status="argument_limit" if exhausted else "invalid_arguments", result=result.model_dump(mode="json"))
                writer({"tool_call_id": call_id, "title": f"校验工具参数: {display_name}", "status": "ERROR",
                        "message": result.content})
                return result
            request = prepared
            if corrections:
                writer({"tool_call_id": call_id + ":arguments", "title": f"修正工具参数: {display_name}", "status": "END",
                        "message": "已按工具 schema 规范参数：" + "、".join(corrections)})
            attempts = row.attempts if row else 0
            safe_replay = read_only or sandboxed
            max_attempts = 3 if safe_replay else attempts + 1
            if attempts >= max_attempts:
                result = ToolMessage(content="此工具已达到重试次数上限，请选择其他工具或说明限制。", name=name, tool_call_id=call_id, status="error")
                await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status="failed", result=result.model_dump(mode="json"))
                return result
            while attempts < max_attempts:
                await self.boundary()
                attempts += 1
                recorded_arguments = redact(request.tool_call.get("args", {}))
                if sandboxed:
                    recorded_arguments = {**recorded_arguments, '_execution_policy': 'sandbox-v1'}
                await RunDao.save_tool(self.run_id, call_id, owner=self.owner, name=name, arguments=recorded_arguments,
                    read_only=read_only, status="running", attempts=attempts, error="")
                writer({"tool_call_id": call_id, "title": f"执行工具: {display_name}", "status": "START",
                        "message": f"正在执行，第 {attempts} 次尝试"})
                result_saved = False
                try:
                    async with asyncio.timeout(60 if read_only else 180):
                        if sandboxed:
                            import json
                            output = await self.get_sandbox().execute_tool(name, request.tool_call['args'], call_id)
                            result = ToolMessage(content=json.dumps(output, ensure_ascii=False), name=name, tool_call_id=call_id)
                        else:
                            result = await handler(request)
                    if not isinstance(result, ToolMessage):
                        # Command 等状态更新不是可安全重放的工具结果。
                        raise TypeError("持久化任务的工具必须返回 ToolMessage")
                    status = "failed" if result.status == "error" else "succeeded"
                    await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status=status, result=result.model_dump(mode="json"))
                    result_saved = True
                    writer({"tool_call_id": call_id, "title": f"执行工具: {display_name}",
                        "status": "ERROR" if status == "failed" else "END", "message": redact(str(result.content))[:12000]})
                    return await self.get_context().offload_tool(result)
                except GraphBubbleUp:
                    raise
                except asyncio.CancelledError:
                    if not result_saved:
                        await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status="pending" if safe_replay else "unknown",
                                               error="执行进程中断，结果尚未确认")
                    raise
                except Exception as error:
                    if result_saved:
                        # A projection/indexing failure after commit is not an uncertain
                        # external write and must never trigger the external action again.
                        raise
                    message = f"{type(error).__name__}: {redact(str(error))[:1000]}"
                    if not safe_replay:
                        await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status="unknown", error=message)
                        writer({"tool_call_id": call_id, "title": f"执行工具: {display_name}", "status": "ERROR",
                                "message": "操作结果不确定，已暂停，需核实后继续"})
                        decision = await self._unknown(request, display_name)
                        if decision == "retry":
                            max_attempts += 1
                            continue
                        result = ToolMessage(content="用户跳过了结果不确定的操作。", name=name, tool_call_id=call_id, status="error")
                        await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status="skipped", result=result.model_dump(mode="json"))
                        return result
                    if transient(error) and attempts < max_attempts:
                        writer({"tool_call_id": call_id, "title": f"执行工具: {display_name}", "status": "START",
                                "message": "服务暂时不可用，稍后自动重试"})
                        await asyncio.sleep(min(4, 0.5 * 2 ** (attempts - 1)))
                        continue
                    result = ToolMessage(content=f"工具调用失败：{message}。请修正参数、选择其他工具或说明当前限制。",
                                         name=name, tool_call_id=call_id, status="error")
                    await RunDao.save_tool(self.run_id, call_id, owner=self.owner, status="failed", error=message, result=result.model_dump(mode="json"))
                    writer({"tool_call_id": call_id, "title": f"执行工具: {display_name}", "status": "ERROR", "message": message})
                    return result
