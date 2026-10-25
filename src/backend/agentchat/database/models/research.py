"""论文调研工作区；节点结果、原始检索记录和方向修订独立于检查点保存。"""
from sqlalchemy import Column, JSON
from sqlmodel import Field
from agentchat.database.models.base import SQLModelSerializable


class ResearchWorkspace(SQLModelSerializable, table=True):
    __tablename__ = "research_workspace"
    run_id: str = Field(primary_key=True)
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
