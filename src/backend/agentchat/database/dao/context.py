"""所有上下文访问同时约束用户及会话；原件不可变，记忆采用追加版本。"""
import hashlib
import json
import re

from sqlalchemy import exists, or_, text, String, Float
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import select, delete

from agentchat.database.models.context import ContextArtifact, ContextChunk, ContextMemory
from agentchat.database.models.history import HistoryTable
from agentchat.database.models.conversation import ConversationTable
from agentchat.database.session import async_session_getter


def terms(query, limit=40):
    """English words plus CJK bigrams; FTS unicode61 alone does not segment Chinese."""
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", query.casefold())
    result = []
    for word in words:
        result.extend([word] if not re.search(r"[\u3400-\u9fff]", word) or len(word) < 3
                      else [word[i:i + 2] for i in range(len(word) - 1)])
    result = list(dict.fromkeys(result))
    return result[:limit] if limit is not None else result


def chunks(content, parts=None, size=2000):
    spans = parts or [{"start": 0, "end": len(content), "page": None}]
    ordinal = 0
    for span in spans:
        for start in range(span["start"], span["end"], max(1, size - min(200, size // 5))):
            end = min(start + size, span["end"])
            yield {"ordinal": ordinal, "start": start, "end": end, "page": span.get("page"), "content": content[start:end]}
            ordinal += 1
            if end == span["end"]:
                break


class ContextDao:
    @staticmethod
    async def _scope(session, user_id, conversation_id):
        conversation = await session.get(ConversationTable, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise ValueError("上下文不存在或没有权限访问")

    @staticmethod
    async def _fts(session):
        if session.bind.dialect.name != "sqlite":
            return False
        # FTS5 is optional in SQLite builds; LIKE fallback keeps other backends usable.
        try:
            await session.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS context_chunk_fts USING fts5(chunk_id UNINDEXED, content)"))
            return True
        except OperationalError:
            return False

    @classmethod
    async def put(cls, *, user_id, conversation_id, run_id, kind, key, title, content, details=None, parts=None):
        digest = hashlib.sha256(content.encode()).hexdigest()
        identity = hashlib.sha256(json.dumps([user_id, conversation_id, run_id, kind, key, digest], ensure_ascii=False).encode()).hexdigest()[:40]
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            row = await session.get(ContextArtifact, identity)
            if row:
                return row
            row = ContextArtifact(id=identity, user_id=user_id, conversation_id=conversation_id, run_id=run_id,
                kind=kind, logical_key=key, title=title[:300], sha256=digest, content=content, details=details or {})
            session.add(row)
            use_fts = await cls._fts(session)
            for chunk in chunks(content, parts):
                chunk_id = identity + ":" + str(chunk["ordinal"])
                session.add(ContextChunk(id=chunk_id, artifact_id=identity, **chunk))
                if use_fts:
                    await session.execute(text("INSERT INTO context_chunk_fts(chunk_id, content) VALUES (:id, :content)"),
                        {"id": chunk_id, "content": " ".join(terms(row.title + " " + encode_details(row.details) + " " + chunk["content"], limit=None)) + " " + chunk["content"]})
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                row = await session.get(ContextArtifact, identity)
                if row is None:
                    raise
            return row

    @classmethod
    async def get(cls, artifact_id, *, user_id, conversation_id):
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            row = await session.get(ContextArtifact, artifact_id)
            if row is None or row.user_id != user_id or row.conversation_id != conversation_id:
                raise ValueError("上下文不存在或没有权限访问")
            return row

    @classmethod
    async def find(cls, *, user_id, conversation_id, run_id, kind, key):
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            return (await session.exec(select(ContextArtifact).where(ContextArtifact.user_id == user_id,
                ContextArtifact.conversation_id == conversation_id, ContextArtifact.run_id == run_id,
                ContextArtifact.kind == kind, ContextArtifact.logical_key == key)
                .order_by(ContextArtifact.create_time.desc()).limit(1))).first()

    @classmethod
    async def search(cls, query, *, user_id, conversation_id, limit=5, kind=None, exclude_requests=False):
        tokens = terms(query)
        if not tokens:
            return []
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            statement = select(ContextChunk, ContextArtifact).join(ContextArtifact, ContextArtifact.id == ContextChunk.artifact_id).where(
                ContextArtifact.user_id == user_id, ContextArtifact.conversation_id == conversation_id)
            if kind:
                statement = statement.where(ContextArtifact.kind == kind)
            if exclude_requests:
                statement = statement.where(ContextArtifact.kind != "user_request")
            use_fts = await cls._fts(session)
            if use_fts:
                matches = text("SELECT chunk_id, rank AS relevance FROM context_chunk_fts WHERE context_chunk_fts MATCH :match")
                matches = matches.bindparams(match=" OR ".join('"' + token + '"' for token in tokens)).columns(
                    chunk_id=String, relevance=Float).subquery()
                statement = statement.join(matches, matches.c.chunk_id == ContextChunk.id).order_by(matches.c.relevance)
            else:
                statement = statement.where(or_(*(ContextChunk.content.icontains(token, autoescape=True) for token in tokens),
                                               *(ContextArtifact.title.icontains(token, autoescape=True) for token in tokens)))
            rows = (await session.exec(statement.order_by(ContextArtifact.create_time.desc()).limit(300))).all()
            ranked = rows if use_fts else sorted(rows, key=lambda row: sum(1 for token in tokens if token in row[0].content.casefold()), reverse=True)
            result, spans = [], []
            for chunk, artifact in ranked:
                if any(a == artifact.id and abs(s - chunk.start) < 1000 for a, s in spans):
                    continue
                # Put the matching span in view, not invariably the first lines of a chunk.
                first = min((chunk.content.casefold().find(token) for token in tokens if token in chunk.content.casefold()), default=0)
                local_start = max(0, first - 200)
                excerpt = chunk.content[local_start:local_start + 1000]
                result.append({"artifact_id": artifact.id, "chunk_id": chunk.id, "kind": artifact.kind,
                    "title": artifact.title, "run_id": artifact.run_id, "page": chunk.page,
                    "start": chunk.start + local_start, "end": chunk.start + local_start + len(excerpt),
                    "text": excerpt, "source_id": artifact.details.get("source_id"), "revision": artifact.details.get("revision"),
                    "sha256": artifact.sha256})
                spans.append((artifact.id, chunk.start))
                if len(result) >= min(10, max(1, limit)):
                    break
            return result

    @classmethod
    async def latest_memory(cls, *, user_id, conversation_id):
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            row = (await session.exec(select(ContextMemory).where(ContextMemory.user_id == user_id,
                ContextMemory.conversation_id == conversation_id).order_by(ContextMemory.version.desc()).limit(1))).first()
            return row

    @classmethod
    async def memory_for_run(cls, *, user_id, conversation_id, run_id):
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            return (await session.exec(select(ContextMemory).where(ContextMemory.user_id == user_id,
                ContextMemory.conversation_id == conversation_id, ContextMemory.run_id == run_id)
                .order_by(ContextMemory.version.desc()).limit(1))).first()

    @staticmethod
    async def clear(session, conversation_id):
        from agentchat.database.dao.sandbox import SandboxDao
        await SandboxDao.clear(session, conversation_id)
        from agentchat.database.dao.experience import ExperienceDao
        await ExperienceDao.clear(session, conversation_id)
        artifacts = select(ContextArtifact.id).where(ContextArtifact.conversation_id == conversation_id)
        if session.bind.dialect.name == "sqlite":
            table = (await session.execute(text("SELECT name FROM sqlite_master WHERE name='context_chunk_fts'"))).first()
            if table:
                await session.execute(text("DELETE FROM context_chunk_fts WHERE chunk_id IN "
                    "(SELECT context_chunk.id FROM context_chunk JOIN context_artifact ON context_artifact.id=context_chunk.artifact_id "
                    "WHERE context_artifact.conversation_id=:conversation)"), {"conversation": conversation_id})
        await session.exec(delete(ContextChunk).where(ContextChunk.artifact_id.in_(artifacts)))
        await session.exec(delete(ContextArtifact).where(ContextArtifact.conversation_id == conversation_id))
        await session.exec(delete(ContextMemory).where(ContextMemory.conversation_id == conversation_id))

    @classmethod
    async def save_memory(cls, *, user_id, conversation_id, run_id, key, data):
        identity = hashlib.sha256(f"{user_id}:{conversation_id}:{run_id}:{key}".encode()).hexdigest()[:40]
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            existing = await session.get(ContextMemory, identity)
            if existing:
                return existing
            latest = (await session.exec(select(ContextMemory.version).where(ContextMemory.conversation_id == conversation_id)
                .order_by(ContextMemory.version.desc()).limit(1))).first()
            row = ContextMemory(id=identity, user_id=user_id, conversation_id=conversation_id, run_id=run_id,
                                version=(latest or 0) + 1, data=data)
            session.add(row)
            await session.commit()
            return row

    @classmethod
    async def index_history(cls, *, user_id, conversation_id):
        # Backfill legacy history lazily, in bounded batches, without a vector service.
        while True:
            async with async_session_getter() as session:
                await cls._scope(session, user_id, conversation_id)
                rows = (await session.exec(select(HistoryTable).where(HistoryTable.conversation_id == conversation_id,
                    HistoryTable.content != "", ~exists(select(ContextArtifact.id).where(
                        ContextArtifact.conversation_id == conversation_id, ContextArtifact.kind == "history",
                        ContextArtifact.logical_key == HistoryTable.id)))
                    .order_by(HistoryTable.create_time, HistoryTable.id).limit(100))).all()
            if not rows:
                return
            for row in rows:
                await cls.put(user_id=user_id, conversation_id=conversation_id, run_id="history", kind="history",
                    key=row.id, title=f"{row.role} · {row.create_time}", content=row.content,
                    details={"history_id": row.id, "role": row.role, "time": str(row.create_time)})

    @classmethod
    async def manifest(cls, *, user_id, conversation_id, run_id):
        async with async_session_getter() as session:
            await cls._scope(session, user_id, conversation_id)
            # Do not load artifact bodies for diagnostics.
            return [dict(zip(("id", "kind", "title", "sha256", "details"), row)) for row in
                (await session.exec(select(ContextArtifact.id, ContextArtifact.kind, ContextArtifact.title,
                    ContextArtifact.sha256, ContextArtifact.details).where(ContextArtifact.user_id == user_id,
                    ContextArtifact.conversation_id == conversation_id, ContextArtifact.run_id == run_id))).all()]


def encode_details(details):
    return " ".join(str(details.get(key, "")) for key in ("source_id", "url", "role"))
