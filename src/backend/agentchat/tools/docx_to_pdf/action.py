"""DOCX 转 PDF：仅经持久化任务的隔离沙箱执行。"""
from langchain_core.tools import tool
from agentchat.services.sandbox import CURRENT_RUN, SandboxUnavailable


@tool("docx_to_pdf")
async def convert_to_pdf(file_url: str) -> str:
    """在隔离沙箱中转换本次任务上传的文档，返回受权限保护的下载地址。"""
    context = CURRENT_RUN.get()
    if context is None:
        raise SandboxUnavailable("文档转换必须在持久化任务的沙箱中执行")
    import json
    result = await context.get_sandbox().execute_tool("docx_to_pdf", {"file_url": file_url}, "direct:docx_to_pdf")
    return json.dumps(result, ensure_ascii=False)


def _convert_to_pdf(file_url):
    raise SandboxUnavailable("宿主机文档转换已禁用，请使用 /runs 任务沙箱")
