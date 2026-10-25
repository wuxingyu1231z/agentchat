from typing import Literal
from pydantic import BaseModel, Field, field_validator

from langchain.tools import tool
from tavily import TavilyClient

from agentchat.settings import app_settings


class WebSearchArgs(BaseModel):
    """网页搜索参数；可选时间范围默认不限制。"""
    query: str = Field(min_length=1, description="搜索关键词")
    topic: Literal["general", "news", "finance"] = "general"
    max_results: int = Field(default=5, ge=1, le=20)
    time_range: Literal["day", "week", "month", "year"] | None = Field(default=None, description="时间范围；不限制时省略或使用 JSON null，不能传字符串 null")

    @field_validator("time_range", mode="before")
    @classmethod
    def normalize_time_range(cls, value):
        if isinstance(value, str):
            value = value.strip().casefold()
            return None if value in {"", "null", "none", "undefined"} else value
        return value


@tool("web_search", args_schema=WebSearchArgs, description="根据关键词联网搜索。可选时间范围支持 day、week、month、year；不限制时省略或传 JSON null。")
def tavily_search(query: str,
                  topic: str = "general",
                  max_results: int = 5,
                  time_range: str | None = None):
    """
    根据用户的问题以及查询参数进行联网搜索

    Args:
        query: 用户想要搜索的问题
        topic: 搜索主题领域，general为通用，news为新闻，finance为财经
        max_results: 最大返回结果数量，控制结果数量上限
        time_range: 时间范围，支持 day、week、month、year；不限制时省略或用 JSON null

    Returns:
        将联网搜索到的信息返回给用户
    """
    return _tavily_search(query, topic, max_results, time_range)

def _tavily_search(query, topic, max_results, time_range):
    """使用Tavily搜索工具给用户进行搜索"""
    api_key = app_settings.tools.tavily.get("api_key")
    if not api_key:
        raise ValueError("当前未配置 Tavily 搜索服务")
    tavily_client = TavilyClient(api_key=api_key)
    options = {"query": query, "topic": topic, "max_results": max_results}
    if topic == "general":
        options["country"] = "china"
    if time_range is not None:
        options["time_range"] = time_range
    response = tavily_client.search(**options)

    return "\n\n".join([f'网址:{result["url"]}, 内容: {result["content"]}' for result in response["results"]])
