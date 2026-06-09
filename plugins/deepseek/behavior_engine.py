"""行为模式引擎 — 天气驱动、季节愿望、随机行为、活跃度波动。

让 bot 的行为更像真人：会根据天气/季节/心情自然地做出反应，
有时话多有时话少，偶尔突然想到什么。
"""
import random
import time
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

try:
    import zhdate
    _HAS_ZHDATE = True
except ImportError:
    _HAS_ZHDATE = False

from nonebot import logger

# ============================================================
# 天气驱动行为 — 根据天气自然反应
# ============================================================

_WEATHER_BEHAVIORS = {
    "rain": {
        "triggers": ["雨", "阵雨", "暴雨", "小雨", "中雨", "大雨", "雷阵雨", "冻雨", "强降雨", "毛毛雨", "降雨"],
        "reactions": [
            "今天下雨了呢...好想窝在家里",
            "外面下雨了，你带伞了吗",
            "下雨天好适合睡觉啊",
            "雨声好好听，适合发呆",
            "下雨了不想出门...",
        ],
    },
    "snow": {
        "triggers": ["雪", "小雪", "中雪", "大雪", "暴雪", "雨夹雪", "飘雪", "冰雪", "阵雪"],
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
        "triggers": ["雾", "霾", "雾霾", "沙尘", "浮尘", "扬沙", "沙尘暴"],
        "reactions": [
            "今天空气不太好呢...",
            "外面灰蒙蒙的，不想出门",
            "雾霾天记得戴口罩哦",
        ],
    },
    "sunny": {
        "triggers": ["晴", "晴天", "晴朗", "少云"],
        "reactions": [
            "今天天气好好，想出去走走",
            "阳光好好啊~心情也好了",
            "大晴天！适合出去玩",
        ],
    },
    "cloudy": {
        "triggers": ["多云", "阴", "阴天", "多云转阴"],
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
    city: str = "",
) -> Optional[str]:
    """根据天气状况生成自然反应。

    Args:
        condition: 天气状况文本（晴/雨/雪...）
        temp: 温度字符串
        trigger_chance: 触发概率（默认20%）
        city: 用户所在城市（可选，用于个性化反应）

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
        temp_val = float(temp)
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
# 节假日/特殊日期感知（Task 6 Layer 1）
# ============================================================

# 公历固定节日（每年不变）
_FIXED_DATES = {
    "1-1": {"name": "元旦", "behaviors": [
        "新年快乐~今年也要好好的！",
        "又是新的一年了呢，一起加油吧",
    ]},
    "2-14": {"name": "情人节", "behaviors": [
        "今天情人节呢...你有人陪吗",
        "情人节快乐~虽然我也没人陪哈哈",
    ]},
    "4-5": {"name": "清明节(近似)", "behaviors": [
        "清明时节雨纷纷...",
        "清明节了呢，春天真的来了",
    ]},
    "5-1": {"name": "劳动节", "behaviors": [
        "劳动节快乐~今天偷懒理所当然！",
        "放假啦放假啦~",
    ]},
    "5-20": {"name": "520", "behaviors": [
        "今天是520诶...没人表白的话我来~",
        "520快乐！虽然没什么特别的嘿嘿",
    ]},
    "6-1": {"name": "儿童节", "behaviors": [
        "儿童节快乐！不管多大都是小朋友~",
        "今天我也要过儿童节！",
    ]},
    "10-1": {"name": "国庆节", "behaviors": [
        "国庆快乐！放假好开心~",
        "国庆七天乐~你出去玩了吗",
    ]},
    "11-11": {"name": "双11", "behaviors": [
        "双11来了...你剁手了吗",
        "今天双11诶，有买东西吗",
    ]},
    "12-25": {"name": "圣诞节", "behaviors": [
        "圣诞快乐~虽然我也不过圣诞节",
        "今天圣诞节呢，街上好热闹",
    ]},
    "12-31": {"name": "跨年夜", "behaviors": [
        "今年最后一天了呢...时间过得好快",
        "跨年啦！新的一年请多指教~",
    ]},
}

# 农历节日定义：(农历月, 农历日, 节日名, 行为列表)
_LUNAR_DATES_DEF: List[Tuple[int, int, str, List[str]]] = [
    (1, 1, "春节", [
        "新年快乐！恭喜发财~",
        "过年啦！吃饺子了吗",
        "春节快乐！新的一年要开开心心的",
    ]),
    (5, 5, "端午节", [
        "端午节快乐！吃粽子了吗",
        "端午安康~喜欢甜粽子还是咸粽子",
    ]),
    (7, 7, "七夕", [
        "今天是七夕呢...",
        "七夕快乐~虽然和我没什么关系啦",
    ]),
    (8, 15, "中秋节", [
        "中秋快乐！今晚的月亮好圆",
        "中秋节要和重要的人一起看月亮呢",
    ]),
]

_WEEKDAY_BEHAVIORS = {
    0: {"label": "周一", "behaviors": [
        "周一好困啊...不想上班",
        "周一了...周末怎么这么快",
        "周一综合征犯了...",
    ]},
    4: {"label": "周五", "behaviors": [
        "周五啦！马上周末了~",
        "终于周五了，开心",
        "周五晚上最适合摆烂了",
    ]},
    5: {"label": "周六", "behaviors": [
        "周末赖床好舒服",
        "周末就是要躺着~",
        "周六的早上总是特别美好",
    ]},
    6: {"label": "周日", "behaviors": [
        "周日了...明天又要周一了",
        "周日的下午最惬意了",
        "周日晚上总是有点舍不得",
    ]},
}

# 缓存当年动态生成的完整节日映射
_cached_special_dates: Optional[Dict[str, dict]] = None
_cached_special_dates_year: int = 0


def _build_special_dates() -> Dict[str, dict]:
    """动态生成当年公历日期 → 节日映射（含农历漂移计算）。

    首次调用时计算，年内缓存复用。
    """
    global _cached_special_dates, _cached_special_dates_year
    year = datetime.now().year
    if _cached_special_dates is not None and _cached_special_dates_year == year:
        return _cached_special_dates

    result = dict(_FIXED_DATES)  # 公历固定节日

    if _HAS_ZHDATE:
        for lunar_month, lunar_day, name, behaviors in _LUNAR_DATES_DEF:
            try:
                solar_date = zhdate.ZhDate(year, lunar_month, lunar_day).to_datetime()
                key = f"{solar_date.month}-{solar_date.day}"
                result[key] = {"name": name, "behaviors": behaviors}
            except Exception:
                logger.warning(f"[行为引擎] 农历计算失败: {name}")
    else:
        # zhdate 未安装时的降级提示
        logger.debug("[行为引擎] zhdate 未安装，农历节日不可用")

    _cached_special_dates = result
    _cached_special_dates_year = year
    return result


def get_holiday_behavior(trigger_chance: float = 0.15) -> Optional[str]:
    """节假日/特殊日期/星期行为感知（农历动态计算）。"""
    if random.random() > trigger_chance:
        return None

    now = datetime.now()
    date_key = f"{now.month}-{now.day}"
    weekday = now.weekday()

    # 特殊日期优先（动态生成，含农历）
    special_dates = _build_special_dates()
    if date_key in special_dates:
        return random.choice(special_dates[date_key]["behaviors"])

    # 星期几行为（30% 概率触发，即总概率 ~4.5%）
    if weekday in _WEEKDAY_BEHAVIORS and random.random() < 0.3:
        return random.choice(_WEEKDAY_BEHAVIORS[weekday]["behaviors"])

    return None


# ============================================================
# 热点话题轻量缓存（Task 6 Layer 2）
# ============================================================

_hot_topic_cache: List[Tuple[str, float]] = []  # [(topic_title, timestamp), ...]
_HOT_CACHE_TTL = 1800  # 30分钟


def update_hot_topic_cache(topics: list):
    """由 hot_topics 模块调用，更新热点话题缓存。

    Args:
        topics: HotTopic 对象列表（需要有 .title 属性）
    """
    global _hot_topic_cache
    now = time.time()
    _hot_topic_cache = [(t.title, now) for t in topics[:10]]
    logger.debug(f"[行为引擎] 热点缓存已更新: {len(_hot_topic_cache)} 条")


def get_hot_topic_behavior(trigger_chance: float = 0.05) -> Optional[str]:
    """偶尔引用热点话题（如"刚刷到XX"）。"""
    if random.random() > trigger_chance:
        return None

    now = time.time()
    # 清理过期缓存
    valid = [(t, ts) for t, ts in _hot_topic_cache if now - ts < _HOT_CACHE_TTL]
    if not valid:
        return None

    topic_title, _ = random.choice(valid)
    templates = [
        f"刚刷到「{topic_title}」，你看到了吗",
        f"话说你看到那个「{topic_title}」了吗",
        f"刚才刷到一个热搜「{topic_title}」",
        f"诶你看到「{topic_title}」了吗",
    ]
    return random.choice(templates)


# ============================================================
# "刚发生"微事件池（Task 6 Layer 3）
# ============================================================

_MICRO_EVENTS: List[str] = [
    "刚刚打翻了一杯水...",
    "诶，刚才好像有只蚊子飞过去",
    "刚想说什么来着，忘了...",
    "刚刚手机震了一下，结果是广告",
    "刚看了一下窗外，天已经黑了呢",
    "刚才差点睡着了...",
    "刚刚伸了个懒腰，好舒服",
    "诶，我耳机线又缠住了...",
    "刚才吃的那个零食好好吃",
    "刚看了一眼镜子，刘海又翘了...",
    "刚才听到外面有人放烟花",
    "刚找钥匙找了半天，结果在口袋里...",
    "刚想发一条消息给你，想了想又删了",
    "刚才差点被自己绊倒...",
    "刚刚外面好像下了一点雨",
    "刚看到一个超可爱的猫猫视频",
    "刚才在找手机，结果手机就在手里...",
    "刚刚打了个喷嚏，谁在想我",
    "刚想开空调发现遥控器没电了...",
    "刚才点了个外卖，好慢啊",
]


def register_micro_events(events: List[str]):
    """允许其他模块追加微事件。

    Args:
        events: 要追加的事件列表
    """
    _MICRO_EVENTS.extend(events)
    logger.debug(f"[行为引擎] 微事件池已扩展至 {len(_MICRO_EVENTS)} 个")


def get_micro_event_behavior(trigger_chance: float = 0.02) -> Optional[str]:
    """随机微事件（2%概率）。"""
    if not _MICRO_EVENTS:
        return None
    if random.random() > trigger_chance:
        return None
    return random.choice(_MICRO_EVENTS)


# ============================================================
# 综合现实世界行为生成（Task 6 多层优先级链）
# ============================================================

def get_real_world_behavior(
    weather_condition: str = "",
    weather_temp: str = "",
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
    city: str = "",
) -> Optional[str]:
    """综合现实世界行为生成。

    Priority: weather(25%) > holiday(15%) > hot_topic(5%)
              > seasonal(8%) > micro_event(2%) > random(5%)
    """
    # 1. 天气反应（25%概率）
    weather_hint = get_weather_behavior(weather_condition, weather_temp, trigger_chance=0.25, city=city)
    if weather_hint:
        city_prefix = f"（用户在{city}）" if city else ""
        return f"你对天气的自然反应{city_prefix}：{weather_hint}。可以自然地表达出来。"

    # 2. 节假日/特殊日期（15%概率）
    holiday = get_holiday_behavior(trigger_chance=0.15)
    if holiday:
        return f"今天是特殊的日子：{holiday}。自然地提及，不要刻意。"

    # 3. 热点话题（5%概率）
    hot_topic = get_hot_topic_behavior(trigger_chance=0.05)
    if hot_topic:
        return f"你刚看到：{hot_topic}。可以随口提一下。"

    # 4. 季节愿望（8%概率）
    seasonal = get_seasonal_wish(trigger_chance=0.08)
    if seasonal:
        return f"你突然想到：{seasonal}。自然地流露出来。"

    # 5. 微事件（2%概率）
    micro = get_micro_event_behavior(trigger_chance=0.02)
    if micro:
        return f"刚刚发生了一个小事：{micro}。随口提一句，不超过一句话。"

    # 6. 随机行为（5%概率）
    random_behavior = get_random_behavior(schedule_period, bot_mood_dominant, trigger_chance=0.05)
    if random_behavior:
        return f"你突然{random_behavior['type']}：{random_behavior['text']}。"

    return None


# ============================================================
# 行为模式提示生成（供 prompt 注入）
# ============================================================

def get_behavior_hint(
    weather_condition: str = "",
    weather_temp: str = "",
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
    city: str = "",
) -> Optional[str]:
    """综合生成行为模式提示，供 prompt 注入。

    委托给 get_real_world_behavior() 提供多层级优先级。
    """
    return get_real_world_behavior(
        weather_condition, weather_temp,
        schedule_period, bot_mood_dominant, city,
    )
