import hashlib
from sqlmodel import select, update, delete
from agentchat.database.models.agent_run import AgentRun, utcnow
from agentchat.database.models.sandbox import SandboxJob, SandboxDocument, SandboxUpload
from agentchat.database.dao.agent_run import RunConflict
from agentchat.database.dao.context import ContextDao
from agentchat.database.session import async_session_getter


def document_id(user_id, conversation_id, name):
    return hashlib.sha256(f"{user_id}:{conversation_id}:{name}".encode()).hexdigest()


class SandboxDao:
    @staticmethod
    async def register_upload(object_name, user_id, data):
        async with async_session_getter() as session:
            session.add(SandboxUpload(id=hashlib.sha256(object_name.encode()).hexdigest(),
                object_name=object_name, user_id=user_id, sha256=hashlib.sha256(data).hexdigest()))
            await session.commit()

    @staticmethod
    async def upload(object_name, user_id):
        async with async_session_getter() as session:
            row = await session.get(SandboxUpload, hashlib.sha256(object_name.encode()).hexdigest())
            if not row or row.user_id != user_id:
                raise ValueError('附件不是当前用户上传的文件，旧附件需重新上传')
            return row

    @staticmethod
    async def fence(session, run_id, owner):
        changed = await session.exec(update(AgentRun).where(AgentRun.id == run_id, AgentRun.owner == owner,
            AgentRun.status == "running", AgentRun.cancel_requested == False).values(update_time=utcnow()))
        if not changed.rowcount:
            raise RunConflict("沙箱执行租约已失效，不能发布结果")

    @staticmethod
    async def begin(context, identity, operation, policy):
        async with async_session_getter() as session:
            await SandboxDao.fence(session, context.run_id, context.owner)
            run = await session.get(AgentRun, context.run_id)
            row = await session.get(SandboxJob, identity)
            if row and row.status == "succeeded":
                await session.commit()
                return row.result
            if row and row.status == "running" and row.owner == context.owner:
                raise RunConflict("相同沙箱操作正在执行")
            if row:
                row.owner, row.status = context.owner, "running"
            else:
                row = SandboxJob(id=identity, run_id=run.id, user_id=run.user_id,
                    conversation_id=run.conversation_id, owner=context.owner, operation=operation, policy=policy)
            session.add(row)
            await session.commit()
            return None

    @staticmethod
    async def finish(context, identity, result, *, name=None, expected_sha=None):
        async with async_session_getter() as session:
            await SandboxDao.fence(session, context.run_id, context.owner)
            row = await session.get(SandboxJob, identity)
            if not row or row.owner != context.owner:
                raise RunConflict("沙箱执行租约已失效")
            if name:
                key = document_id(row.user_id, row.conversation_id, name)
                head = await session.get(SandboxDocument, key)
                if (head.sha256 if head else None) != expected_sha:
                    raise RunConflict("文档已存在或版本已变化，请读取最新版本后再修改")
                artifact = result["artifact"]
                if head:
                    head.sha256, head.artifact = artifact["sha256"], artifact
                else:
                    head = SandboxDocument(id=key, user_id=row.user_id, conversation_id=row.conversation_id,
                        name=name, sha256=artifact["sha256"], artifact=artifact)
                session.add(head)
            row.status, row.result = "succeeded", result
            session.add(row)
            await session.commit()

    @staticmethod
    async def fail(context, identity):
        async with async_session_getter() as session:
            await session.exec(update(SandboxJob).where(SandboxJob.id == identity, SandboxJob.owner == context.owner,
                SandboxJob.status == "running").values(status="interrupted"))
            await session.commit()

    @staticmethod
    async def document(user_id, conversation_id, name):
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            return await session.get(SandboxDocument, document_id(user_id, conversation_id, name))

    @staticmethod
    async def jobs(user_id, conversation_id):
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            return (await session.exec(select(SandboxJob).where(SandboxJob.user_id == user_id,
                SandboxJob.conversation_id == conversation_id).order_by(SandboxJob.create_time.desc()).limit(100))).all()

    @staticmethod
    async def job(identity, user_id, conversation_id):
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            row = await session.get(SandboxJob, identity)
            return row if row and row.user_id == user_id and row.conversation_id == conversation_id else None

    @staticmethod
    async def clear(session, conversation_id):
        for model in (SandboxDocument, SandboxJob):
            await session.exec(delete(model).where(model.conversation_id == conversation_id))
