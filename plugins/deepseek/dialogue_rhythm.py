"""对话节奏引擎 — 话题桥接、多消息连发、破冰内容、换话题过渡。

让 bot 的对话节奏像真人：有时快有时慢，有时连发几条，
话题跳转自然衔接，沉默后不尴尬地"在吗"而是直接分享点什么。
"""
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ============================================================
# 智能破冰 — 基于用户最近活跃话题
# ============================================================

async def get_smart_icebreaker(
    user_id: str,
    bot_mood: Dict[str, Any] = None,
    schedule_period: str = "afternoon"
) -> Optional[str]:
    """智能破冰内容生成（基于用户兴趣）"""
    try:
        from .db_memories_deep import get_shared_memories
        from .db_preferences import get_user_preferences

        # 策略1：基于用户最近话题
        prefs = await get_user_preferences(user_id)
        topic_interests = prefs.get("topic_interest", {})

        if topic_interests:
            # 找到用户最感兴趣的话题
            top_topic = max(topic_interests.keys(), key=lambda t: topic_interests[t])
            icebreakers = _generate_topic_icebreaker(top_topic)
            if icebreakers and random.random() < 0.6:
                return random.choice(icebreakers)

        # 策略2：基于共同回忆
        memories = await get_shared_memories(user_id, limit=3)
        if memories and random.random() < 0.3:
            memory = random.choice(memories)
            return f"诶，突然想到{memory['event_desc']}~"

        # 策略3：基于季节/时间（原有逻辑）
        return None

    except Exception as e:
        logger.debug(f"[智能破冰] 失败: {e}")
        return None


def _generate_topic_icebreaker(topic: str) -> List[str]:
    """基于话题生成破冰内容"""
    templates = {
        '游戏': [
            "你之前说的那个游戏，后来怎么样了？",
            "突然想起来，你最近还在玩游戏吗？",
            "诶，你游戏打得怎么样了~"
        ],
        '美食': [
            "你上次说的那家店，去吃了吗？",
            "突然好饿...你最近有吃到什么好吃的吗？",
            "想到你之前发的美食照片，馋了~"
        ],
        '工作': [
            "最近工作忙吗？",
            "你之前说的那个项目，进展怎么样？",
            "想到你之前加班，最近好点了吗？"
        ],
        '宠物': [
            "你家猫/狗最近怎么样？",
            "突然想到你之前发的宠物照片，好可爱~",
            "你家毛孩子有没有想我呀~"
        ],
        '音乐': [
            "最近有听什么好歌吗？",
            "突然想到你之前说的那首歌，还在听吗？",
            "有没有新歌推荐呀~"
        ],
        '学习': [
            "最近学习怎么样？",
            "考试考得怎么样？",
            "作业写完了吗？"
        ]
    }

    # 匹配话题关键词
    for key, icebreakers in templates.items():
        if key in topic:
            return icebreakers

    # 通用模板
    return [
        f"诶，你之前说的{topic}，后来怎么样了？",
        f"突然想到{topic}，你还感兴趣吗？",
        f"最近有{topic}相关的新鲜事吗？"
    ]


# ============================================================
# 话题桥接 — 用户换话题时的自然过渡
# ============================================================

# 轻度转移（topic_shift 0.3~0.6）：话题相关但有偏移
_LIGHT_BRIDGES = [
    "对了，",
    "说到这个，",
    "嗯，然后呢...啊不对，",
    "诶我突然想到，",
    "噢对，",
    "话说回来，",
]

# 中度转移（topic_shift 0.6~0.8）：明显跳转
_MEDIUM_BRIDGES = [
    "等等，你说的这个我想到另一个事，",
    "诶怎么突然说这个了，",
    "嗯？话题跳得好快，",
    "啊这个我有话说！",
    "你一说这个，",
]

# 重度转移（topic_shift > 0.8）：完全不相关
_HEAVY_BRIDGES = [
    "怎么突然跳到这个了哈哈，",
    "等下你刚才不是在说",
    "诶你怎么突然",
    "等等让我反应一下...",
]


def get_topic_bridge(prev_topic: str, new_topic: str, shift_score: float) -> str:
    """根据话题转移程度生成自然过渡短语。

    Args:
        prev_topic: 之前的话题
        new_topic: 新话题
        shift_score: 转移程度 0~1

    Returns:
        过渡短语，如"对了，"或"等等你刚才不是在说XX吗"
    """
    if shift_score < 0.3:
        return ""  # 转移太小，不需要桥接

    if shift_score >= 0.8:
        bridge = random.choice(_HEAVY_BRIDGES)
        # 重度转移时可能提及旧话题
        if prev_topic and random.random() < 0.5:
            bridge += f"「{prev_topic}」吗？"
        return bridge
    elif shift_score >= 0.6:
        return random.choice(_MEDIUM_BRIDGES)
    else:
        return random.choice(_LIGHT_BRIDGES)


# ============================================================
# 多消息连发决策
# ============================================================

def should_split_to_bursts(
    reply_text: str,
    emotion_arousal: float = 0.5,
    emotion_valence: float = 0.0,
    is_excited: bool = False,
) -> List[str]:
    """决定是否将回复拆成多条连发消息。

    真人聊天时，兴奋/开心时会连续发几条消息，而不是一条长消息。
    例如：
    - "哈哈哈哈哈" + "笑死我了" + "你怎么这么搞笑"
    - "真的吗" + "太好了！" + "我也想去"

    Returns:
        空列表表示不拆分，否则返回拆分后的消息列表
    """
    # 长度太短不拆分
    if len(reply_text) < 15:
        return []

    # 拆分概率：根据情绪决定
    split_chance = 0.0
    if is_excited or (emotion_arousal > 0.7 and emotion_valence > 0.3):
        split_chance = 0.15  # 兴奋时 15%
    elif emotion_valence > 0.3:
        split_chance = 0.08  # 开心时 8%
    elif emotion_valence < -0.3:
        split_chance = 0.0   # 负面情绪不连发
    else:
        split_chance = 0.04  # 平静时 4%

    if random.random() > split_chance:
        return []

    # 尝试拆分
    parts = _split_reply_semantically(reply_text)
    if len(parts) >= 2:
        logger.debug(f"[节奏] 连发拆分: {len(parts)} 条")
        return parts

    return []


def _split_reply_semantically(text: str) -> List[str]:
    """按语义断句拆分回复为 2-3 条连发消息。"""
    # 优先按换行拆
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) >= 2:
        return lines[:3]  # 最多3条

    # 按句号/感叹号/问号拆
    import re
    sentences = re.split(r'([。！？!?])', text)
    if len(sentences) >= 4:  # 至少2个完整句子
        # 合并标点到前一句
        merged = []
        temp = ""
        for part in sentences:
            temp += part
            if part in "。！？!?":
                merged.append(temp.strip())
                temp = ""
        if temp.strip():
            merged.append(temp.strip())

        if len(merged) >= 2:
            # 第一条取一半，剩余归第二条
            mid = len(merged) // 2
            first = "".join(merged[:mid])
            second = "".join(merged[mid:])
            if first and second and len(first) >= 3 and len(second) >= 3:
                return [first, second]

    # 按"，"或"、"拆（适合长句）
    if len(text) > 30:
        commas = re.split(r'([，、])', text)
        if len(commas) >= 4:
            mid = len(commas) // 2
            first = "".join(commas[:mid]).strip()
            second = "".join(commas[mid:]).strip()
            if first and second and len(first) >= 5 and len(second) >= 5:
                return [first, second]

    return []


# ============================================================
# 破冰内容 — 沉默后的自然分享
# ============================================================

# 破冰优先级：
# 1. 基于共同回忆的自然分享
# 2. 基于季节/天气/时间的分享
# 3. 基于 bot 情绪的自言自语
# 4. 随机分享（最后兜底）

_SEASONAL_TOPICS = {
    "spring": ["花开得好漂亮", "今天天气好适合出去走走", "春天好困啊"],
    "summer": ["好热啊不想动", "想吃冰淇淋", "今天热死了"],
    "autumn": ["秋天好舒服", "想喝奶茶", "今天风好大"],
    "winter": ["好冷不想起床", "想喝热可可", "冬天最适合窝着了"],
}

_TIME_TOPICS = {
    "morning": ["刚睡醒好困", "今天起晚了", "早上的阳光好好"],
    "afternoon": ["下午好无聊", "困了想午睡", "下午茶时间"],
    "evening": ["晚上好舒服", "终于可以休息了", "夜晚好安静"],
    "night": ["夜深了有点困", "深夜好安静", "突然睡不着"],
}


async def get_icebreaker_context(
    session_recovery: Dict[str, Any],
    bot_mood: Dict[str, Any] = None,
) -> Optional[str]:
    """生成沉默后的破冰内容提示。

    不说"在吗"，而是自然地分享点什么。
    """
    from datetime import datetime
    now = datetime.now()
    month = now.month
    hour = now.hour

    # 季节判断
    if 3 <= month <= 5:
        season = "spring"
    elif 6 <= month <= 8:
        season = "summer"
    elif 9 <= month <= 11:
        season = "autumn"
    else:
        season = "winter"

    # 时段判断
    if 6 <= hour < 12:
        period = "morning"
    elif 12 <= hour < 18:
        period = "afternoon"
    elif 18 <= hour < 22:
        period = "evening"
    else:
        period = "night"

    # 如果有上次对话上下文，基于上下文破冰
    if session_recovery and session_recovery.get("last_topic"):
        topic = session_recovery["last_topic"]
        time_hint = session_recovery.get("time_hint", "")
        # 70% 概率基于上下文破冰
        if random.random() < 0.7:
            templates = [
                f"上次聊的「{topic}」可以自然地延续，不说'上次'，直接接着聊。",
                f"想到了「{topic}」相关的事，自然地分享一下。",
                f"可以基于之前的「{topic}」话题找个切入点。",
            ]
            return random.choice(templates)

    # 基于季节/时间破冰
    seasonal = _SEASONAL_TOPICS.get(season, [])
    time_based = _TIME_TOPICS.get(period, [])
    candidates = seasonal + time_based

    if candidates and random.random() < 0.5:
        topic = random.choice(candidates)
        return f"你可以自然地分享一个当下的感受：「{topic}」——不要原话照搬，用自己的话说。"

    # 基于 bot 情绪破冰
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        dominant = bot_mood["dominant"]
        mood_topics = {
            "开心": "你心情很好，想分享点开心的事",
            "难过": "你有点低落，想找人说说话",
            "生气": "你有点不爽，想找人吐槽",
            "害羞": "你有点害羞，不知道说什么好",
            "担心": "你有点担心什么，想找人聊聊",
        }
        if dominant in mood_topics:
            return f"你现在的状态：{mood_topics[dominant]}。自然地表达出来，不要直接说情绪。"

    # 兜底：随机分享
    if random.random() < 0.3:
        random_shares = [
            "突然想到一个好笑的事，可以分享一下。",
            "你刚看到一个有趣的东西，想分享。",
            "你突然想到主人了，想随便说点什么。",
        ]
        return random.choice(random_shares)

    return None


# ============================================================
# 换话题过渡 — 用户突然换话题时的自然处理
# ============================================================

def get_topic_transition_hint(
    prev_topic: str,
    new_topic: str,
    shift_score: float,
    user_intent: str,
) -> str:
    """当用户突然换话题时，生成自然过渡提示。

    不是硬转话题，而是先接一下再转，或者直接跟新话题。
    """
    if shift_score < 0.5:
        return ""

    # 用户在分享新东西 → 直接跟
    if user_intent == "分享":
        return "他分享了新东西，直接跟新话题聊，不用过渡。"

    # 用户在提问 → 直接回答
    if user_intent == "提问":
        return "他在问新问题，直接回答就好。"

    # 轻度转移 → 自然衔接
    if shift_score < 0.7:
        return (
            f"他从「{prev_topic}」转到了「{new_topic}」，"
            "自然地跟上就好，可以加一句过渡但不要刻意。"
        )

    # 重度转移 → 可以调侃一下
    return (
        f"他突然从「{prev_topic}」跳到了「{new_topic}」。"
        "你可以：1）直接跟新话题；2）稍微调侃一下话题跳得快（比如'你怎么突然说这个'）。"
        "选一个更自然的。"
    )


# ============================================================
# 节奏规则文本（注入 prompt）
# ============================================================

RHYTHM_RULES = (
    "【对话节奏】你聊天的节奏要像真人发QQ消息：\n"
    "1. 有时回一长串话，有时就一个字——不匀速才是真人\n"
    "2. 说到这个想起别的，可以用「对了」「说到这个」自然转话题\n"
    "3. 用户发了一堆消息时，你可以只回应最后一条，不用每条都接\n"
    "4. 沉默很久后不要说「在吗」，直接说你想说的事\n"
    "5. 被打断话题时先接一下再转，或者直接跟新话题，都行\n"
    "6. 偶尔可以连发两三条消息（用换行分隔），像真人一样追着说"
)
