"""任务、执行事件和工具调用账本；检查点由 LangGraph 独立管理。"""
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, JSON, Text
from sqlmodel import Field

from agentchat.database.models.base import SQLModelSerializable


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentRun(SQLModelSerializable, table=True):
    __tablename__ = "agent_run"
    id: str = Field(default_factory=lambda: uuid4().hex, primary_key=True)
    conversation_id: str = Field(index=True)
    # NULL releases the conversation when a task finishes or is cancelled.
    active_conversation_id: Optional[str] = Field(default=None, unique=True)
    user_id: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    request: dict = Field(default_factory=dict, sa_column=Column(JSON))
    agent_config: dict = Field(default_factory=dict, sa_column=Column(JSON))
    initial_messages: list = Field(default_factory=list, sa_column=Column(JSON))
    memory_available: bool = False
    answer: str = Field(default="", sa_column=Column(Text))
    error: str = Field(default="", sa_column=Column(Text))
    pause_requested: bool = False
    cancel_requested: bool = False
    owner: Optional[str] = None
    lease_until: Optional[datetime] = None
    attempts: int = 0
    create_time: datetime = Field(default_factory=utcnow)
    update_time: datetime = Field(default_factory=utcnow)


class RunEvent(SQLModelSerializable, table=True):
    __tablename__ = "agent_run_event"
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    event: dict = Field(sa_column=Column(JSON))


class RunToolCall(SQLModelSerializable, table=True):
    __tablename__ = "agent_run_tool_call"
    id: str = Field(primary_key=True)
    run_id: str = Field(index=True)
    tool_call_id: str
    name: str
    arguments: dict = Field(default_factory=dict, sa_column=Column(JSON))
    read_only: bool = False
    status: str = "pending"
    attempts: int = 0
    result: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    error: str = Field(default="", sa_column=Column(Text))
    update_time: datetime = Field(default_factory=utcnow)


class RetiredAgent(SQLModelSerializable, table=True):
    __tablename__ = "retired_agent"
    id: str = Field(primary_key=True)
    user_id: str
    config: dict = Field(sa_column=Column(JSON))
    create_time: datetime = Field(default_factory=utcnow)


class ToolPreference(SQLModelSerializable, table=True):
    __tablename__ = "tool_preference"
    tool_id: str = Field(primary_key=True)
    enabled: bool = True
