"""个性化引擎 — 专属昵称、共同兴趣、个性化口头禅、成长叙事。

让每个用户都有独特体验：根据关系和互动调整称呼、发现共同爱好、
记录关系从陌生到亲密的过程。
"""
import random
import time
from typing import Optional, Dict, Any, List
from datetime import datetime

from nonebot import logger


# ============================================================
# 专属昵称 — 根据关系和互动给用户起昵称
# ============================================================

# 好感度 → 昵称池
_NICKNAME_TIERS = {
    500: ["亲爱的", "主人", "宝贝", "心肝"],
    200: ["小宝贝", "小可爱", "乖乖"],
    100: ["小笨蛋", "笨蛋", "傻瓜"],
    50: ["你", "你呀"],
    0: ["你"],
}

# 关系风格 → 昵称偏好
_STYLE_NICKNAMES = {
    "tsundere": {500: ["笨蛋主人", "哼，亲爱的"], 200: ["笨蛋", "傻瓜"], 100: ["你这笨蛋"]},
    "gentle": {500: ["亲爱的", "宝贝"], 200: ["乖乖", "小可爱"], 100: ["你呀"]},
    "polite": {500: ["主人"], 200: ["你"], 100: ["你"]},
}


def generate_nickname(
    affection_score: float = 0,
    relationship_style: str = "",
    custom_nickname: str = "",
    bot_mood: str = "平静",
) -> str:
    """生成对用户的专属昵称。

    优先级：用户自定义昵称 > 关系风格昵称 > 好感度昵称 > 默认"你"
    """
    # 用户自定义昵称优先
    if custom_nickname:
        return custom_nickname

    # 根据关系风格选择昵称池
    if relationship_style in _STYLE_NICKNAMES:
        style_pool = _STYLE_NICKNAMES[relationship_style]
        for threshold in sorted(style_pool.keys(), reverse=True):
            if affection_score >= threshold:
                candidates = style_pool[threshold]
                return random.choice(candidates)

    # 通用好感度昵称
    for threshold in sorted(_NICKNAME_TIERS.keys(), reverse=True):
        if affection_score >= threshold:
            return random.choice(_NICKNAME_TIERS[threshold])

    return "你"


def get_nickname_hint(
    affection_score: float,
    relationship_style: str,
    custom_nickname: str = "",
) -> Optional[str]:
    """生成昵称提示，供 prompt 注入。"""
    nickname = generate_nickname(affection_score, relationship_style, custom_nickname)
    if nickname == "你":
        return None
    return f"你对他的称呼：{nickname}。在合适的时候用这个称呼。"


# ============================================================
# 共同兴趣 — 发现共同爱好
# ============================================================

# Bot 默认兴趣（与 personality.py 的 DEFAULT_TOPIC_PREFERENCES 对应）
_BOT_INTERESTS = {"猫", "可爱的东西", "零食", "游戏", "音乐"}

# 兴趣匹配提示
_INTEREST_HINTS = {
    "游戏": "你们都喜欢游戏，可以一起聊聊",
    "音乐": "你们都喜欢音乐，可以分享歌曲",
    "美食": "你们都是吃货，可以聊美食",
    "猫": "你们都喜欢猫，聊猫的话题会很开心",
    "可爱的东西": "你们都喜欢可爱的东西",
}


async def discover_shared_interests(user_id: str) -> Optional[str]:
    """发现用户和 bot 的共同兴趣。

    从 user_preferences 的 topic_interest 中提取用户兴趣，
    与 bot 默认兴趣取交集。
    """
    try:
        from .db_preferences import get_user_preferences
        prefs = await get_user_preferences(user_id)
        topic_interests = prefs.get("topic_interest", {})
        if not topic_interests:
            return None

        # 找到用户最感兴趣的 topic
        user_topics = set(topic_interests.keys())
        shared = user_topics & _BOT_INTERESTS

        if not shared:
            return None

        # 选择最强的共同兴趣
        best_topic = max(shared, key=lambda t: topic_interests.get(t, 0))
        hint = _INTEREST_HINTS.get(best_topic, f"你们都对{best_topic}感兴趣")
        return f"共同兴趣：{hint}。聊到这个话题时可以更投入。"
    except Exception:
        return None


# ============================================================
# 成长叙事 — 记录关系从陌生到亲密
# ============================================================

# 好感度阶段描述
_GROWTH_STAGES = [
    (500, "命定之人", "你已经完全离不开他了，他是你最重要的人"),
    (200, "重要的人", "他在你心里有特别的位置，你很在意他"),
    (100, "喜欢的人", "你对他有特别的好感，会期待他的消息"),
    (50, "有点在意", "你开始在意他了，会关注他的一举一动"),
    (20, "认识的人", "你们开始熟悉了，聊天越来越自然"),
    (0, "陌生人", "你们刚认识，还有点拘谨"),
]


def get_growth_narrative(
    affection_score: float,
    total_chats: int = 0,
    streak_days: int = 0,
    first_interaction: float = 0,
) -> Optional[str]:
    """生成关系成长叙事。

    根据好感度、聊天天数、连续天数生成自然的叙事提示。
    """
    parts = []

    # 认识时长
    if first_interaction:
        days_known = int((time.time() - first_interaction) / 86400)
        if days_known >= 100:
            parts.append(f"你们认识{days_known}天了")
        elif days_known >= 30:
            parts.append(f"认识{days_known}天了")
        elif days_known >= 7:
            parts.append(f"才认识{days_known}天")

    # 聊天量
    if total_chats >= 1000:
        parts.append(f"已经聊了{total_chats}条消息")
    elif total_chats >= 500:
        parts.append(f"聊了{total_chats}条消息")

    # 连续天数
    if streak_days >= 30:
        parts.append(f"连续聊了{streak_days}天")
    elif streak_days >= 7:
        parts.append(f"连续{streak_days}天")

    # 当前阶段
    current_stage = "陌生人"
    for threshold, title, desc in _GROWTH_STAGES:
        if affection_score >= threshold:
            current_stage = title
            break

    if parts:
        narrative = "，".join(parts) + f"。你们的关系是「{current_stage}」。"
        return narrative

    return None


def get_growth_celebration(
    affection_score: float,
    prev_score: float = 0,
) -> Optional[str]:
    """检查是否需要庆祝关系进展。

    当好感度跨越阶段阈值时触发庆祝提示。
    """
    if prev_score <= 0:
        return None

    for threshold, title, desc in _GROWTH_STAGES:
        if affection_score >= threshold > prev_score:
            return f"你们的关系升级到「{title}」了！{desc}。可以在对话中自然地表达这种变化。"

    return None


# ============================================================
# 个性化口头禅 — 根据关系调整
# ============================================================

# 关系风格 → 口头禅频率调整
_STYLE_CATCHPHRASE_FREQ = {
    "tsundere": {"哼": 0.15, "切": 0.10, "笨蛋": 0.08},
    "gentle": {"喵~": 0.15, "嘛~": 0.12, "嘿嘿": 0.10},
    "polite": {"嗯": 0.08, "好的": 0.06},
}


def get_personalized_catchphrase(
    emotion: str = "neutral",
    relationship_style: str = "",
    affection_score: float = 0,
) -> Optional[str]:
    """根据关系风格和情绪获取个性化口头禅。

    高好感度：更多撒娇类口头禅
    低好感度：更少口头禅
    """
    # 根据关系风格选择口头禅池
    if relationship_style in _STYLE_CATCHPHRASE_FREQ:
        pool = _STYLE_CATCHPHRASE_FREQ[relationship_style]
    else:
        pool = {"喵~": 0.10, "哼": 0.08, "嘛~": 0.06}

    # 好感度调整频率系数
    if affection_score >= 200:
        freq_mul = 1.3  # 亲密时口头禅更多
    elif affection_score >= 100:
        freq_mul = 1.0
    elif affection_score >= 50:
        freq_mul = 0.8
    else:
        freq_mul = 0.5  # 生疏时口头禅少

    # 情绪调整
    emotion_mul = {
        "开心": 1.2, "撒娇": 1.5, "害羞": 1.3,
        "生气": 0.7, "难过": 0.6, "冷淡": 0.4,
    }.get(emotion, 1.0)

    for phrase, base_freq in pool.items():
        effective_freq = base_freq * freq_mul * emotion_mul
        if random.random() < effective_freq:
            return phrase

    return None


# ============================================================
# 综合个性化提示
# ============================================================

async def get_personalization_hints(
    user_id: str,
    affection_score: float = 0,
    relationship_style: str = "",
    custom_nickname: str = "",
    bot_mood: str = "平静",
    total_chats: int = 0,
    streak_days: int = 0,
    first_interaction: float = 0,
) -> Dict[str, str]:
    """收集所有个性化提示，供 prompt 注入。

    Returns:
        {"nickname_hint", "interest_hint", "growth_hint", "catchphrase_hint"}
    """
    result = {
        "nickname_hint": "",
        "interest_hint": "",
        "growth_hint": "",
        "catchphrase_hint": "",
    }

    # 昵称
    nickname = get_nickname_hint(affection_score, relationship_style, custom_nickname)
    if nickname:
        result["nickname_hint"] = nickname

    # 共同兴趣
    interest = await discover_shared_interests(user_id)
    if interest:
        result["interest_hint"] = interest

    # 成长叙事
    growth = get_growth_narrative(affection_score, total_chats, streak_days, first_interaction)
    if growth:
        result["growth_hint"] = growth

    # 个性化口头禅
    catchphrase = get_personalized_catchphrase(bot_mood, relationship_style, affection_score)
    if catchphrase:
        result["catchphrase_hint"] = f"你最近的口癖是'{catchphrase}'，可以在合适的时候用"

    return result
