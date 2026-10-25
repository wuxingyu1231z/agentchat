"""Procedural experience only; user requirements and source evidence remain separate."""
from datetime import datetime
from sqlalchemy import Column, JSON
from sqlmodel import Field
from agentchat.database.models.base import SQLModelSerializable
from agentchat.database.models.agent_run import utcnow


class ExperienceEntry(SQLModelSerializable, table=True):
    __tablename__ = "context_experience"
    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    conversation_id: str = Field(index=True)
    rule_id: str
    version: str
    # Content is immutable. A template revision creates a new entry, never edits this one.
    content: dict = Field(sa_column=Column(JSON))
    status: str = "candidate"
    create_time: datetime = Field(default_factory=utcnow)


class ExperienceObservation(SQLModelSerializable, table=True):
    __tablename__ = "context_experience_observation"
    id: str = Field(primary_key=True)
    experience_id: str = Field(index=True)
    run_id: str = Field(index=True)
    evidence: dict = Field(sa_column=Column(JSON))


class ExperienceEvaluation(SQLModelSerializable, table=True):
    __tablename__ = "context_experience_evaluation"
    id: str = Field(primary_key=True)
    experience_id: str = Field(index=True)
    user_id: str
    conversation_id: str = Field(index=True)
    status: str = "running"
    report: dict = Field(default_factory=dict, sa_column=Column(JSON))
    create_time: datetime = Field(default_factory=utcnow)


class RunExperienceSnapshot(SQLModelSerializable, table=True):
    __tablename__ = "run_experience_snapshot"
    run_id: str = Field(primary_key=True)
    user_id: str
    conversation_id: str = Field(index=True)
    entries: list = Field(default_factory=list, sa_column=Column(JSON))
    create_time: datetime = Field(default_factory=utcnow)
