import hashlib
import json
from datetime import timedelta
from uuid import uuid4
from sqlalchemy.exc import IntegrityError
from sqlmodel import select, delete, update
from agentchat.database.session import async_session_getter
from agentchat.database.dao.context import ContextDao
from agentchat.database.models.agent_run import utcnow
from agentchat.database.models.experience import (
    ExperienceEntry, ExperienceObservation, ExperienceEvaluation, RunExperienceSnapshot,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ExperienceDao:
    @staticmethod
    async def observe(run, rule_id, content, evidence):
        identity = digest([run.user_id, run.conversation_id, rule_id, content])[:40]
        observation_id = digest([run.id, identity])[:40]
        async with async_session_getter() as session:
            await ContextDao._scope(session, run.user_id, run.conversation_id)
            if not await session.get(ExperienceEntry, identity):
                session.add(ExperienceEntry(id=identity, user_id=run.user_id, conversation_id=run.conversation_id,
                    rule_id=rule_id, version=digest(content), content=content))
            if not await session.get(ExperienceObservation, observation_id):
                session.add(ExperienceObservation(id=observation_id, experience_id=identity, run_id=run.id, evidence=evidence))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not await session.get(ExperienceObservation, observation_id):
                    raise
        return identity

    @staticmethod
    async def entries(user_id, conversation_id):
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            return (await session.exec(select(ExperienceEntry).where(ExperienceEntry.user_id == user_id,
                ExperienceEntry.conversation_id == conversation_id).order_by(ExperienceEntry.id))).all()

    @staticmethod
    async def get(identity, user_id, conversation_id):
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            row = await session.get(ExperienceEntry, identity)
            if not row or row.user_id != user_id or row.conversation_id != conversation_id:
                raise ValueError("经验不存在或没有权限访问")
            return row

    @staticmethod
    async def snapshot(run):
        async with async_session_getter() as session:
            await ContextDao._scope(session, run.user_id, run.conversation_id)
            saved = await session.get(RunExperienceSnapshot, run.id)
            if saved:
                return saved.entries
            rows = (await session.exec(select(ExperienceEntry).where(ExperienceEntry.user_id == run.user_id,
                ExperienceEntry.conversation_id == run.conversation_id, ExperienceEntry.status == "active")
                .order_by(ExperienceEntry.id).limit(32))).all()
            entries = [{"id": row.id, "version": row.version, "rule_id": row.rule_id, **row.content} for row in rows]
            session.add(RunExperienceSnapshot(run_id=run.id, user_id=run.user_id,
                conversation_id=run.conversation_id, entries=entries))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                saved = await session.get(RunExperienceSnapshot, run.id)
                if not saved:
                    raise
                return saved.entries
            return entries

    @staticmethod
    async def begin(entry):
        async with async_session_getter() as session:
            await ContextDao._scope(session, entry.user_id, entry.conversation_id)
            stale = (await session.exec(select(ExperienceEvaluation).where(
                ExperienceEvaluation.experience_id == entry.id, ExperienceEvaluation.status == "running",
                ExperienceEvaluation.create_time < utcnow() - timedelta(minutes=5)))).all()
            if stale:
                for previous in stale:
                    previous.status, previous.report = "interrupted", {"reason": "评测进程失联或超时，未启用候选"}
                    session.add(previous)
                await session.exec(update(ExperienceEntry).where(ExperienceEntry.id == entry.id,
                    ExperienceEntry.status == "evaluating").values(status="candidate"))
            # CAS serializes evaluations and prevents an in-flight result from undoing retirement.
            changed = await session.exec(update(ExperienceEntry).where(ExperienceEntry.id == entry.id,
                ExperienceEntry.status.in_(["candidate", "validated"])).values(status="evaluating"))
            if not changed.rowcount:
                raise ValueError("经验已启用、停用或正在评测")
            row = ExperienceEvaluation(id=uuid4().hex, experience_id=entry.id, user_id=entry.user_id,
                conversation_id=entry.conversation_id)
            session.add(row)
            await session.commit()
            return row.id

    @staticmethod
    async def complete(evaluation_id, report, accepted):
        async with async_session_getter() as session:
            row = await session.get(ExperienceEvaluation, evaluation_id)
            if row is None or row.status != "running":
                raise ValueError("评测不存在或已经结束")
            await ContextDao._scope(session, row.user_id, row.conversation_id)
            row.report, row.status = report, "passed" if accepted else "rejected"
            session.add(row)
            changed = await session.exec(update(ExperienceEntry).where(ExperienceEntry.id == row.experience_id,
                ExperienceEntry.status == "evaluating").values(status="validated" if accepted else "candidate"))
            if accepted and changed.rowcount:
                entry = await session.get(ExperienceEntry, row.experience_id)
                await session.exec(update(ExperienceEntry).where(ExperienceEntry.user_id == row.user_id,
                    ExperienceEntry.conversation_id == row.conversation_id, ExperienceEntry.rule_id == entry.rule_id,
                    ExperienceEntry.id != entry.id, ExperienceEntry.status == "active").values(status="retired"))
                # Validation report and activation commit together.
                await session.exec(update(ExperienceEntry).where(ExperienceEntry.id == row.experience_id,
                    ExperienceEntry.status == "validated").values(status="active"))
            report["activated"] = bool(accepted and changed.rowcount)
            row.report = dict(report)
            session.add(row)
            await session.commit()

    @staticmethod
    async def retire(identity, user_id, conversation_id):
        await ExperienceDao.get(identity, user_id, conversation_id)
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            await session.exec(update(ExperienceEntry).where(ExperienceEntry.id == identity,
                ExperienceEntry.user_id == user_id, ExperienceEntry.conversation_id == conversation_id).values(status="retired"))
            await session.commit()

    @staticmethod
    async def diagnostics(user_id, conversation_id):
        entries = await ExperienceDao.entries(user_id, conversation_id)
        async with async_session_getter() as session:
            await ContextDao._scope(session, user_id, conversation_id)
            evaluations = (await session.exec(select(ExperienceEvaluation).where(
                ExperienceEvaluation.user_id == user_id, ExperienceEvaluation.conversation_id == conversation_id)
                .order_by(ExperienceEvaluation.create_time.desc()).limit(50))).all()
            observations = (await session.exec(select(ExperienceObservation).where(
                ExperienceObservation.experience_id.in_([row.id for row in entries])))).all()
            return {"entries": [row.model_dump(mode="json") for row in entries],
                "observations": [row.model_dump(mode="json") for row in observations],
                "evaluations": [row.model_dump(mode="json") for row in evaluations]}

    @staticmethod
    async def clear(session, conversation_id):
        ids = select(ExperienceEntry.id).where(ExperienceEntry.conversation_id == conversation_id)
        await session.exec(delete(ExperienceObservation).where(ExperienceObservation.experience_id.in_(ids)))
        for model in (ExperienceEvaluation, RunExperienceSnapshot, ExperienceEntry):
            await session.exec(delete(model).where(model.conversation_id == conversation_id))
