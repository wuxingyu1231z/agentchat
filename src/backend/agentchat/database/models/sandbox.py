from datetime import datetime
from sqlalchemy import Column, JSON
from sqlmodel import Field
from agentchat.database.models.base import SQLModelSerializable
from agentchat.database.models.agent_run import utcnow


class SandboxJob(SQLModelSerializable, table=True):
    __tablename__ = "sandbox_job"
    id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    user_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    owner: str
    operation: str
    policy: dict = Field(sa_column=Column(JSON))
    status: str = "running"
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    create_time: datetime = Field(default_factory=utcnow)


class SandboxDocument(SQLModelSerializable, table=True):
    __tablename__ = "sandbox_document"
    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    name: str
    sha256: str
    artifact: dict = Field(sa_column=Column(JSON))


class SandboxUpload(SQLModelSerializable, table=True):
    __tablename__ = 'sandbox_upload'
    id: str = Field(primary_key=True)
    object_name: str
    user_id: str = Field(index=True)
    sha256: str
