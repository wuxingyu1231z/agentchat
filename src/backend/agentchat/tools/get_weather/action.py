import requests
from loguru import logger
from langchain.tools import tool

from agentchat.settings import app_settings
from agentchat.prompts.tool import WEATHER_PROMPT, MESSAGE_PROMPT


@tool(parse_docstring=True)
def get_weather(city: str):
    """
    查询用户提供城市的天气情况。

    Args:
        city (str): 用户提供的城市名称。

    Returns:
        str: 城市的天气信息。
    """
    return _get_weather(city)

def _get_weather(location: str):
    """帮助用户想要查询的天气"""
    if not app_settings.tools.weather.get('api_key'):
        raise ValueError('天气工具尚未配置服务密钥，请配置 weather.api_key')
    params = {
        'key': app_settings.tools.weather.get('api_key'),
        'city': location,
        'extensions': 'all'
    }

    try:
        res = requests.get(url=app_settings.tools.weather.get('endpoint'), params=params, timeout=5)  # 预报天气
        res.raise_for_status()
        result = res.json()
        if not result.get('forecasts'):
            raise ValueError(f"天气查询失败：{result.get('info', '城市未匹配或服务配置无效')}")
        city = result.get('forecasts')[0].get("city")  # 获取城市
        message_result = []
        data = result.get('forecasts')[0].get("casts")

        for item in data:
            date = item.get('date')  # 获取日期
            day_temp = item.get('daytemp')  # 白天温度
            night_temp = item.get('nighttemp')  # 晚上温度
            day_weather = item.get('dayweather')  # 白天天气现象
            night_weather = item.get('nightweather')  # 晚上天气现象
            weather_message = MESSAGE_PROMPT.format(date, day_temp, night_temp, day_weather, night_weather)

            message_result.append(weather_message)

        final_result = WEATHER_PROMPT.format(city, message_result[0], message_result[1:])
        return final_result
    except Exception as err:
        logger.warning('天气工具调用失败：{}', type(err).__name__)
        raise


