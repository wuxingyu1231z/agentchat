import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse, Response, FileResponse
from pydantic import BaseModel, Field

from agentchat.api.responses.builder import resp_200
from agentchat.api.services.conversation import ConversationService
from agentchat.api.services.user import UserPayload, get_login_user
from agentchat.database.dao.agent_run import RunConflict, RunDao
from agentchat.schemas.completion import CompletionReq
from agentchat.services.agent_runs import RunService
from agentchat.utils.logging import redact

router = APIRouter(tags=["Tasks"])


class CreateRunRequest(CompletionReq):
    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=80)


class ResumeRunRequest(BaseModel):
    decisions: dict[str, Literal["retry", "skip"]] = Field(default_factory=dict)
    research_direction: str | None = Field(default=None, min_length=1, max_length=4000)


def api_error(error):
    return HTTPException(status_code=409 if isinstance(error, RunConflict) else 400, detail=str(error))


async def owned(run_id, user_id):
    try:
        return await RunService.authorized(run_id, user_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="任务不存在或没有权限访问") from error


@router.post("/runs")
async def create_run(req: CreateRunRequest, user: UserPayload = Depends(get_login_user)):
    try:
        run = await RunService.create(CompletionReq(**req.model_dump(exclude={"request_id"})), user.user_id, req.request_id)
        return resp_200(await RunService.public(run))
    except ValueError as error:
        raise api_error(error) from error


@router.get("/runs/active")
async def active_run(conversation_id: str, user: UserPayload = Depends(get_login_user)):
    try:
        await ConversationService.verify_user_permission(conversation_id, user.user_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="会话不存在或没有权限访问") from error
    await RunDao.expire_leases()
    run = await RunDao.active(conversation_id)
    return resp_200(await RunService.public(run) if run else None)


@router.get("/runs/latest")
async def latest_run(conversation_id: str, user: UserPayload = Depends(get_login_user)):
    try:
        await ConversationService.verify_user_permission(conversation_id, user.user_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="会话不存在或没有权限访问") from error
    run = await RunDao.latest(conversation_id, user.user_id)
    return resp_200(await RunService.public(run) if run else None)


@router.get("/runs/{run_id}")
async def get_run(run_id: str, user: UserPayload = Depends(get_login_user)):
    return resp_200(await RunService.public(await owned(run_id, user.user_id)))


@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRunRequest, user: UserPayload = Depends(get_login_user)):
    await owned(run_id, user.user_id)
    try:
        run = await RunService.resume(run_id, user.user_id, req.decisions, req.research_direction)
        return resp_200(await RunService.public(run))
    except ValueError as error:
        raise api_error(error) from error


@router.post("/runs/{run_id}/{action}")
async def control_run(run_id: str, action: Literal["pause", "cancel"], user: UserPayload = Depends(get_login_user)):
    await owned(run_id, user.user_id)
    return resp_200(await RunService.public(await RunService.control(run_id, user.user_id, action)))


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after: int = Query(0, ge=0), user: UserPayload = Depends(get_login_user)):
    await owned(run_id, user.user_id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的事件游标")
    return StreamingResponse(RunService.events(run_id, cursor), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, user: UserPayload = Depends(get_login_user)):
    await owned(run_id, user.user_id)
    async def records():
        cursor = 0
        while True:
            rows = await RunDao.events(run_id, cursor)
            for row in rows:
                cursor = row.id
                yield json.dumps(redact({"run_id": run_id, "sequence": row.id, **row.event}), ensure_ascii=False) + "\n"
            if len(rows) < 200:
                return
    return StreamingResponse(records(), media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="task-{run_id}.jsonl"'})


@router.get("/runs/{run_id}/research")
async def research_workspace(run_id: str, user: UserPayload = Depends(get_login_user)):
    run = await owned(run_id, user.user_id)
    from agentchat.utils.research_agent import RESEARCH_AGENT_ID
    if run.agent_config["id"] != RESEARCH_AGENT_ID:
        raise HTTPException(status_code=400, detail="该任务不是论文调研任务")
    from agentchat.database.dao.research import ResearchDao
    return resp_200(redact(await ResearchDao.get(run_id)))


@router.get("/runs/{run_id}/context")
async def context_diagnostics(run_id: str, user: UserPayload = Depends(get_login_user)):
    """用户可检查工作记忆及容量轨迹；不返回其他会话、密钥或隐藏推理。"""
    run = await owned(run_id, user.user_id)
    from agentchat.database.dao.context import ContextDao
    memory = await ContextDao.memory_for_run(user_id=run.user_id, conversation_id=run.conversation_id, run_id=run_id)
    traces, cursor = [], 0
    while True:
        rows = await RunDao.events(run_id, cursor)
        traces.extend(row.event["data"] for row in rows if row.event.get("type") == "context")
        if len(rows) < 200:
            break
        cursor = rows[-1].id
    return resp_200(redact({"memory": {"version": memory.version, **memory.data} if memory else None,
        "artifacts": await ContextDao.manifest(user_id=run.user_id, conversation_id=run.conversation_id, run_id=run_id),
        "traces": traces}))


@router.get("/runs/{run_id}/experiences")
async def experience_diagnostics(run_id: str, user: UserPayload = Depends(get_login_user)):
    run = await owned(run_id, user.user_id)
    from agentchat.database.dao.experience import ExperienceDao
    return resp_200(redact(await ExperienceDao.diagnostics(run.user_id, run.conversation_id)))


@router.get('/runs/{run_id}/sandbox')
async def sandbox_diagnostics(run_id: str, user: UserPayload = Depends(get_login_user)):
    run = await owned(run_id, user.user_id)
    from agentchat.database.dao.sandbox import SandboxDao
    return resp_200([row.model_dump(mode='json') for row in await SandboxDao.jobs(run.user_id, run.conversation_id)])


@router.get('/runs/{run_id}/sandbox/{job_id}/download')
async def sandbox_download(run_id: str, job_id: str, user: UserPayload = Depends(get_login_user)):
    run = await owned(run_id, user.user_id)
    from agentchat.database.dao.sandbox import SandboxDao
    from agentchat.services.sandbox import trusted_file, root_path, digest
    job = await SandboxDao.job(job_id, run.user_id, run.conversation_id)
    if not job or job.run_id != run.id or job.status != 'succeeded' or not job.result.get('artifact'):
        raise HTTPException(status_code=404, detail='产物不存在或没有权限访问')
    artifact = job.result['artifact']
    try:
        path = trusted_file(root_path() / artifact['relative_path'], root_path(), 24_000_000)
        if digest(path.read_bytes()) != artifact['sha256']:
            raise ValueError('产物校验失败')
        return FileResponse(path, filename=artifact['name'], media_type='application/octet-stream')
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=409, detail='产物缺失或完整性校验失败') from error


@router.post("/runs/{run_id}/experiences/{experience_id}/evaluate")
async def evaluate_experience(run_id: str, experience_id: str, user: UserPayload = Depends(get_login_user)):
    """Explicit batch evaluation: up to 10 real model calls; no client-supplied scores."""
    run = await owned(run_id, user.user_id)
    from agentchat.database.dao.experience import ExperienceDao
    from agentchat.services.experience_evaluation import evaluate
    from agentchat.core.models.manager import ModelManager
    from agentchat.utils.contexts import set_user_id_context, set_conversation_id_context, set_agent_name_context
    try:
        entry = await ExperienceDao.get(experience_id, run.user_id, run.conversation_id)
        set_user_id_context(run.user_id)
        set_conversation_id_context(run.conversation_id)
        set_agent_name_context("经验验证")
        return resp_200(await evaluate(entry, ModelManager.get_chat_model(max_retries=0, timeout=30)))
    except ValueError as error:
        raise api_error(error) from error


@router.post("/runs/{run_id}/experiences/{experience_id}/retire")
async def retire_experience(run_id: str, experience_id: str, user: UserPayload = Depends(get_login_user)):
    run = await owned(run_id, user.user_id)
    from agentchat.database.dao.experience import ExperienceDao
    try:
        await ExperienceDao.retire(experience_id, run.user_id, run.conversation_id)
        return resp_200({"status": "retired", "applies_to": "future_runs"})
    except ValueError as error:
        raise api_error(error) from error


@router.get("/runs/{run_id}/research/export")
async def export_research(run_id: str, format: Literal["markdown", "json"] = "markdown", user: UserPayload = Depends(get_login_user)):
    workspace = await research_workspace(run_id, user)
    data = workspace["data"] if isinstance(workspace, dict) else workspace.data
    if format == "json":
        content, extension, media = json.dumps(data, ensure_ascii=False, indent=2), "json", "application/json"
    else:
        content, extension, media = data.get("report", ""), "md", "text/markdown"
        if not content:
            raise HTTPException(status_code=409, detail="报告尚未生成，可先导出研究记录 JSON")
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="research-{run_id}.{extension}"'})
