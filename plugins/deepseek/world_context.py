"""现实世界感知模块（Phase 6）。

接入和风天气 API，为猫娘提供：
- 实时天气信息
- 时间感知（早晨/午后/傍晚/深夜）
- 季节信息
- 天气相关生活建议
"""
import re
import time
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass

import aiohttp
from nonebot import logger

from .config import WEATHER_API_KEY, WEATHER_CITY, WEATHER_CACHE_TTL
from .api import get_http_session


# ============================================================
# 数据结构
# ============================================================

@dataclass
class WeatherInfo:
    condition: str = "未知"       # 天气状况：晴/多云/雨...
    temp: str = "--"              # 温度
    feels_like: str = "--"        # 体感温度
    humidity: str = "--"          # 湿度
    wind_dir: str = ""            # 风向
    wind_scale: str = ""          # 风力
    air_quality: str = ""         # 空气质量
    sunrise: str = ""             # 日出
    sunset: str = ""              # 日落
    text: str = ""                # 天气文字描述


# ============================================================
# 缓存
# ============================================================

_weather_cache: Dict[str, tuple] = {}  # city -> (WeatherInfo, timestamp)


def _get_cache(city: str) -> Optional[WeatherInfo]:
    if city in _weather_cache:
        data, ts = _weather_cache[city]
        if time.time() - ts < WEATHER_CACHE_TTL:
            return data
    return None


def _set_cache(city: str, data: WeatherInfo):
    _weather_cache[city] = (data, time.time())


# ============================================================
# 和风天气 API
# ============================================================

# 和风天气 API（使用标准端点，与 geoapi 一致认证方式）
_QWEATHER_GEO_URL = "https://geoapi.qweather.com/v2/city/lookup"
_QWEATHER_NOW_URL = "https://api.qweather.com/v7/weather/now"
_QWEATHER_AIR_URL = "https://api.qweather.com/v7/air/now"

# 常用城市 ID 映射（跳过 geo 查询，直接用 ID）
_CITY_ID_MAP = {
    "上海": "101020100", "北京": "101010100", "广州": "101280101",
    "深圳": "101280601", "杭州": "101210101", "成都": "101270101",
    "武汉": "101200101", "南京": "101190101", "重庆": "101040100",
    "西安": "101110101", "苏州": "101190401", "天津": "101030100",
}


async def _lookup_city(city_name: str) -> Optional[str]:
    """查询城市 ID。优先使用本地映射，fallback 到 API。"""
    # 优先使用本地映射
    if city_name in _CITY_ID_MAP:
        return _CITY_ID_MAP[city_name]

    # fallback: API 查询
    try:
        session = await get_http_session()
        params = {"location": city_name, "key": WEATHER_API_KEY, "number": 1}
        async with session.get(_QWEATHER_GEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"[天气] Geo API 状态码: {resp.status}")
                return None
            data = await resp.json()
            if data.get("code") == "200" and data.get("location"):
                return data["location"][0]["id"]
            logger.warning(f"[天气] Geo API 返回: code={data.get('code')}")
    except Exception as e:
        logger.error(f"[天气] 城市查询失败: {e}")
    return None


def extract_city_from_message(msg: str) -> Optional[str]:
    """从用户消息中提取城市名。"""
    # 常见中国城市名（用于验证提取结果，避免误匹配非城市文本）
    _KNOWN_CITIES = {
        # _CITY_ID_MAP 中的 12 个
        *_CITY_ID_MAP.keys(),
        # 其他常见城市
        "东莞", "佛山", "宁波", "厦门", "长沙", "郑州", "合肥", "济南",
        "青岛", "大连", "昆明", "福州", "贵阳", "南昌", "哈尔滨", "长春",
        "沈阳", "石家庄", "太原", "兰州", "海口", "银川", "西宁", "拉萨",
        "呼和浩特", "南宁", "珠海", "惠州", "泉州", "烟台", "无锡", "常州",
        "温州", "嘉兴", "绍兴", "金华", "台州", "芜湖", "绵阳", "中山",
        "三亚", "桂林", "丽江", "洛阳", "开封", "扬州", "镇江", "徐州",
        "连云港", "威海", "日照", "秦皇岛", "廊坊", "保定", "唐山",
    }
    # 匹配 "我在XX" / "XX天气" / "XX今天" 等模式
    # 用 [一-龥&&[^了呢吧吗个过]] 排除常见后缀字，避免"去东莞了"被匹配为"东莞了"
    # Python re 不支持字符类差集，改用 [^\W\d_了呢吧吗个过] 等效写法
    _C = r'[一-龥]'  # 单个汉字
    patterns = [
        r'我在(' + _C + r'{2,4})',
        r'(' + _C + r'{2,4})天气',
        r'(' + _C + r'{2,4})今天',
        r'(' + _C + r'{2,4})多少度',
        r'来(' + _C + r'{2,4})了',
        r'到(' + _C + r'{2,4})了',
        r'去(' + _C + r'{2,4}?)(?:[了呢吧吗个过]|$)',
    ]
    for pattern in patterns:
        match = re.search(pattern, msg)
        if match:
            city = match.group(1)
            # 必须是已知城市名，避免"上课喵"、"教室"等误匹配
            if city in _KNOWN_CITIES:
                return city
    return None


async def get_weather(city: str = None) -> Optional[WeatherInfo]:
    """获取天气信息（带缓存）。"""
    if not WEATHER_API_KEY:
        logger.warning("[天气] WEATHER_API_KEY 未配置")
        return None

    if city is None:
        city = WEATHER_CITY

    # 检查缓存
    cached = _get_cache(city)
    if cached:
        return cached

    try:
        # 1. 查询城市 ID
        city_id = await _lookup_city(city)
        if not city_id:
            logger.warning(f"[天气] 城市未找到: {city}")
            return None

        session = await get_http_session()

        # 2. 获取实时天气
        params = {"location": city_id, "key": WEATHER_API_KEY}
        weather_info = WeatherInfo()

        async with session.get(_QWEATHER_NOW_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("code") != "200":
                    logger.warning(f"[天气] Weather API 返回: code={data.get('code')}, msg={data.get('msg','')}")
                if data.get("code") == "200" and data.get("now"):
                    now = data["now"]
                    weather_info.condition = now.get("text", "未知")
                    weather_info.temp = now.get("temp", "--")
                    weather_info.feels_like = now.get("feelsLike", "--")
                    weather_info.humidity = now.get("humidity", "--")
                    weather_info.wind_dir = now.get("windDir", "")
                    weather_info.wind_scale = now.get("windScale", "")

        # 3. 获取空气质量
        async with session.get(_QWEATHER_AIR_URL, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("code") == "200" and data.get("now"):
                    aqi = data["now"].get("aqi", "")
                    category = data["now"].get("category", "")
                    if category:
                        weather_info.air_quality = f"{category}(AQI:{aqi})"

        # 写入缓存
        _set_cache(city, weather_info)
        logger.info(f"[天气] 获取成功: {city} {weather_info.condition} {weather_info.temp}°C")
        return weather_info

    except Exception as e:
        logger.error(f"[天气] 获取失败: {e}")
        return None


# ============================================================
# 时间感知
# ============================================================

def get_time_of_day() -> str:
    """获取当前时间段描述。"""
    from datetime import datetime, timezone, timedelta
    hour = datetime.now(timezone(timedelta(hours=8))).hour
    if 5 <= hour < 9:
        return "清晨"
    elif 9 <= hour < 12:
        return "上午"
    elif 12 <= hour < 14:
        return "中午"
    elif 14 <= hour < 17:
        return "午后"
    elif 17 <= hour < 19:
        return "傍晚"
    elif 19 <= hour < 22:
        return "晚上"
    elif 22 <= hour or hour < 2:
        return "深夜"
    else:
        return "凌晨"


def get_season() -> str:
    """获取当前季节。"""
    from datetime import datetime, timezone, timedelta
    month = datetime.now(timezone(timedelta(hours=8))).month
    if month in (3, 4, 5):
        return "春天"
    elif month in (6, 7, 8):
        return "夏天"
    elif month in (9, 10, 11):
        return "秋天"
    else:
        return "冬天"


# ============================================================
# Prompt 注入
# ============================================================

async def build_world_context_prompt(city: str = None) -> str:
    """构建世界上下文 prompt 注入文本。city 可由用户消息或记忆标签提供。"""
    if not WEATHER_API_KEY:
        return ""

    weather = await get_weather(city)
    if not weather:
        return ""

    time_of_day = get_time_of_day()
    season = get_season()

    lines = ["【现实世界感知】"]
    lines.append(f"- 时间感知：{time_of_day}，{season}")

    weather_line = f"- 天气：{weather.condition}，{weather.temp}°C"
    if weather.feels_like and weather.feels_like != "--":
        weather_line += f"（体感{weather.feels_like}°C）"
    if weather.wind_dir:
        weather_line += f"，{weather.wind_dir}{weather.wind_scale}级"
    lines.append(weather_line)

    if weather.air_quality:
        lines.append(f"- 空气：{weather.air_quality}")

    return "\n".join(lines)


def get_weather_suggestion(weather: WeatherInfo) -> str:
    """根据天气生成生活建议。"""
    if not weather:
        return ""

    temp = weather.temp
    try:
        temp_val = int(temp)
    except (ValueError, TypeError):
        return ""

    suggestions = []

    # 温度建议
    if temp_val >= 35:
        suggestions.append("今天好热呀，记得多喝水，别中暑了~")
    elif temp_val >= 30:
        suggestions.append("今天有点热呢，出门记得防晒~")
    elif temp_val <= 0:
        suggestions.append("外面好冷呀，多穿点衣服，别冻着了~")
    elif temp_val <= 5:
        suggestions.append("今天挺冷的，注意保暖哦~")

    # 天气状况建议
    condition = weather.condition
    if "雨" in condition:
        suggestions.append("外面下雨了，出门记得带伞~")
    elif "雪" in condition:
        suggestions.append("下雪了呢，路滑小心哦~")
    elif "雾" in condition or "霾" in condition:
        suggestions.append("今天能见度低，出门注意安全~")

    return " ".join(suggestions) if suggestions else ""
