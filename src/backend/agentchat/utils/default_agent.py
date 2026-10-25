"""无需创建数据库智能体即可使用的通用对话配置。"""

DEFAULT_AGENT_ID = "general"


def default_agent_config(user_id: str) -> dict:
    return {
        "id": DEFAULT_AGENT_ID,
        "name": "通用助手",
        "description": "直接提问，使用默认模型进行对话。",
        "logo_url": "",
        "user_id": user_id,
        "llm_id": "",
        "system_prompt": "",
        "enable_memory": False,
        "tool_ids": [],
        "mcp_ids": [],
        "knowledge_ids": [],
        "agent_skill_ids": [],
    }
