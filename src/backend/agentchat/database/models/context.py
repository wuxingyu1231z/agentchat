"""可回读的上下文原件、检索片段及带版本的工作记忆。只新增表。"""
from sqlalchemy import Column, JSON, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlmodel import Field

from agentchat.database.models.base import SQLModelSerializable
from agentchat.database.models.agent_run import utcnow
from datetime import datetime


class ContextArtifact(SQLModelSerializable, table=True):
    __tablename__ = "context_artifact"
    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    run_id: str = Field(index=True)
    kind: str = Field(index=True)
    logical_key: str = Field(index=True)
    title: str
    sha256: str
    content: str = Field(sa_column=Column(Text().with_variant(LONGTEXT(), "mysql")))
    details: dict = Field(default_factory=dict, sa_column=Column(JSON))
    create_time: datetime = Field(default_factory=utcnow)


class ContextChunk(SQLModelSerializable, table=True):
    __tablename__ = "context_chunk"
    id: str = Field(primary_key=True)
    artifact_id: str = Field(index=True)
    ordinal: int
    # Offset in the immutable original; never an offset in a generated summary.
    start: int
    end: int
    page: int | None = None
    content: str = Field(sa_column=Column(Text))


class ContextMemory(SQLModelSerializable, table=True):
    __tablename__ = "context_memory"
    __table_args__ = (UniqueConstraint("conversation_id", "version"),)
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    user_id: str = Field(index=True)
    run_id: str = Field(index=True)
    version: int
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    create_time: datetime = Field(default_factory=utcnow)
