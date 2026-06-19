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
from . import config

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
    affection_score: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """生成随机行为（3%概率）。

    根据时段、情绪和好感度选择行为类型，返回行为配置。
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

        # 好感度修正：高好感更亲密，低好感更克制
        if affection_score >= 200:
            if behavior["type"] in ("tease", "curiosity"):
                w = int(w * 2.0)  # 熟人之间更放得开
            elif behavior["type"] == "promise":
                w = int(w * 1.5)
        elif affection_score < 20:
            if behavior["type"] == "tease":
                w = 0  # 陌生人不会调戏
            elif behavior["type"] == "promise":
                w = int(w * 0.3)

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
    affection_score: float = 0.0,
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

    # 好感度修正：越熟越话多，越生越克制
    if affection_score >= 200:
        modifier += 0.1
    elif affection_score < 20:
        modifier -= 0.1

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

# 真人化 P1-3：注册到全局状态表
try:
    from .global_state import register as _gs_reg
    from .global_state import register_snapshot as _gs_snap
    _gs_reg("behavior._hot_topic_cache", [], namespace="behavior")
    _gs_reg("behavior._cached_special_dates", None, namespace="behavior")
    _gs_reg("behavior._cached_special_dates_year", 0, namespace="behavior")
except ImportError:
    _gs_reg = None
    _gs_snap = None


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
    """偶尔引用热点话题（如"刚刷到XX"）。优先使用 social_feed。"""
    if random.random() > trigger_chance:
        return None

    # 优先从 social_feed 获取（有去重和衰减）
    try:
        from .social_feed import get_scroll_trigger_hint
        hint = get_scroll_trigger_hint()
        if hint:
            return hint
    except Exception:
        pass

    # fallback: 旧的热点缓存
    now = time.time()
    valid = [(t, ts) for t, ts in _hot_topic_cache if now - ts < _HOT_CACHE_TTL]
    if not valid:
        return None

    topic_title, _ = random.choice(valid)
    logger.debug(f"[行为] 热点缓存回退触发: {topic_title[:30]} (social_feed 无数据)")
    templates = [
        f"刚刷到「{topic_title}」，你看到了吗",
        f"话说你看到那个「{topic_title}」了吗",
        f"刚才刷到一个热搜「{topic_title}」",
        f"诶你看到「{topic_title}」了吗",
    ]
    return random.choice(templates)


# ============================================================
# 刷手机行为模板（Social Feed Behavior）
# ============================================================

_SCROLL_BEHAVIORS = [
    "刚刷到的...{content}",
    "今天{source}上都在刷...{content}",
    "刷到一条超好笑的...{content}",
    "刚才在{source}看到{content}",
    "诶你看到{content}了吗",
    "{source}上好多人都在发{content}",
    "笑死，刚刷到一个{content}",
    "话说{content}...你关注了吗",
]


def get_scroll_behavior(trigger_chance: float = 0.12) -> Optional[str]:
    """从 social_feed 获取自然引用（12%概率，仅次于天气25%和节日15%）。

    优先级高于旧的热点话题引用（5%）。
    """
    if random.random() > trigger_chance:
        return None

    try:
        from .social_feed import get_scroll_trigger_hint
        return get_scroll_trigger_hint()
    except Exception:
        return None


# ============================================================
# "刚发生"微事件池（真人化 P2-1：动态生成 + 冷却期 + 持久化）
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

# 真人化 P2-1：微事件冷却追踪
# {event_key: [(user_id, timestamp), ...]}
_MICRO_EVENT_HISTORY: Dict[str, List[tuple]] = {}
_MICRO_EVENT_COOLDOWN = 86400 * 30  # 30 天冷却

# 微事件 LLM 生成缓存（避免频繁调用 LLM）
_LLM_MICRO_EVENT_CACHE: List[str] = []
_LLM_MICRO_EVENT_CACHE_EXPIRY: float = 0.0
_LLM_MICRO_EVENT_CACHE_TTL = 86400  # 1 天


def register_micro_events(events: List[str]):
    """允许其他模块追加微事件。

    Args:
        events: 要追加的事件列表
    """
    _MICRO_EVENTS.extend(events)
    logger.debug(f"[行为引擎] 微事件池已扩展至 {len(_MICRO_EVENTS)} 个")


def _is_micro_event_in_cooldown(event_text: str, user_id: str) -> bool:
    """检查微事件对该用户是否在冷却期内（同步，仅检查内存缓存）。

    真人化 P2-1：同一事件对同一用户 30 天内不重复。
    对于完整检查，请使用 async 版本 `is_micro_event_available()`。
    """
    # 用事件的前 10 个字作为简易 key（避免完全相同才命中）
    event_key = event_text[:10]
    if event_key not in _MICRO_EVENT_HISTORY:
        return False

    now = time.time()
    for uid, ts in _MICRO_EVENT_HISTORY[event_key]:
        if uid == user_id and (now - ts) < _MICRO_EVENT_COOLDOWN:
            return True
    return False


def _record_micro_event_sent(event_text: str, user_id: str):
    """记录微事件已发送给某用户（同步，仅内存缓存）。

    真人化 P2-1：同时尝试写入 DB（fire-and-forget），持久化确保重启后冷却期不丢失。
    """
    event_key = event_text[:10]
    if event_key not in _MICRO_EVENT_HISTORY:
        _MICRO_EVENT_HISTORY[event_key] = []
    _MICRO_EVENT_HISTORY[event_key].append((user_id, time.time()))

    # 清理旧记录（超过冷却期的）
    now = time.time()
    _MICRO_EVENT_HISTORY[event_key] = [
        (uid, ts) for uid, ts in _MICRO_EVENT_HISTORY[event_key]
        if (now - ts) < _MICRO_EVENT_COOLDOWN
    ]

    # 限制历史大小
    if len(_MICRO_EVENT_HISTORY) > 500:
        # 删除最老的 key
        oldest_key = min(_MICRO_EVENT_HISTORY, key=lambda k: min(
            (ts for _, ts in _MICRO_EVENT_HISTORY[k]), default=0
        ))
        del _MICRO_EVENT_HISTORY[oldest_key]

    # 真人化 P2-1：fire-and-forget 写入 DB（异步持久化）
    try:
        import asyncio as _asyncio
        # 尝试获取当前事件循环——如果在运行中，创建 task
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_save_micro_event_to_db(user_id, event_text))
        else:
            _asyncio.run(_save_micro_event_to_db(user_id, event_text))
    except RuntimeError:
        pass  # 无事件循环时静默降级


async def _save_micro_event_to_db(user_id: str, event_text: str):
    """异步将微事件写入 DB（fire-and-forget 使用）。"""
    try:
        from . import db_proactive
        await db_proactive.save_micro_event_sent(user_id, event_text)
    except Exception:
        pass


async def is_micro_event_available(user_id: str, event_text: str) -> bool:
    """检查微事件对该用户是否可用（异步，综合检查内存+DB）。

    真人化 P2-1：先检查内存缓存（快），再检查 DB（准确）。
    """
    # 1. 先查内存缓存
    if _is_micro_event_in_cooldown(event_text, user_id):
        return False

    # 2. 再查 DB（持久化记录，防重启丢失）
    try:
        from . import db_proactive
        if await db_proactive.is_micro_event_in_cooldown(user_id, event_text):
            # 同步到内存缓存（避免后续重复查 DB）
            event_key = event_text[:10]
            if event_key not in _MICRO_EVENT_HISTORY:
                _MICRO_EVENT_HISTORY[event_key] = []
            _MICRO_EVENT_HISTORY[event_key].append((user_id, time.time()))
            return False
    except Exception:
        pass

    return True


async def generate_micro_event(user_id: str) -> Optional[str]:
    """LLM 动态生成微事件（真人化 P2-1）。

    优先从 LLM 生成缓存取，缓存过期后使用模板池。
    返回 None 表示所有可用事件都在冷却期。

    注意：LLM 生成是异步的——首次缓存填充后，后续生成在后台进行。
    """
    # 1. 检查 LLM 缓存
    global _LLM_MICRO_EVENT_CACHE, _LLM_MICRO_EVENT_CACHE_EXPIRY
    now = time.time()
    if _LLM_MICRO_EVENT_CACHE and now < _LLM_MICRO_EVENT_CACHE_EXPIRY:
        # 从缓存中选择一个未对该用户冷却的事件
        available = [
            e for e in _LLM_MICRO_EVENT_CACHE
            if await is_micro_event_available(user_id, e)
        ]
        if available:
            event = random.choice(available)
            _record_micro_event_sent(event, user_id)
            return event

    # 2. Fallback: 从模板池选择（确保至少有一个可用）
    available_templates = [
        e for e in _MICRO_EVENTS
        if await is_micro_event_available(user_id, e)
    ]
    if available_templates:
        event = random.choice(available_templates)
        _record_micro_event_sent(event, user_id)
        return event

    # 3. 所有事件都在冷却期 → 触发 LLM 后台生成新事件
    _schedule_llm_micro_event_generation()

    # 4. 如果有新生成的 LLM 缓存事件可用（即使冷却未完全过期）
    if _LLM_MICRO_EVENT_CACHE:
        event = random.choice(_LLM_MICRO_EVENT_CACHE)
        _record_micro_event_sent(event, user_id)
        return event

    return None


def _schedule_llm_micro_event_generation():
    """调度 LLM 后台生成微事件（不阻塞主流程）。"""
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_generate_micro_events_via_llm())
        else:
            _asyncio.run(_generate_micro_events_via_llm())
    except RuntimeError:
        pass


async def _generate_micro_events_via_llm(count: int = 10) -> list:
    """使用 LLM 生成新的微事件（真人化 P2-1）。

    调用本地 LLM 生成自然、生活化的微事件描述，
    生成结果写入 _LLM_MICRO_EVENT_CACHE 并在 24h 内复用。

    Args:
        count: 要生成的事件数量（默认 10）

    Returns:
        生成的事件文本列表
    """
    global _LLM_MICRO_EVENT_CACHE, _LLM_MICRO_EVENT_CACHE_EXPIRY

    # 防止重复触发：如果缓存还新鲜，不生成
    now = time.time()
    if _LLM_MICRO_EVENT_CACHE and now < _LLM_MICRO_EVENT_CACHE_EXPIRY:
        return _LLM_MICRO_EVENT_CACHE

    # 构建 prompt
    existing_sample = random.sample(_MICRO_EVENTS, min(5, len(_MICRO_EVENTS)))
    prompt = (
        "你是一个日常生活丰富的真人。请生成一些「刚刚发生的小事」，语气自然、生活化，"
        "像是随口一提的感觉。\n\n"
        f"参考风格（不要照抄）：\n" + "\n".join(f"- {e}" for e in existing_sample) +
        f"\n\n请生成 {count} 条不同的微事件，每行一条，每条 10-25 字，用中文。"
        "不要机械重复参考样本的内容，要有新意。"
        "事件类型可以五花八门：意外/发现/感想/动作/环境/物品/身体/心情。"
    )

    new_events = []

    try:
        # 使用本地 LLM 生成
        from .local_llm import query_local_llm
        result = await query_local_llm(
            prompt=prompt,
            max_tokens=500,
            temperature=0.9,  # 高创造性
        )
        if result:
            # 解析生成结果：每行一条
            for line in result.strip().split("\n"):
                line = line.strip()
                # 去掉可能的编号前缀（如 "1. " 或 "1、"）
                if line and len(line) >= 8:
                    # 尝试去掉编号
                    import re
                    cleaned = re.sub(r'^[\d]+[\.\、\s]+', '', line)
                    if len(cleaned) >= 8:
                        new_events.append(cleaned)
                    elif len(line) >= 8:
                        new_events.append(line)
    except Exception:
        logger.debug("[行为引擎] LLM 微事件生成失败，使用模板池")

    # 如果生成成功，更新缓存
    if len(new_events) >= 3:
        _LLM_MICRO_EVENT_CACHE = new_events
        _LLM_MICRO_EVENT_CACHE_EXPIRY = now + _LLM_MICRO_EVENT_CACHE_TTL
        logger.info(f"[行为引擎] LLM 生成 {len(new_events)} 条新微事件")

    return new_events


async def refresh_micro_event_cache():
    """手动刷新 LLM 微事件缓存（供外部定时调用）。"""
    return await _generate_micro_events_via_llm(count=10)


def get_micro_event_behavior(trigger_chance: float = 0.02,
                              user_id: str = "") -> Optional[str]:
    """随机微事件（真人化 P2-1：含冷却期检查）。

    Args:
        trigger_chance: 触发概率
        user_id: 用户 ID（用于冷却期检查，空字符串则跳过冷却检查）
    """
    if not _MICRO_EVENTS:
        return None
    if random.random() > trigger_chance:
        return None

    # 真人化 P2-1：冷却期过滤（内存检查，同步）
    if user_id:
        available = [
            e for e in _MICRO_EVENTS
            if not _is_micro_event_in_cooldown(e, user_id)
        ]
        if not available:
            return None
        event = random.choice(available)
        _record_micro_event_sent(event, user_id)
        return event

    return random.choice(_MICRO_EVENTS)


async def get_micro_event_behavior_async(trigger_chance: float = 0.02,
                                          user_id: str = "") -> Optional[str]:
    """随机微事件（异步版本，含 DB 冷却期检查）。

    比同步版本更准确：综合检查内存缓存 + DB 持久化记录。
    推荐在异步上下文中使用此版本。

    Args:
        trigger_chance: 触发概率
        user_id: 用户 ID
    """
    if not _MICRO_EVENTS:
        return None
    if random.random() > trigger_chance:
        return None

    if user_id:
        # 真人化 P2-1：综合检查（内存 + DB）
        available = []
        for e in _MICRO_EVENTS:
            if await is_micro_event_available(user_id, e):
                available.append(e)
        if not available:
            return None
        event = random.choice(available)
        _record_micro_event_sent(event, user_id)
        return event

    return random.choice(_MICRO_EVENTS)


def get_micro_event_history() -> Dict[str, List[tuple]]:
    """获取微事件发送历史（用于测试和调试）。"""
    return dict(_MICRO_EVENT_HISTORY)


def clear_micro_event_history():
    """清除所有微事件历史（用于测试重置）。"""
    global _MICRO_EVENT_HISTORY
    _MICRO_EVENT_HISTORY = {}


# ============================================================
# 行为优先级定义（真人化 P2-4）
# ============================================================

BEHAVIOR_PRIORITY = {
    "weather": 7,
    "seasonal": 6,
    "holiday": 5,
    "scroll_feed": 4,
    "hot_topic": 3,
    "micro_event": 2,
    "random": 1,
}

# 优先级对应的概率权重（高优先级更大概率胜出，但不是绝对的）
_BEHAVIOR_PRIORITY_WEIGHTS = {
    7: 0.35,   # weather
    6: 0.20,   # seasonal
    5: 0.15,   # holiday
    4: 0.12,   # scroll_feed
    3: 0.08,   # hot_topic
    2: 0.06,   # micro_event
    1: 0.04,   # random
}


def _select_by_priority(candidates: list) -> Optional[str]:
    """按优先级选择一个行为（概率性地，高优先级更易胜出）。

    真人化 P2-4：替代随机 sample N 个合并的旧逻辑。
    每次只输出 1 个行为提示，避免「天气+节日+微事件」同时出现的生硬拼接。
    """
    if not candidates:
        return None

    if len(candidates) == 1:
        return candidates[0][1]  # (priority, text)

    # 按优先级排序（高→低）
    sorted_candidates = sorted(candidates, key=lambda x: x[0], reverse=True)
    max_priority = sorted_candidates[0][0]

    # 概率性地选择：最高优先级大概率胜出，但不绝对
    # 这样有时也会出现低优先级行为（更有"突然想到"的感觉）
    weights = [_BEHAVIOR_PRIORITY_WEIGHTS.get(p, 0.05) for p, _ in sorted_candidates]
    total = sum(weights)
    probs = [w / total for w in weights]
    idx = random.choices(range(len(sorted_candidates)), weights=probs, k=1)[0]

    selected_priority, selected_text = sorted_candidates[idx]
    logger.debug(
        f"[行为] 优先级选择: 候选{len(candidates)}个 "
        f"(最高优先级={max_priority}) → 选中优先级={selected_priority}"
    )
    return selected_text


# ============================================================
# 综合现实世界行为生成（真人化 P2-4 优先级链）
# ============================================================

def get_real_world_behavior(
    weather_condition: str = "",
    weather_temp: str = "",
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
    city: str = "",
    affection_score: float = 0.0,
    is_lightweight: bool = False,
    user_id: str = "",
) -> Optional[str]:
    """综合现实世界行为生成（真人化 P2-4 优先级链）。

    各行为独立判断命中与否，然后按优先级链概率性选择 1 个。
    高优先级（天气/季节）有更大概率胜出，但不绝对——
    低优先级行为偶尔也会出现，保持「突然想到」的自然感。

    替代旧版「累积模式随机选 N 个合并」的逻辑。
    """
    candidates: list = []  # [(priority, text), ...]

    # 1. 天气反应 (priority=7)
    weather_hint = get_weather_behavior(
        weather_condition, weather_temp,
        trigger_chance=config.BEHAVIOR_WEATHER_CHANCE, city=city
    )
    if weather_hint:
        city_prefix = f"（用户在{city}）" if city else ""
        candidates.append((BEHAVIOR_PRIORITY["weather"],
            f"你对天气的自然反应{city_prefix}：{weather_hint}。可以自然地表达出来。"))

    # 2. 季节愿望 (priority=6)
    seasonal = get_seasonal_wish(trigger_chance=config.BEHAVIOR_SEASONAL_CHANCE)
    if seasonal:
        candidates.append((BEHAVIOR_PRIORITY["seasonal"],
            f"你突然想到：{seasonal}。自然地流露出来。"))

    # 3. 节假日/特殊日期 (priority=5)
    holiday = get_holiday_behavior(trigger_chance=config.BEHAVIOR_HOLIDAY_CHANCE)
    if holiday:
        candidates.append((BEHAVIOR_PRIORITY["holiday"],
            f"今天是特殊的日子：{holiday}。自然地提及，不要刻意。"))

    # 4. 刷手机Feed引用 (priority=4)
    scroll_feed = get_scroll_behavior(trigger_chance=config.BEHAVIOR_SCROLL_CHANCE)
    if scroll_feed:
        candidates.append((BEHAVIOR_PRIORITY["scroll_feed"], scroll_feed))

    # 5. 热点话题 (priority=3)
    hot_topic = get_hot_topic_behavior(trigger_chance=config.BEHAVIOR_HOT_TOPIC_CHANCE)
    if hot_topic:
        candidates.append((BEHAVIOR_PRIORITY["hot_topic"],
            f"你刚看到：{hot_topic}。可以随口提一下。"))

    # 6. 微事件 (priority=2, 真人化 P2-1：含冷却期)
    micro_chance = config.BEHAVIOR_LIGHT_MICRO_EVENT_CHANCE if is_lightweight else config.BEHAVIOR_MICRO_EVENT_CHANCE
    micro = get_micro_event_behavior(trigger_chance=micro_chance, user_id=user_id)
    if micro:
        candidates.append((BEHAVIOR_PRIORITY["micro_event"],
            f"刚刚发生了一个小事：{micro}。随口提一句，不超过一句话。"))

    # 7. 随机行为 (priority=1)
    random_behavior = get_random_behavior(
        schedule_period, bot_mood_dominant,
        trigger_chance=config.BEHAVIOR_RANDOM_CHANCE, affection_score=affection_score
    )
    if random_behavior:
        candidates.append((BEHAVIOR_PRIORITY["random"],
            f"你突然{random_behavior['type']}：{random_behavior['text']}。"))

    return _select_by_priority(candidates)


# ============================================================
# 行为模式提示生成（供 prompt 注入）
# ============================================================

def get_behavior_hint(
    weather_condition: str = "",
    weather_temp: str = "",
    schedule_period: str = "active",
    bot_mood_dominant: str = "平静",
    city: str = "",
    affection_score: float = 0.0,
    user_id: str = "",
) -> Optional[str]:
    """综合生成行为模式提示，供 prompt 注入。

    委托给 get_real_world_behavior() 提供多层级优先级链。
    """
    return get_real_world_behavior(
        weather_condition, weather_temp,
        schedule_period, bot_mood_dominant, city,
        affection_score=affection_score,
        is_lightweight=False,
        user_id=user_id,
    )


# ============================================================
# 轻量行为注入（短消息用）
# ============================================================

def get_lightweight_behavior_hint(
    weather_condition: str = "",
    weather_temp: str = "",
    schedule_period: str = "active",
    affection_score: float = 0.0,
    city: str = "",
) -> Optional[str]:
    """短消息轻量行为注入——不跑全量分析但给一点生活感。

    真人化 P2-4：使用优先级选择替代 random.choice。
    三层独立判断后按优先级链（天气 > 季节 > 微事件）概率性选择 1 个。

    返回最多 1 个行为提示（短消息不宜信息过载）。
    """
    candidates: list = []  # [(priority, text), ...]

    # 天气反应 (priority=7, 最高)
    weather = get_weather_behavior(
        weather_condition, weather_temp,
        trigger_chance=config.BEHAVIOR_LIGHT_WEATHER_CHANCE, city=city
    )
    if weather:
        candidates.append((BEHAVIOR_PRIORITY["weather"], f"天气感受：{weather}"))

    # 季节愿望 (priority=6)
    seasonal = get_seasonal_wish(trigger_chance=config.BEHAVIOR_LIGHT_SEASONAL_CHANCE)
    if seasonal:
        candidates.append((BEHAVIOR_PRIORITY["seasonal"], f"突然想到：{seasonal}"))

    # 微事件 (priority=2, 短消息最合适的自然流露)
    micro = get_micro_event_behavior(trigger_chance=config.BEHAVIOR_LIGHT_MICRO_EVENT_CHANCE)
    if micro:
        candidates.append((BEHAVIOR_PRIORITY["micro_event"], f"随口一提：{micro}"))

    return _select_by_priority(candidates)
