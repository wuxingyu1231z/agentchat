from copy import deepcopy
from datetime import timedelta
from sqlmodel import update
from agentchat.database.models.agent_run import AgentRun, utcnow
from agentchat.database.models.research import ResearchWorkspace
from agentchat.database.session import async_session_getter
from agentchat.database.dao.agent_run import RunConflict, RECOVERABLE


class ResearchDao:
    @staticmethod
    async def get(run_id):
        async with async_session_getter() as session:
            row = await session.get(ResearchWorkspace, run_id)
            return deepcopy(row.data) if row else {}

    @staticmethod
    async def save(run_id, *, owner, step=None, result=None, agent_id=None, agent=None, **values):
        async with async_session_getter() as session:
            changed = await session.exec(update(AgentRun).where(AgentRun.id == run_id, AgentRun.owner == owner)
                                         .values(update_time=utcnow()))
            if not changed.rowcount:
                raise RunConflict("任务执行租约已失效")
            row = await session.get(ResearchWorkspace, run_id)
            if row is None:
                row = ResearchWorkspace(run_id=run_id)
            data = deepcopy(row.data)
            if step:
                data.setdefault("steps", {})[step] = result
            if agent_id:
                previous = data.setdefault("agents", {}).get(agent_id, {})
                data["agents"][agent_id] = {**previous, **(agent or {})}
            data.update(values)
            row.data = data
            session.add(row)
            await session.commit()

    @staticmethod
    async def requeue_direction(run_id, direction):
        """与恢复状态原子提交，两个继续请求不能覆盖彼此的方向。"""
        async with async_session_getter() as session:
            changed = await session.exec(update(AgentRun).where(AgentRun.id == run_id, AgentRun.status.in_(RECOVERABLE))
                .values(status="queued", owner=None, pause_requested=False, cancel_requested=False, error="",
                        lease_until=utcnow() + timedelta(seconds=60), update_time=utcnow()))
            if not changed.rowcount:
                raise RunConflict("任务已在运行或已结束，请刷新状态")
            row = await session.get(ResearchWorkspace, run_id)
            if row is None:
                row = ResearchWorkspace(run_id=run_id)
            data = deepcopy(row.data)
            revision = max(int(data.get("revision", 0)), int((data.get("pending_direction") or {}).get("revision", 0))) + 1
            if revision > 10:
                raise ValueError("本任务最多调整 10 次研究方向，请另建任务")
            data["pending_direction"] = {"direction": direction, "revision": revision}
            data.setdefault("directions", []).append({"direction": direction, "revision": revision, "time": utcnow().isoformat()})
            row.data = data
            session.add(row)
            await session.commit()
