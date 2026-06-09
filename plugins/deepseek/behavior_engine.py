"""行为模式引擎 — 天气驱动、季节愿望、随机行为、活跃度波动。

让 bot 的行为更像真人：会根据天气/季节/心情自然地做出反应，
有时话多有时话少，偶尔突然想到什么。
"""
import random
from datetime import datetime
from typing import Any
from typing import Dict
from typing import Optional

from nonebot import logger

# ============================================================
# 天气驱动行为 — 根据天气自然反应
# ============================================================

_WEATHER_BEHAVIORS = {
    "rain": {
        "triggers": ["雨", "阵雨", "暴雨", "小雨", "中雨", "大雨", "雷阵雨"],
        "reactions": [
            "今天下雨了呢...好想窝在家里",
            "外面下雨了，你带伞了吗",
            "下雨天好适合睡觉啊",
            "雨声好好听，适合发呆",
            "下雨了不想出门...",
        ],
    },
    "snow": {
        "triggers": ["雪", "小雪", "中雪", "大雪", "暴雪", "雨夹雪"],
        "reactions": [
            "下雪了！好想堆雪人",
            "外面白白的，好漂亮",
            "下雪了路滑，小心点哦",
            "好冷啊...不想动",
            "下雪天适合喝热可可",
        ],
    },
    "hot": {
        "triggers": [],  # 由温度判断
        "temp_threshold": 33,
        "reactions": [
            "好热啊不想动...",
            "今天热死了，想吃冰",
            "热到融化...开空调了吗",
            "这种天气只想躺着",
            "好想去游泳啊",
        ],
    },
    "cold": {
        "triggers": [],
        "temp_threshold_low": 5,
        "reactions": [
            "好冷啊不想起床...",
            "今天好冷，多穿点",
            "冷到不想动...",
            "好想喝热可可暖暖手",
            "冬天最适合窝在被窝里了",
        ],
    },
    "haze": {
        "triggers": ["雾", "霾", "雾霾", "沙尘"],
        "reactions": [
            "今天空气不太好呢...",
            "外面灰蒙蒙的，不想出门",
            "雾霾天记得戴口罩哦",
        ],
    },
    "sunny": {
        "triggers": ["晴", "晴天"],
        "reactions": [
            "今天天气好好，想出去走走",
            "阳光好好啊~心情也好了",
            "大晴天！适合出去玩",
        ],
    },
    "cloudy": {
        "triggers": ["多云", "阴"],
        "reactions": [
            "今天阴天呢，有点懒懒的",
            "多云天气，不冷不热刚刚好",
        ],
    },
}


def get_weather_behavior(
    condition: str = "",
    temp: str = "",
    trigger_chance: float = 0.20,
) -> Optional[str]:
    """根据天气状况生成自然反应。

    Args:
        condition: 天气状况文本（晴/雨/雪...）
        temp: 温度字符串
        trigger_chance: 触发概率（默认20%）

    Returns:
        反应文本，或 None（本次不触发）
    """
    if random.random() > trigger_chance:
        return None

    if not condition:
        return None

    # 检查天气状况匹配
    for key, cfg in _WEATHER_BEHAVIORS.items():
        if key in ("hot", "cold"):
            continue  # 温度类单独处理
        for trigger in cfg.get("triggers", []):
            if trigger in condition:
                return random.choice(cfg["reactions"])

    # 检查温度
    try:
        temp_val = int(temp)
    except (ValueError, TypeError):
        return None

    if temp_val >= 33:
        return random.choice(_WEATHER_BEHAVIORS["hot"]["reactions"])
    elif temp_val <= 5:
        return random.choice(_WEATHER_BEHAVIORS["cold"]["reactions"])

    return None


# ============================================================
# 季节性愿望 — 偶尔自然流露
# ============================================================

_SEASONAL_WISHES = {
    "spring": [
        "好想出去赏花啊",
        "春天到了，想出去走走",
        "今天风好舒服，想放风筝",
        "春天好困啊...春困秋乏",
        "花开了呢，好想去看",
    ],
    "summer": [
        "好热啊想吃冰淇淋",
        "夏天好适合游泳",
        "好想去海边啊",
        "热到不想动...空调救我",
        "想吃西瓜",
    ],
    "autumn": [
        "秋天好舒服，想出去散步",
        "好想喝奶茶啊",
        "秋天的风好舒服",
        "想去看枫叶",
        "秋天最适合散步了",
    ],
    "winter": [
        "好想窝在被窝里",
        "好想喝热可可",
        "冬天好冷不想出门",
        "好想堆雪人啊",
        "冬天最适合吃火锅了",
    ],
}


def get_seasonal_wish(trigger_chance: float = 0.05) -> Optional[str]:
    """生成季节性愿望（5%概率）。

    偶尔自然流露对季节的感受，像真人一样。
    """
    if random.random() > trigger_chance:
        return None

    month = datetime.now().month
    if 3 <= month <= 5:
        season = "spring"
    elif 6 <= month <= 8:
        season = "summer"
    elif 9 <= month <= 11:
        season = "autumn"
    else:
        season = "winter"

    return random.choice(_SEASONAL_WISHES[season])


# ============================================================
# 随机行为增强 — 更多样的自发行为
# ============================================================

_RANDOM_BEHAVIORS = [
    {
        "type": "sudden_thought",
        "weight": 30,
        "templates": [
            "诶我突然想到...",
            "啊对了！",
            "等下我想起来一个事",
            "突然想到一个好笑的",
        ],
    },
    {
        "type": "mood_share",
        "weight": 20,
        "templates": [
            "今天心情好好~",
            "有点无聊呢",
            "突然有点困",
            "嘿嘿",
        ],
    },
    {
        "type": "anticipation",
        "weight": 15,
        "templates": [
            "好期待周末啊",
            "快放假了吧",
            "好想出去玩",
            "什么时候能休息啊",
        ],
    },
    {
        "type": "curiosity",
        "weight": 15,
        "templates": [
            "你在干嘛呀",
            "在忙什么",
            "今天过得怎么样",
            "有没有什么好玩的",
        ],
    },
    {
        "type": "promise",
        "weight": 10,
        "templates": [
            "下次我们一起去吧",
            "等有空了...",
            "改天请你",
        ],
    },
    {
        "type": "tease",
        "weight": 10,
        "templates": [
            "嘿嘿你在想什么",
            "是不是在想我",
            "偷偷告诉你一个事...算了不说了",
        ],
    },
]


def get_random_behavior(
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
    trigger_chance: float = 0.03,
) -> Optional[Dict[str, Any]]:
    """生成随机行为（3%概率）。

    根据时段和情绪选择行为类型，返回行为配置。
    """
    if random.random() > trigger_chance:
        return None

    # 根据时段调整权重
    weights = []
    for behavior in _RANDOM_BEHAVIORS:
        w = behavior["weight"]

        # 深夜：减少活跃类行为
        if schedule_period in ("sleeping", "night_owl"):
            if behavior["type"] in ("anticipation", "curiosity"):
                w = int(w * 0.3)

        # 犯困：减少需要精力的行为
        if schedule_period == "lazy":
            if behavior["type"] in ("tease", "promise"):
                w = int(w * 0.5)

        # 负面情绪：减少积极行为
        if bot_mood_dominant in ("生气", "难过"):
            if behavior["type"] in ("anticipation", "tease"):
                w = int(w * 0.3)
            elif behavior["type"] == "mood_share":
                w = int(w * 2)

        weights.append(max(1, w))

    # 加权随机选择
    total = sum(weights)
    probs = [w / total for w in weights]
    idx = random.choices(range(len(_RANDOM_BEHAVIORS)), weights=probs, k=1)[0]
    selected = _RANDOM_BEHAVIORS[idx]

    template = random.choice(selected["templates"])
    logger.debug(f"[行为] 随机行为: {selected['type']} -> {template[:20]}")

    return {
        "type": selected["type"],
        "text": template,
    }


# ============================================================
# 活跃度波动 — 回复长度/频率的修正
# ============================================================

def get_verbosity_modifier(
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
    hour: int = None,
    is_weekend: bool = False,
) -> float:
    """返回回复长度/活跃度的修正系数（0.5~1.5）。

    让 bot 有时话多有时话少，像真人一样有状态波动。
    """
    if hour is None:
        hour = datetime.now().hour

    modifier = 1.0

    # 时段修正
    period_mods = {
        "sleeping": 0.4,
        "waking": 0.6,
        "meal": 0.7,
        "lazy": 0.7,
        "active": 1.0,
        "night_owl": 0.7,
    }
    modifier *= period_mods.get(schedule_period, 1.0)

    # 周末修正
    if is_weekend:
        modifier *= 1.1  # 周末话多一点

    # 情绪修正
    mood_mods = {
        "开心": 1.2,
        "兴奋": 1.3,
        "害羞": 0.8,
        "生气": 0.6,
        "难过": 0.7,
        "担心": 0.9,
        "得意": 1.1,
        "撒娇": 1.1,
        "无聊": 0.8,
        "犯困": 0.6,
        "冷淡": 0.5,
    }
    modifier *= mood_mods.get(bot_mood_dominant, 1.0)

    # 随机波动（±10%）
    modifier *= random.uniform(0.9, 1.1)

    return max(0.4, min(1.5, modifier))


# ============================================================
# 行为模式提示生成（供 prompt 注入）
# ============================================================

def get_behavior_hint(
    weather_condition: str = "",
    weather_temp: str = "",
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
) -> Optional[str]:
    """综合生成行为模式提示，供 prompt 注入。

    优先级：天气反应 > 季节愿望 > 随机行为
    """
    # 天气驱动
    weather_hint = get_weather_behavior(weather_condition, weather_temp, trigger_chance=0.25)
    if weather_hint:
        return f"你对天气的自然反应：{weather_hint}。可以自然地表达出来。"

    # 季节愿望
    seasonal = get_seasonal_wish(trigger_chance=0.08)
    if seasonal:
        return f"你突然想到：{seasonal}。自然地流露出来。"

    # 随机行为
    random_behavior = get_random_behavior(schedule_period, bot_mood_dominant, trigger_chance=0.05)
    if random_behavior:
        return f"你突然{random_behavior['type']}：{random_behavior['text']}。"

    return None
