from datetime import datetime
from typing import List

from sqlmodel import Session, and_, delete, desc, or_, select, update

from agentchat.database.models.tool import ToolTable
from agentchat.database.models.agent_run import ToolPreference
from agentchat.database.session import async_session_getter, session_getter


class ToolDao:

    @staticmethod
    async def enabled_flags(tool_ids):
        if not tool_ids:
            return {}
        async with async_session_getter() as session:
            rows = (await session.exec(select(ToolPreference).where(ToolPreference.tool_id.in_(tool_ids)))).all()
            return {row.tool_id: row.enabled for row in rows}

    @staticmethod
    async def set_enabled(tool_id, enabled):
        async with async_session_getter() as session:
            row = await session.get(ToolPreference, tool_id)
            if row is None:
                row = ToolPreference(tool_id=tool_id)
            row.enabled = enabled
            session.add(row)
            await session.commit()

    @classmethod
    async def _get_tool(cls, zh_name: str, en_name: str,
                        user_id: str, description: str, logo_url: str):
        tool = ToolTable(zh_name=zh_name, en_name=en_name, logo_url=logo_url,
                         user_id=user_id, description=description)
        return tool

    @classmethod
    async def delete_user_defined_tool(cls, tool_id: str):
        async with async_session_getter() as session:
            statement = delete(ToolTable).where(ToolTable.tool_id == tool_id)
            await session.exec(statement)
            await session.commit()

    @classmethod
    async def update_tool_by_id(cls, tool_id: str, zh_name: str, en_name: str,
                                description: str, logo_url: str):
        with session_getter() as session:
            update_values = {}
            if zh_name:
                update_values['zh_name'] = zh_name
            if en_name:
                update_values['en_name'] = en_name
            if description:
                update_values['description'] = description
            if logo_url:
                update_values['logo_url'] = logo_url

            sql = update(ToolTable).where(ToolTable.tool_id == tool_id).values(**update_values)
            session.exec(sql)
            session.commit()

    @classmethod
    async def get_tool_by_user_id(cls, user_id: str):
        with session_getter() as session:
            sql = select(ToolTable).where(ToolTable.user_id == user_id)
            result = session.exec(sql).all()
            return result

    @classmethod
    async def get_tool_name_by_id(cls, tool_id: List[str]):
        with session_getter() as session:
            sql = select(ToolTable).where(ToolTable.tool_id.in_(tool_id))
            result = session.exec(sql).all()
            return result

    @classmethod
    async def get_all_tools(cls, user_id):
        async with async_session_getter() as session:
            statement = select(ToolTable).where(
                ToolTable.user_id == user_id
            )
            result = await session.exec(statement)
            return result

    @classmethod
    async def get_tool_by_id(cls, tool_id: str):
        with session_getter() as session:
            sql = select(ToolTable).where(ToolTable.tool_id == tool_id)
            tool = session.exec(sql).first()
            return tool

    @classmethod
    async def get_id_by_tool_name(cls, tool_name, user_id):
        with session_getter() as session:
            sql = select(ToolTable).where(and_(
                ToolTable.name == tool_name,
                ToolTable.user_id == user_id,
            ))
            tool = session.exec(sql).first()
            return tool

    @classmethod
    async def get_tool_ids_from_name(cls, tool_names: List[str], user_id):
        with session_getter() as session:
            statement = select(ToolTable).where(
                and_(
                    ToolTable.display_name.in_(tool_names),
                    ToolTable.user_id == user_id
                )
            )
            tools = session.exec(statement)
            return tools.all()

    @classmethod
    async def get_zh_name_from_en_name(cls, en_name):
        with session_getter() as session:
            sql = select(ToolTable).where(ToolTable.en_name == en_name)
            tool = session.exec(sql)
            return tool.first()

    @classmethod
    async def create_user_defined_tool(cls, tool: ToolTable):
        async with async_session_getter() as session:
            session.add(tool)
            await session.commit()
            await session.refresh(tool)
            return tool

    @classmethod
    async def create_default_tool(cls, tool):
        async with async_session_getter() as session:
            session.add(tool)
            await session.commit()
            await session.refresh(tool)
            return tool

    @classmethod
    async def get_user_defined_tools(cls, user_id):
        async with async_session_getter() as session:
            statement = select(ToolTable).where(
                ToolTable.user_id == user_id
            )
            result = await session.exec(statement)
            return result

    @classmethod
    async def update_user_defined_tool(cls, tool_id, update_values):
        async with async_session_getter() as session:
            statement = update(ToolTable).where(
                ToolTable.tool_id == tool_id
            ).values(**update_values)
            await session.exec(statement)
            await session.commit()
