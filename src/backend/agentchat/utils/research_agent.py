from agentchat.utils.default_agent import default_agent_config, DEFAULT_AGENT_ID

RESEARCH_AGENT_ID = "paper-research"
BUILTIN_AGENT_IDS = {DEFAULT_AGENT_ID, RESEARCH_AGENT_ID}


def research_agent_config(user_id):
    return {**default_agent_config(user_id), "id": RESEARCH_AGENT_ID, "name": "论文调研助手",
            "description": "拆解研究主题，检索与阅读论文，比较方法和实验，生成带原始来源的调研报告。",
            "is_builtin": True, "is_custom": False}
