from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy.exc import IntegrityError
from sqlmodel import select, update

from agentchat.database.models.agent_run import AgentRun, RunEvent, RunToolCall, utcnow
from agentchat.database.models.conversation import ConversationTable
from agentchat.database.models.history import HistoryTable
from agentchat.database.session import async_session_getter
from agentchat.utils.conversation_title import fallback_conversation_title

TERMINAL = {"succeeded", "cancelled"}
RECOVERABLE = {"paused", "interrupted", "failed", "awaiting_input"}


class RunConflict(ValueError):
    pass


def history_id(run_id, role):
    return uuid5(NAMESPACE_URL, f"agentchat/run/{run_id}/{role}").hex


class RunDao:
    @staticmethod
    async def get(run_id):
        async with async_session_getter() as session:
            return await session.get(AgentRun, run_id)

    @staticmethod
    async def active(conversation_id):
        async with async_session_getter() as session:
            return (await session.exec(select(AgentRun).where(AgentRun.active_conversation_id == conversation_id))).first()

    @staticmethod
    async def latest(conversation_id, user_id):
        async with async_session_getter() as session:
            return (await session.exec(select(AgentRun).where(AgentRun.conversation_id == conversation_id,
                AgentRun.user_id == user_id).order_by(AgentRun.create_time.desc()).limit(1))).first()

    @staticmethod
    async def create(run):
        async with async_session_getter() as session:
            session.add(run)
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise RunConflict("当前会话已有未完成任务，请继续或结束该任务") from error
            await session.refresh(run)
            return run

    @staticmethod
    async def claim(run_id, owner):
        async with async_session_getter() as session:
            changed = await session.exec(update(AgentRun).where(
                AgentRun.id == run_id, AgentRun.status == "queued",
            ).values(status="running", owner=owner, lease_until=utcnow() + timedelta(seconds=60),
                     attempts=AgentRun.attempts + 1, update_time=utcnow()))
            await session.commit()
            return bool(changed.rowcount)

    @staticmethod
    async def update(run_id, *, owner=None, **values):
        async with async_session_getter() as session:
            statement = update(AgentRun).where(AgentRun.id == run_id)
            if owner is not None:
                statement = statement.where(AgentRun.owner == owner)
            result = await session.exec(statement.values(**values, update_time=utcnow()))
            await session.commit()
            return bool(result.rowcount)

    @staticmethod
    async def requeue(run_id):
        async with async_session_getter() as session:
            result = await session.exec(update(AgentRun).where(
                AgentRun.id == run_id, AgentRun.status.in_(RECOVERABLE),
            ).values(status="queued", owner=None, pause_requested=False, cancel_requested=False,
                     error="", lease_until=utcnow() + timedelta(seconds=60), update_time=utcnow()))
            await session.commit()
            if not result.rowcount:
                raise RunConflict("任务已在运行或已结束，请刷新状态")

    @staticmethod
    async def expire_leases():
        async with async_session_getter() as session:
            await session.exec(update(AgentRun).where(
                AgentRun.status.in_(["running", "queued"]),
                (AgentRun.lease_until < utcnow()) | (AgentRun.lease_until.is_(None)),
            ).values(status="interrupted", owner=None, lease_until=None,
                     error="执行进程已中断，可从已保存的步骤继续", update_time=utcnow()))
            await session.commit()

    @staticmethod
    async def append_event(run_id, event, *, answer=None, owner=None):
        async with async_session_getter() as session:
            statement = update(AgentRun).where(AgentRun.id == run_id)
            if owner is not None:
                statement = statement.where(AgentRun.owner == owner)
            values = {"update_time": utcnow()}
            if answer is not None:
                values["answer"] = answer
            changed = await session.exec(statement.values(**values))
            if not changed.rowcount:
                raise RunConflict("任务执行租约已失效")
            session.add(RunEvent(run_id=run_id, event=event))
            await session.commit()

    @staticmethod
    async def events(run_id, after=0, limit=200):
        async with async_session_getter() as session:
            return (await session.exec(select(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.id > after,
            ).order_by(RunEvent.id).limit(limit))).all()

    @staticmethod
    async def prepare(run_id, messages, *, memory_available=False):
        """原子保存模型输入、用户消息及回复占位，恢复时不重复插入。"""
        async with async_session_getter() as session:
            run = await session.get(AgentRun, run_id)
            if run.initial_messages:
                return run.initial_messages
            run.initial_messages = messages
            run.memory_available = memory_available
            conversation = await session.get(ConversationTable, run.conversation_id)
            if conversation is None:
                raise ValueError("会话不存在")
            previous = (await session.exec(select(HistoryTable.id).where(
                HistoryTable.conversation_id == run.conversation_id, HistoryTable.role == "user",
            ).limit(1))).first()
            if previous is None:
                conversation.name = fallback_conversation_title(run.request["user_input"])
            conversation.update_time = utcnow() + timedelta(hours=8)
            from agentchat.schemas.completion import CompletionReq
            from agentchat.utils.chat_attachments import attachment_events
            session.add(HistoryTable(id=history_id(run.id, "user"), role="user",
                conversation_id=run.conversation_id, content=run.request["user_input"],
                events=attachment_events(CompletionReq(**run.request)), create_time=conversation.update_time))
            identity = {"type": "agent", "data": {"agent_id": run.agent_config["id"],
                "name": run.agent_config["name"], "logo_url": run.agent_config.get("logo_url", "")}}
            session.add(HistoryTable(id=history_id(run.id, "assistant"), role="assistant",
                conversation_id=run.conversation_id, content="", events=[identity],
                create_time=conversation.update_time + timedelta(microseconds=1)))
            session.add(conversation)
            session.add(run)
            await session.commit()
            return messages

    @staticmethod
    async def finish(run_id, status, error="", *, owner=None):
        async with async_session_getter() as session:
            run = await session.get(AgentRun, run_id)
            if run is None or (owner is not None and run.owner != owner):
                return
            run.status, run.error = status, error
            run.lease_until = None
            run.owner = None
            run.update_time = utcnow()
            if status in TERMINAL:
                run.active_conversation_id = None
            history = await session.get(HistoryTable, history_id(run.id, "assistant"))
            if history:
                history.content = run.answer
                events = (await session.exec(select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id))).all()
                history.events = [entry.event for entry in events if entry.event.get("type") in {"agent", "event"}]
                session.add(history)
            session.add(run)
            session.add(RunEvent(run_id=run.id, event={"type": "run_status", "data": {"status": status, "message": error}}))
            await session.commit()

    @staticmethod
    async def tool(run_id, call_id):
        async with async_session_getter() as session:
            return await session.get(RunToolCall, f"{run_id}:{call_id}")

    @staticmethod
    async def save_tool(run_id, call_id, *, owner=None, **values):
        async with async_session_getter() as session:
            if owner is not None:
                changed = await session.exec(update(AgentRun).where(AgentRun.id == run_id, AgentRun.owner == owner)
                                             .values(update_time=utcnow()))
                if not changed.rowcount:
                    raise RunConflict("任务执行租约已失效")
            row = await session.get(RunToolCall, f"{run_id}:{call_id}")
            if row is None:
                row = RunToolCall(id=f"{run_id}:{call_id}", run_id=run_id, tool_call_id=call_id, **values)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            row.update_time = utcnow()
            session.add(row)
            await session.commit()

    @staticmethod
    async def tools(run_id):
        async with async_session_getter() as session:
            return (await session.exec(select(RunToolCall).where(RunToolCall.run_id == run_id))).all()
