"""PDF 转 DOCX：仅经持久化任务的隔离沙箱执行。"""
from langchain_core.tools import tool
from agentchat.services.sandbox import CURRENT_RUN, SandboxUnavailable


@tool("pdf_to_docx")
async def convert_to_docx(file_url: str) -> str:
    """在隔离沙箱中转换本次任务上传的文档，返回受权限保护的下载地址。"""
    context = CURRENT_RUN.get()
    if context is None:
        raise SandboxUnavailable("文档转换必须在持久化任务的沙箱中执行")
    import json
    result = await context.get_sandbox().execute_tool("pdf_to_docx", {"file_url": file_url}, "direct:pdf_to_docx")
    return json.dumps(result, ensure_ascii=False)


def _convert_to_docx(file_url):
    raise SandboxUnavailable("宿主机文档转换已禁用，请使用 /runs 任务沙箱")
