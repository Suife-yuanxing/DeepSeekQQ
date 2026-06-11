"""现实世界感知模块（Phase 6）。

接入 Open-Meteo 免费天气 API，为念念提供：
- 实时天气信息
- 时间感知（早晨/午后/傍晚/深夜）
- 季节信息
- 天气相关生活建议

Open-Meteo: 完全免费，无需 API Key，无需注册。
"""
import re
import time
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

import aiohttp
from nonebot import logger

from .api import get_http_session
from .config import WEATHER_CACHE_TTL
from .config import WEATHER_CITY

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
# Open-Meteo API（免费，无需 Key）
# ============================================================

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

# 城市→坐标本地缓存（避免每次都 geocoding）
_CITY_COORD_CACHE: Dict[str, Tuple[float, float]] = {
    "上海": (31.23, 121.47), "北京": (39.91, 116.40),
    "广州": (23.13, 113.26), "深圳": (22.54, 114.06),
    "杭州": (30.29, 120.15), "成都": (30.57, 104.07),
    "武汉": (30.59, 114.31), "南京": (32.06, 118.80),
    "重庆": (29.53, 106.50), "西安": (34.26, 108.94),
    "苏州": (31.30, 120.62), "天津": (39.13, 117.18),
}

# WMO Weather Code → 中文天气描述
# 确保 condition 文本能被 behavior_engine._WEATHER_BEHAVIORS 的 trigger 匹配
_WMO_CODE_MAP: Dict[Any, str] = {
    0: "晴天",
    (1, 2, 3): "多云",
    (45, 48): "雾霾",
    (51, 53, 55): "小雨",
    (56, 57): "冻雨",
    (61, 63, 65): "中雨",
    (66, 67): "暴雨",
    (71, 73, 75): "小雪",
    77: "大雪",
    (80, 81, 82): "阵雨",
    (85, 86): "阵雪",
    (95, 96, 99): "雷阵雨",
}

# 风向角度→中文
_WIND_DIRS = [
    (0, "北"), (45, "东北"), (90, "东"), (135, "东南"),
    (180, "南"), (225, "西南"), (270, "西"), (315, "西北"), (360, "北"),
]


def _wmo_to_condition(code: int) -> str:
    """将 WMO 天气码转为中文描述。"""
    for key, val in _WMO_CODE_MAP.items():
        if isinstance(key, tuple):
            if code in key:
                return val
        elif key == code:
            return val
    return f"未知天气(code={code})"


def _wind_deg_to_dir(deg: float) -> str:
    """风向角度→中文风向。"""
    closest = min(_WIND_DIRS, key=lambda d: abs(d[0] - deg))
    return closest[1]


async def _openmeteo_get(url: str, params: dict, timeout_sec: int = 10):
    """单次 Open-Meteo HTTP GET 调用。

    Returns:
        dict 或 None（失败时）
    """
    session = await get_http_session()
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=timeout_sec)) as resp:
            if resp.status != 200:
                logger.warning(f"[天气] Open-Meteo HTTP {resp.status}: {url}")
                return None
            return await resp.json()
    except Exception as e:
        logger.warning(f"[天气] Open-Meteo 请求失败: {e}")
        return None


async def _geocode_city(city_name: str) -> Optional[Tuple[float, float]]:
    """查询城市坐标。优先本地缓存，fallback 到 Geocoding API。"""
    # 优先本地缓存
    if city_name in _CITY_COORD_CACHE:
        return _CITY_COORD_CACHE[city_name]

    try:
        params = {"name": city_name, "count": 1, "language": "zh",
                   "format": "json"}
        data = await _openmeteo_get(_GEOCODING_URL, params)
        if data and data.get("results"):
            r = data["results"][0]
            lat, lon = float(r["latitude"]), float(r["longitude"])
            # 加入缓存
            _CITY_COORD_CACHE[city_name] = (lat, lon)
            logger.debug(f"[天气] Geocoding: {city_name} → ({lat:.2f}, {lon:.2f})")
            return (lat, lon)
    except Exception as e:
        logger.error(f"[天气] Geocoding 失败: {e}")
    return None


def extract_city_from_message(msg: str) -> Optional[str]:
    """从用户消息中提取城市名。"""
    _KNOWN_CITIES = {
        *_CITY_COORD_CACHE.keys(),
        "东莞", "佛山", "宁波", "厦门", "长沙", "郑州", "合肥", "济南",
        "青岛", "大连", "昆明", "福州", "贵阳", "南昌", "哈尔滨", "长春",
        "沈阳", "石家庄", "太原", "兰州", "海口", "银川", "西宁", "拉萨",
        "呼和浩特", "南宁", "珠海", "惠州", "泉州", "烟台", "无锡", "常州",
        "温州", "嘉兴", "绍兴", "金华", "台州", "芜湖", "绵阳", "中山",
        "三亚", "桂林", "丽江", "洛阳", "开封", "扬州", "镇江", "徐州",
        "连云港", "威海", "日照", "秦皇岛", "廊坊", "保定", "唐山",
    }
    _C = r'[一-龥]'
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
            if city in _KNOWN_CITIES:
                return city
    return None


async def get_weather(city: str = None) -> Optional[WeatherInfo]:
    """获取天气信息（Open-Meteo，带缓存）。

    Args:
        city: 城市名。None 则用配置默认城市。

    Returns:
        WeatherInfo 或 None（获取失败时）
    """
    if city is None:
        city = WEATHER_CITY

    # 检查缓存
    cached = _get_cache(city)
    if cached:
        return cached

    try:
        # 1. 获取城市坐标
        coord = await _geocode_city(city)
        if not coord:
            logger.warning(f"[天气] 城市未找到: {city}")
            return None

        lat, lon = coord
        weather_info = WeatherInfo()

        # 2. 获取实时天气
        weather_params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                      "weather_code,wind_speed_10m,wind_direction_10m",
            "timezone": "Asia/Shanghai",
            "forecast_days": 1,
        }
        weather_data = await _openmeteo_get(_WEATHER_URL, weather_params)
        if weather_data and weather_data.get("current"):
            cur = weather_data["current"]
            code = cur.get("weather_code", -1)
            weather_info.condition = _wmo_to_condition(code)
            weather_info.temp = str(round(cur.get("temperature_2m", 0)))
            weather_info.feels_like = str(round(cur.get("apparent_temperature", 0)))
            weather_info.humidity = str(cur.get("relative_humidity_2m", ""))
            wind_speed = cur.get("wind_speed_10m", 0) or 0
            weather_info.wind_scale = str(round(wind_speed / 3.6, 1)) if wind_speed else ""
            wind_deg = cur.get("wind_direction_10m", 0) or 0
            weather_info.wind_dir = _wind_deg_to_dir(wind_deg)
            weather_info.text = f"{weather_info.condition} {weather_info.temp}°C"
        else:
            logger.warning(f"[天气] 天气数据为空: {city}")
            return None

        # 3. 获取空气质量（可选，失败不影响主流程）
        try:
            air_params = {
                "latitude": lat,
                "longitude": lon,
                "current": "european_aqi",
                "timezone": "Asia/Shanghai",
            }
            air_data = await _openmeteo_get(_AIR_QUALITY_URL, air_params)
            if air_data and air_data.get("current"):
                aqi = air_data["current"].get("european_aqi", "")
                if aqi:
                    quality_labels = {1: "优", 2: "良", 3: "轻度污染", 4: "中度污染", 5: "重度污染"}
                    label = quality_labels.get(int(aqi), f"AQI:{aqi}")
                    weather_info.air_quality = f"{label}(AQI:{aqi})"
        except Exception:
            pass  # 空气质量获取失败不影响主流程

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
    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone
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
    elif hour >= 22 or hour < 2:
        return "深夜"
    else:
        return "凌晨"


def get_season() -> str:
    """获取当前季节。"""
    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone
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
        wind_str = f"{weather.wind_dir}"
        if weather.wind_scale:
            wind_str += f"{weather.wind_scale}级"
        weather_line += f"，{wind_str}"
    lines.append(weather_line)

    if weather.air_quality:
        lines.append(f"- 空气：{weather.air_quality}")

    suggestion = get_weather_suggestion(weather)
    if suggestion:
        lines.append(f"- 建议：{suggestion}")

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

    if temp_val >= 35:
        suggestions.append("今天好热呀，记得多喝水，别中暑了~")
    elif temp_val >= 30:
        suggestions.append("今天有点热呢，出门记得防晒~")
    elif temp_val <= 0:
        suggestions.append("外面好冷呀，多穿点衣服，别冻着了~")
    elif temp_val <= 5:
        suggestions.append("今天挺冷的，注意保暖哦~")

    condition = weather.condition
    if "雨" in condition:
        suggestions.append("外面下雨了，出门记得带伞~")
    elif "雪" in condition:
        suggestions.append("下雪了呢，路滑小心哦~")
    elif "雾" in condition or "霾" in condition:
        suggestions.append("今天能见度低，出门注意安全~")

    return " ".join(suggestions) if suggestions else ""
