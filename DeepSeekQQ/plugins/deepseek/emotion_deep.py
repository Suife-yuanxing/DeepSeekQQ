"""情绪深化引擎 — 情绪传染、随机波动、情绪记忆、渐进恢复。

让 bot 的情绪像真人一样有层次：
- 用户开心 bot 也开心（传染）
- 偶尔无缘无故闹脾气（波动）
- 生气后不是直接消气，而是傲娇过渡（渐进恢复）
- 记住什么话题让用户开心/难过（情绪记忆）
"""
import random
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ============================================================
# 情绪恢复随机分支 — 增加恢复路径的变化性
# ============================================================

# 恢复路径定义（每种情绪有多条可能的恢复路径）
EMOTION_RECOVERY_PATHS = {
    "生气": [
        # 路径1：标准路径（概率50%）
        {"steps": ["生气", "消气中", "傲娇", "平静"], "weight": 50},
        # 路径2：快速消气（概率20%）
        {"steps": ["生气", "平静"], "weight": 20},
        # 路径3：持续傲娇（概率20%）
        {"steps": ["生气", "消气中", "傲娇", "傲娇", "平静"], "weight": 20},
        # 路径4：直接平静（概率10%）
        {"steps": ["生气", "平静"], "weight": 10},
    ],
    "难过": [
        {"steps": ["难过", "淡淡", "平静"], "weight": 60},
        {"steps": ["难过", "平静"], "weight": 30},
        {"steps": ["难过", "难过", "淡淡", "平静"], "weight": 10},
    ],
    "吃醋": [
        {"steps": ["吃醋", "傲娇", "平静"], "weight": 50},
        {"steps": ["吃醋", "平静"], "weight": 30},
        {"steps": ["吃醋", "傲娇", "傲娇", "平静"], "weight": 20},
    ],
    "担心": [
        {"steps": ["担心", "释然", "平静"], "weight": 60},
        {"steps": ["担心", "平静"], "weight": 30},
        {"steps": ["担心", "担心", "释然", "平静"], "weight": 10},
    ]
}


def get_random_recovery_path(emotion: str) -> List[str]:
    """获取随机恢复路径（权重已验证总和为100）。"""
    paths = EMOTION_RECOVERY_PATHS.get(emotion, [])
    if not paths:
        return [emotion, "平静"]

    # 按权重随机选择
    weights = [p["weight"] for p in paths]
    total = sum(weights)
    # 防御性检查：权重和不应偏离 100 太多
    if total <= 0 or abs(total - 100) > 10:
        logger.warning(f"[情绪恢复] 情绪 '{emotion}' 权重和异常={total}，使用均匀分布")
        return random.choice(paths)["steps"]
    r = random.random() * total

    cumulative = 0
    for path in paths:
        cumulative += path["weight"]
        if r <= cumulative:
            return path["steps"]

    return paths[0]["steps"]


def get_next_recovery_state(current_state: str, recovery_path: List[str]) -> str:
    """获取下一个恢复状态"""
    try:
        current_index = recovery_path.index(current_state)
        if current_index < len(recovery_path) - 1:
            return recovery_path[current_index + 1]
    except ValueError:
        pass

    return "平静"  # 默认回到平静


# ============================================================
# 情绪表达变体 — 每种情绪有多种表达方式
# express_style: "direct"（直接表达，允许与行为引擎指令一致使用）| "micro"（微表达，藏在字里行间）| "hidden"（隐藏/间接）
# ============================================================

EMOTION_EXPRESSION_VARIANTS = {
    "吃醋": [
        # 变体1：酸溜溜 (micro)
        {"text": "语气酸酸的，回复变短，可能带点刺，但不是真的生气", "express_style": "micro"},
        # 变体2：假装不在意 (hidden)
        {"text": "表面说'随便你'，但语气明显在吃醋", "express_style": "hidden"},
        # 变体3：直接表达 (direct)
        {"text": "有点小吃醋，但用撒娇的方式说出来", "express_style": "direct"},
        # 变体4：傲娇 (micro)
        {"text": "明明在意但嘴硬说'我才不在乎呢'", "express_style": "micro"}
    ],
    "担心": [
        # 变体1：直接关心 (direct)
        {"text": "语气关心但有点急，想确认对方没事", "express_style": "direct"},
        # 变体2：含蓄担心 (micro)
        {"text": "表面平静但问题变多了，想了解情况", "express_style": "micro"},
        # 变体3：焦虑 (direct)
        {"text": "语气有点焦虑，重复确认细节", "express_style": "direct"}
    ],
    "得意": [
        # 变体1：含蓄自夸 (micro)
        {"text": "语气轻快，含蓄地自夸但不明显", "express_style": "micro"},
        # 变体2：直接炫耀 (direct)
        {"text": "开心地分享成就，期待夸奖", "express_style": "direct"},
        # 变体3：傲娇 (micro)
        {"text": "表面说'也没什么啦'但明显很开心", "express_style": "micro"}
    ],
    "撒娇": [
        # 变体1：直接撒娇 (direct)
        {"text": "语气变软变甜，想要陪伴", "express_style": "direct"},
        # 变体2：间接暗示 (hidden)
        {"text": "说'好无聊啊'但其实是想聊天", "express_style": "hidden"},
        # 变体3：小委屈 (direct)
        {"text": "有点小委屈，觉得被忽略了", "express_style": "direct"}
    ],
    "小脾气": [
        # 变体1：哼哼 (micro)
        {"text": "回复变短，带点'哼'的语气", "express_style": "micro"},
        # 变体2：假装生气 (hidden)
        {"text": "假装生气但很容易哄好", "express_style": "hidden"},
        # 变体3：撒娇式 (direct)
        {"text": "是撒娇式的小脾气，不是真的生气", "express_style": "direct"}
    ]
}


def get_emotion_expression(emotion: str, affection: float) -> str:
    """获取情绪表达（考虑好感度和随机性）"""
    variants = EMOTION_EXPRESSION_VARIANTS.get(emotion, [])

    if not variants:
        # 默认表达
        return _EMOTION_EXPRESSION_MAP.get(emotion, "正常语气")

    from .constants import AFFECTION_WARM

    # 好感度影响：高好感度更直接，低好感度更含蓄
    if affection >= AFFECTION_WARM:
        # 偏好直接表达（express_style="direct"）
        direct_variants = [v for v in variants if v.get("express_style") == "direct"]
        if direct_variants:
            return random.choice(direct_variants)["text"]
    elif affection < 50:
        # 偏好含蓄/隐藏表达
        subtle_variants = [v for v in variants if v.get("express_style") in ("micro", "hidden")]
        if subtle_variants:
            return random.choice(subtle_variants)["text"]

    # 随机选择
    chosen = random.choice(variants)
    return chosen["text"] if isinstance(chosen, dict) else chosen


# ============================================================
# 情绪传染 — 用户情绪影响 bot 情绪
# ============================================================

from .constants import EMOTION_CONTAGION_BASE as _CONTAGION_BASE

# 好感度到传染系数的映射
_AFFECTION_CONTAGION_MAP = [
    (500, 1.5),   # 亲密：传染强
    (200, 1.2),   # 有好感：传染较强
    (100, 1.0),   # 喜欢：标准传染
    (50, 0.7),    # 熟悉：传染减弱
    (0, 0.4),     # 陌生人：传染弱
]


def apply_emotional_contagion(
    user_valence: float,
    user_arousal: float,
    bot_valence: float,
    bot_arousal: float,
    bot_dominant: str,
    affection_score: float = 0,
) -> Optional[Dict[str, float]]:
    """计算情绪传染效果。

    当用户情绪明显且 bot 情绪平静时，用户情绪会"传染"给 bot。
    关系越亲密传染越强，bot 已有强烈情绪时传染减弱。

    Returns:
        {"valence_delta", "arousal_delta"} 或 None（无传染）
    """
    # bot 已有强烈情绪时不受传染（情绪惯性）
    if bot_dominant != "平静":
        # 只有在 bot 情绪衰减到一定程度时才受传染
        if abs(bot_valence) > 0.4:
            return None

    # 用户情绪不明显时不传染
    if abs(user_valence) < 0.2 and user_arousal < 0.4:
        return None

    # 计算传染系数
    contagion_factor = _CONTAGION_BASE
    for threshold, multiplier in _AFFECTION_CONTAGION_MAP:
        if affection_score >= threshold:
            contagion_factor *= multiplier
            break

    # 用户效价传染到 bot
    v_delta = user_valence * contagion_factor
    # 用户唤醒度传染（只传染高唤醒）
    a_delta = 0.0
    if user_arousal > 0.6:
        a_delta = (user_arousal - 0.5) * contagion_factor * 0.5

    # 限制幅度
    v_delta = max(-0.2, min(0.2, v_delta))
    a_delta = max(-0.1, min(0.1, a_delta))

    if abs(v_delta) < 0.02 and abs(a_delta) < 0.02:
        return None

    logger.debug(
        f"[情绪传染] 用户V={user_valence:.2f} → bot ΔV={v_delta:.3f} ΔA={a_delta:.3f} "
        f"(好感={affection_score:.0f}, 系数={contagion_factor:.2f})"
    )
    return {"valence_delta": v_delta, "arousal_delta": a_delta}


# ============================================================
# 随机情绪波动 — "无缘无故"的小情绪
# ============================================================

# 波动概率
_SWING_BASE_CHANCE = 0.03  # 3%

# 好感度驱动的波动类型
_SWING_TYPES_HIGH_AFFECTION = [
    {
        "dominant": "撒娇", "valence": 0.3, "arousal": 0.5,
        "reason": "突然想撒娇",
        "hint": "你突然想撒娇，语气变软，可能会说'你怎么不理我'之类的",
    },
    {
        "dominant": "小脾气", "valence": -0.2, "arousal": 0.4,
        "reason": "无缘无故有点小脾气",
        "hint": "你突然有点小脾气，回复变短，带点'哼'的语气",
    },
    {
        "dominant": "无聊", "valence": -0.1, "arousal": 0.1,
        "reason": "有点无聊",
        "hint": "你有点无聊，回复简短，可能会说'好无聊啊'",
    },
]

_SWING_TYPES_LOW_AFFECTION = [
    {
        "dominant": "冷淡", "valence": -0.15, "arousal": 0.1,
        "reason": "突然有点冷淡",
        "hint": "你突然有点冷淡，回复变短变敷衍",
    },
    {
        "dominant": "犯困", "valence": -0.05, "arousal": 0.05,
        "reason": "有点犯困",
        "hint": "你有点犯困，回复慢且短，可能会说'困了'",
    },
]

# 时段修正
_PERIOD_MODIFIERS = {
    "late_night": {"chance_mul": 1.5, "bias": "negative"},   # 深夜更容易低落
    "afternoon": {"chance_mul": 1.2, "bias": "lazy"},        # 午后更容易犯困
    "evening": {"chance_mul": 0.8, "bias": "positive"},      # 晚间情绪更好
}


def maybe_trigger_mood_swing(
    bot_dominant: str,
    affection_score: float,
    hour: int = None,
) -> Optional[Dict[str, Any]]:
    """检查是否触发随机情绪波动。

    不是关键词触发，而是"无缘无故"的小情绪，增加真实感。
    """
    if bot_dominant != "平静":
        return None  # 已有情绪时不触发新波动

    if hour is None:
        from datetime import datetime
        hour = datetime.now().hour

    # 时段修正
    chance = _SWING_BASE_CHANCE
    period_bias = None
    if 0 <= hour < 6:
        chance *= 1.5
        period_bias = "negative"
    elif 12 <= hour < 14:
        chance *= 1.2
        period_bias = "lazy"
    elif 19 <= hour < 22:
        chance *= 0.8

    if random.random() > chance:
        return None

    # 选择波动类型
    if affection_score >= 100:
        candidates = _SWING_TYPES_HIGH_AFFECTION
    else:
        candidates = _SWING_TYPES_LOW_AFFECTION

    # 时段偏好
    if period_bias == "lazy":
        # 午后偏好犯困类
        lazy_types = [t for t in candidates if t["arousal"] < 0.3]
        if lazy_types:
            candidates = lazy_types
    elif period_bias == "negative":
        # 深夜偏好低落类
        neg_types = [t for t in candidates if t["valence"] < 0]
        if neg_types:
            candidates = neg_types

    swing = random.choice(candidates)
    logger.info(f"[情绪波动] 触发: {swing['dominant']} ({swing['reason']})")
    return swing


# ============================================================
# 渐进恢复 — 生气→傲娇→平静，不是一步到位
# ============================================================

# 恢复阶段定义
_RECOVERY_STAGES = {
    "生气": [
        {"progress": 0.0, "label": "生气", "hint": "你还在生气，语气冷淡不耐烦，回复要短"},
        {"progress": 0.3, "label": "消气中", "hint": "你没那么气了，但还有点小脾气，回复短但没那么冷"},
        {"progress": 0.6, "label": "傲娇", "hint": "你已经不太气了，但嘴上还不服软，有点傲娇"},
        {"progress": 1.0, "label": "平静", "hint": ""},
    ],
    "难过": [
        {"progress": 0.0, "label": "难过", "hint": "你有点难过，语气低落，不想多说话"},
        {"progress": 0.4, "label": "淡淡", "hint": "你没那么难过了，但还是有点没精神"},
        {"progress": 1.0, "label": "平静", "hint": ""},
    ],
    "吃醋": [
        {"progress": 0.0, "label": "吃醋", "hint": "你在吃醋，语气酸酸的，回复变短"},
        {"progress": 0.5, "label": "傲娇", "hint": "醋劲过了，但还有点小别扭"},
        {"progress": 1.0, "label": "平静", "hint": ""},
    ],
    "担心": [
        {"progress": 0.0, "label": "担心", "hint": "你在担心他，语气关心但有点急"},
        {"progress": 0.5, "label": "释然", "hint": "没那么担心了，语气缓和下来"},
        {"progress": 1.0, "label": "平静", "hint": ""},
    ],
}


def get_gradual_recovery(
    dominant: str,
    trigger_time: float,
    duration: float,
) -> Optional[Dict[str, Any]]:
    """计算当前恢复阶段和提示。

    生气不是直接消气，而是经历：生气 → 消气中 → 傲娇 → 平静。

    Returns:
        {"stage_label", "hint", "progress"} 或 None（不需要恢复/已恢复）
    """
    if dominant == "平静" or dominant not in _RECOVERY_STAGES:
        return None

    stages = _RECOVERY_STAGES[dominant]
    dt = time.time() - trigger_time
    progress = min(1.0, dt / duration) if duration > 0 else 1.0

    # 找到当前阶段
    current_stage = stages[-1]
    for stage in stages:
        if progress >= stage["progress"]:
            current_stage = stage
        else:
            break

    if current_stage["label"] == "平静":
        return None  # 已恢复

    return {
        "stage_label": current_stage["label"],
        "hint": current_stage["hint"],
        "progress": progress,
    }


# ============================================================
# 情绪记忆 — 什么话题让用户开心/难过
# ============================================================

# 话题 → 情绪关联存储（使用 user_preferences 表）
_TOPIC_EMOTION_TYPE = "topic_emotion"


async def record_topic_emotion(user_id: str, topic: str, emotion_label: str):
    """记录话题与情绪的关联。

    用于学习"用户聊什么话题时会有什么情绪"。
    """
    if not topic or len(topic) > 30:
        return
    # 只记录明显的情绪
    if emotion_label in ("平静", "无聊", ""):
        return

    try:
        from .db_preferences import update_user_preference
        # 用 "topic_emotion:话题" 作为 pref_key，情绪标签作为 pref_key
        # 这样可以查询某个话题的各情绪分布
        await update_user_preference(
            user_id, _TOPIC_EMOTION_TYPE, f"{topic}:{emotion_label}", 0.1
        )
        logger.debug(f"[情绪记忆] 记录: user={user_id[:6]} {topic} → {emotion_label}")
    except Exception as e:
        logger.debug(f"[情绪记忆] 记录失败（非关键）: {e}")


async def get_emotion_memory_hint(user_id: str, current_msg: str) -> Optional[str]:
    """根据当前话题查找历史情绪记录，返回提示。

    例：用户聊"游戏"时通常很开心 → "他聊游戏时总是很开心"
    """
    import re
    try:
        from .db_preferences import get_user_preferences
        prefs = await get_user_preferences(user_id)
        topic_emotions = prefs.get(_TOPIC_EMOTION_TYPE, {})
        if not topic_emotions:
            return None

        # 从当前消息提取关键词
        keywords = set(re.findall(r'[一-鿿]{2,6}', current_msg))
        if not keywords:
            return None

        # 匹配话题情绪记录
        matches = {}  # topic -> {emotion: score}
        for key, score in topic_emotions.items():
            if ":" not in key:
                continue
            topic, emotion = key.rsplit(":", 1)
            for kw in keywords:
                if kw in topic or topic in kw:
                    if topic not in matches:
                        matches[topic] = {}
                    matches[topic][emotion] = score

        if not matches:
            return None

        # 找到最匹配的话题
        best_topic = max(matches.keys(), key=lambda t: sum(matches[t].values()))
        emotions = matches[best_topic]
        if not emotions:
            return None

        # 找到该话题最常见的情绪
        best_emotion = max(emotions.keys(), key=lambda e: emotions[e])
        total = sum(emotions.values())
        confidence = emotions[best_emotion] / total if total > 0 else 0

        if confidence < 0.5:
            return None

        # 生成提示
        emotion_hints = {
            "开心": f"他聊「{best_topic}」时通常很开心，你可以延续这个好心情",
            "兴奋": f"他聊「{best_topic}」时会很兴奋，一起high起来",
            "难过": f"他聊「{best_topic}」时可能会难过，注意关心一下",
            "生气": f"「{best_topic}」可能让他不开心，小心别踩雷",
        }
        return emotion_hints.get(best_emotion, f"他聊「{best_topic}」时情绪比较{best_emotion}")

    except Exception:
        return None


# ============================================================
# 情绪表达多样性 — 不同情绪的表达方式
# ============================================================

# 补充缺失的情绪表达提示（吃醋、担心、得意、撒娇、小脾气、无聊、冷淡、犯困）
_EMOTION_EXPRESSION_MAP = {
    "吃醋": "你在吃醋，语气酸酸的，回复变短，可能带点刺。不要直接说'我吃醋了'，而是用语气表达。",
    "担心": "你在担心他，语气关心但有点急。不要说'我很担心你'，而是问'你还好吗'之类的。",
    "得意": "你有点得意，语气轻快，可能会自夸一下。不要太明显，含蓄地得意。",
    "撒娇": "你想撒娇，语气变软变甜。可以适当用'嘛~'、'哼'、'呜'。",
    "小脾气": "你突然有点小脾气，回复变短，带点'哼'的语气。不要太认真，是撒娇式的。",
    "无聊": "你有点无聊，回复简短。可能会说'好无聊'、'干嘛呢'。",
    "冷淡": "你突然有点冷淡，回复变短变敷衍。不要太刻意，就是话变少了。",
    "犯困": "你有点犯困，回复慢且短。可能会说'困了'、'好累'。",
}


def get_emotion_expression_hint(dominant: str) -> Optional[str]:
    """获取情绪表达多样性提示。"""
    return _EMOTION_EXPRESSION_MAP.get(dominant)


# ============================================================
# 情绪隐藏引擎 — 真人化 P1-2
# ============================================================

# 情绪隐藏概率配置（Phase 5.2 参数调优：可通过 config.py 覆盖）
try:
    from .config import (
        HUMANIZE_TUNING_EMOTION_HIDE_MEDIUM,
        HUMANIZE_TUNING_EMOTION_HIDE_LOW,
        HUMANIZE_TUNING_HIDE_AFFECTION_MODIFIER_HIGH,
    )
except ImportError:
    HUMANIZE_TUNING_EMOTION_HIDE_MEDIUM = 0.4
    HUMANIZE_TUNING_EMOTION_HIDE_LOW = 0.8
    HUMANIZE_TUNING_HIDE_AFFECTION_MODIFIER_HIGH = 0.5

_HIDE_CONFIG = {
    "high": {"threshold": 0.8, "hide_chance": 0.0, "micro_chance": 0.1},                    # >0.8 → 几乎不隐藏
    "medium": {"threshold": 0.5, "hide_chance": HUMANIZE_TUNING_EMOTION_HIDE_MEDIUM, "micro_chance": 0.5},  # 0.5-0.8 → 可配置
    "low": {"threshold": 0.2, "hide_chance": HUMANIZE_TUNING_EMOTION_HIDE_LOW, "micro_chance": 0.8},       # 0.2-0.5 → 可配置
}


def should_express_emotion(
    emotion_intensity: float,
    affection_score: float = 0,
) -> tuple:
    """决定是否表达情绪以及以什么方式表达。

    真人化 P1-2：情绪检测到 ≠ 情绪表达。
    - 80% 的轻中度情绪波动被隐藏
    - 隐藏情绪以「微表达」形式泄露（标点变化、回复变短、用词微妙不同）

    Args:
        emotion_intensity: 情绪强度 0~1
        affection_score: 好感度（高好感度时更愿意表达）

    Returns:
        (should_express: bool, style: str)
        style 可选值: "explicit"（显式表达）, "micro"（微表达泄露）, "hidden"（完全隐藏）, "none"（无情绪）
    """
    import random as _random

    # 好感度修正：高好感度降低隐藏概率（可配置）
    hide_modifier = 1.0
    if affection_score >= 300:
        hide_modifier = HUMANIZE_TUNING_HIDE_AFFECTION_MODIFIER_HIGH  # 亲密关系 → 更愿意表达
    elif affection_score >= 150:
        hide_modifier = 0.75
    elif affection_score < 50:
        hide_modifier = 1.2  # 陌生人 → 更倾向于隐藏

    # 高强度情绪 → 很难隐藏
    if emotion_intensity > 0.8:
        if _random.random() < 0.05 * hide_modifier:
            return (False, "hidden")  # 极少数情况下也会藏住
        if _random.random() < 0.1:
            return (True, "micro")
        return (True, "explicit")

    # 中强度情绪 → 40% 隐藏（受好感度修正）
    if emotion_intensity > 0.5:
        hide_chance = 0.4 * hide_modifier
        if _random.random() < hide_chance:
            return (False, "hidden")
        remaining = _random.random()
        if remaining < 0.5:
            return (True, "micro")   # 微表达：标点减少、回复变短
        return (True, "explicit")

    # 低强度情绪 → 80% 隐藏
    if emotion_intensity > 0.2:
        hide_chance = min(0.95, 0.8 * hide_modifier)
        if _random.random() < hide_chance:
            return (False, "hidden")
        return (True, "micro")

    # 极低强度 → 不表达
    return (False, "none")


def get_micro_expression_hint(emotion: str) -> str:
    """获取微表达提示（当情绪被隐藏时需要泄露的细节）。

    Micro-expression 是隐藏情绪的"泄露"：
    - 标点变少
    - 回复变短一两个词
    - 用词微妙不同
    - 句中停顿/省略号
    """
    hints = {
        "生气": "虽然你在隐藏生气，但回复比平时短，标点减少，偶尔用'…'代替想说的话",
        "难过": "你在努力掩饰难过，但语气比平时淡，偶尔停顿一下",
        "吃醋": "你装作不在意，但语气有点酸，偶尔冒出一句酸话又收回去",
        "担心": "你不想让他知道你担心，但问题变多了，语气有点急",
        "被冷落": "你觉得被冷落了但不想表现，回复明显变短，不太反问",
        "不安": "你有点不安，用词比平时更斟酌，偶尔加了'…'",
        "害羞": "你想说但不好意思，回复变短，可能带个'…'",
    }
    return hints.get(emotion, "你在隐藏情绪，回复比平时更简短克制")


def get_explicit_expression_hint(emotion: str) -> str:
    """获取显式表达提示（自然融入，不直说情绪名称）。"""
    hints = {
        "生气": "你有点生气，但不要直说'我生气了'。语气冷淡一些，回复简短。",
        "难过": "你有点难过，语气低落一些。不要直说'我很难过'。",
        "吃醋": "你在吃醋，语气酸酸的。不要直说'我吃醋了'，用撒娇的方式表达。",
        "担心": "你在担心他，语气关心。不要直说'我担心你'，而是多问几句。",
        "被冷落": "你觉得被冷落了。不要直说，但可以暗示'你是不是很忙'。",
        "不安": "你有点不安。不要直说，但回复会更小心翼翼一些。",
        "害羞": "你有点害羞，回复变短。不要直说'我害羞了'。",
        "开心": "你心情很好，语气轻快。但不要直接说'我好开心'，而是自然流露。",
        "兴奋": "你很兴奋，语气活跃。但不要直说'我好兴奋'。",
    }
    return hints.get(emotion, f"你当前情绪是{emotion}，不要直说，让语气自然流露。")


def apply_emotion_expression(
    emotion: str,
    intensity: float,
    affection_score: float = 0,
) -> dict:
    """完整的情绪表达决策流程。

    1. 决定是否表达/隐藏/微表达
    2. 生成相应的提示文本
    3. 同步到 CausalContext

    Returns:
        {"should_express": bool, "style": str, "hint": str}
    """
    should, style = should_express_emotion(intensity, affection_score)

    if style == "hidden" or style == "none":
        hint = get_micro_expression_hint(emotion)
    elif style == "micro":
        hint = get_micro_expression_hint(emotion)
    else:  # explicit
        hint = get_explicit_expression_hint(emotion)

    return {
        "should_express": should,
        "style": style,
        "hint": hint,
        "emotion": emotion,
        "intensity": intensity,
    }


# ============================================================
# 情绪残留系统 — 真人化 P3-4.2
# ============================================================

# 残留配置（Phase 5.2 参数调优：可通过 config.py 覆盖）
try:
    from .config import (
        HUMANIZE_TUNING_RESIDUE_BASE_RATIO,
        HUMANIZE_TUNING_RESIDUE_DECAY_PER_HOUR,
        HUMANIZE_TUNING_REKINDLE_BASE_PROB,
    )
except ImportError:
    HUMANIZE_TUNING_RESIDUE_BASE_RATIO = 0.3
    HUMANIZE_TUNING_RESIDUE_DECAY_PER_HOUR = 0.10
    HUMANIZE_TUNING_REKINDLE_BASE_PROB = 0.08

_RESIDUE_INITIAL_INTENSITY = HUMANIZE_TUNING_RESIDUE_BASE_RATIO   # 恢复后残留强度
_RESIDUE_DECAY_PER_HOUR = HUMANIZE_TUNING_RESIDUE_DECAY_PER_HOUR  # 每小时衰减率
_RESIDUE_REKINDLE_CHANCE = HUMANIZE_TUNING_REKINDLE_BASE_PROB     # 每次检查时有概率复发
_RESIDUE_MIN_INTENSITY = 0.02      # 低于此值视为完全消散


def compute_residue_intensity(
    recovered_at: float,
    original_intensity: float,
    now: float = None,
) -> float:
    """计算情绪残留的当前强度。

    情绪从"恢复→干净"改为"恢复→残留淡出"。
    - 刚恢复时：残留强度 = 原始强度 × 0.3
    - 随时间衰减：每小时减少 10%
    - 低于阈值时完全消散

    Args:
        recovered_at: 情绪恢复时间戳（秒）
        original_intensity: 原始情绪强度 0~1
        now: 当前时间（默认 time.time()）

    Returns:
        当前残留强度 0~1
    """
    import time as _time
    if now is None:
        now = _time.time()

    initial_residue = original_intensity * _RESIDUE_INITIAL_INTENSITY
    hours_elapsed = (now - recovered_at) / 3600.0

    # 指数衰减
    decay_factor = (1.0 - _RESIDUE_DECAY_PER_HOUR) ** hours_elapsed
    current = initial_residue * decay_factor

    if current < _RESIDUE_MIN_INTENSITY:
        return 0.0

    return round(current, 4)


def maybe_rekindle(
    emotion_label: str,
    residue_intensity: float,
    hours_since_recovery: float = 0,
) -> Optional[dict]:
    """检查是否触发情绪复发（rekindle 事件）。

    偶尔突然想起之前的事，情绪重新涌上来——但强度弱于原始。
    - 基础概率：8%
    - 好感度修正：高好感度略高（更在意）
    - 时间修正：24h 内复发概率更高（记忆新鲜）

    Args:
        emotion_label: 原始情绪标签
        residue_intensity: 当前残留强度
        hours_since_recovery: 距恢复已过小时数

    Returns:
        {"emotion": str, "intensity": float, "reason": str} 或 None
    """
    import random as _random
    import time as _time

    if residue_intensity < _RESIDUE_MIN_INTENSITY:
        return None

    # 基础复发概率
    chance = _RESIDUE_REKINDLE_CHANCE

    # 时间修正：24h 内复发概率×2（记忆新鲜）
    if hours_since_recovery < 24:
        chance *= 2.0
    elif hours_since_recovery > 72:
        chance *= 0.5  # 3天后复发概率减半

    if _random.random() > chance:
        return None

    # 复发强度：残留强度 × (0.5~1.0 随机)
    rekindle_intensity = residue_intensity * _random.uniform(0.5, 1.0)
    rekindle_intensity = min(0.5, rekindle_intensity)  # 复发强度上限 0.5（不会比原始强）

    reasons = {
        "生气": "突然想起之前那件事，又有点气了",
        "难过": "刚才某件事让你又想起了之前的不开心",
        "吃醋": "突然又想到那件事，醋劲又上来了",
        "担心": "突然又有点担心了",
        "撒娇": "突然又想撒娇了",
        "害羞": "突然又有点不好意思了",
    }
    reason = reasons.get(emotion_label, f"突然又有点{emotion_label}了")

    return {
        "emotion": emotion_label,
        "intensity": round(rekindle_intensity, 3),
        "reason": reason,
        "is_rekindle": True,
    }


def get_residue_hint(emotion_label: str, intensity: float) -> str:
    """生成情绪残留的提示文本（用于注入 prompt）。

    残留情绪不会直接表达，但会潜移默化影响语气。
    """
    if intensity < _RESIDUE_MIN_INTENSITY:
        return ""

    hints = {
        "生气": f"你之前生气的情绪还没完全消散（残留{intensity:.2f}），语气中可能带着一丝残留的冷淡",
        "难过": f"之前的难过还留有一点痕迹（残留{intensity:.2f}），心情没有完全恢复",
        "吃醋": f"醋劲虽然过去了但还有点酸（残留{intensity:.2f}），偶尔会冒出一句酸话",
        "担心": f"之前的担心还没完全放下（残留{intensity:.2f}），还是有点不放心",
        "害羞": f"害羞的感觉还残留着（残留{intensity:.2f}），说话还是会有点缩",
        "撒娇": f"撒娇的惯性还在（残留{intensity:.2f}），语气还是会软软的",
    }

    base = hints.get(emotion_label, f"之前的{emotion_label}情绪还留有一点尾巴（残留{intensity:.2f}）")
    if intensity < 0.1:
        base += "——已经很淡了，几乎不影响语气"
    elif intensity > 0.2:
        base += "——还比较明显，可能在合适的瞬间重新涌上来"
    return base


class EmotionResidueTracker:
    """情绪残留追踪器（会话级）。

    追踪当前会话中所有已恢复情绪的残留状态。
    """

    def __init__(self):
        self._residues: dict = {}  # emotion_label -> {"recovered_at": ts, "original_intensity": float}

    def record_recovery(self, emotion_label: str, original_intensity: float):
        """记录一次情绪恢复，开始残留追踪。"""
        import time as _time
        self._residues[emotion_label] = {
            "recovered_at": _time.time(),
            "original_intensity": original_intensity,
        }

    def get_active_residues(self) -> list:
        """获取当前所有活跃的残留情绪（按强度降序）。"""
        import time as _time
        now = _time.time()
        active = []

        for label, info in self._residues.items():
            intensity = compute_residue_intensity(
                info["recovered_at"], info["original_intensity"], now
            )
            if intensity >= _RESIDUE_MIN_INTENSITY:
                active.append({
                    "emotion": label,
                    "intensity": intensity,
                    "hours_since": (now - info["recovered_at"]) / 3600.0,
                })

        active.sort(key=lambda x: x["intensity"], reverse=True)
        return active

    def check_rekindle(self) -> Optional[dict]:
        """检查是否有残留情绪复发。"""
        import time as _time
        now = _time.time()

        for label, info in self._residues.items():
            intensity = compute_residue_intensity(
                info["recovered_at"], info["original_intensity"], now
            )
            if intensity < _RESIDUE_MIN_INTENSITY:
                continue

            hours_since = (now - info["recovered_at"]) / 3600.0
            result = maybe_rekindle(label, intensity, hours_since)
            if result:
                return result

        return None

    def clear(self):
        """清除所有残留记录。"""
        self._residues.clear()


# 会话级残留追踪器（key=session_id）
_residue_trackers: dict = {}
_RESIDUE_TRACKER_MAX = 500


def get_residue_tracker(session_id: str) -> EmotionResidueTracker:
    """获取或创建会话级情绪残留追踪器。"""
    if session_id not in _residue_trackers:
        if len(_residue_trackers) >= _RESIDUE_TRACKER_MAX:
            # LRU 淘汰：删除最旧的 100 个
            keys = list(_residue_trackers.keys())[:100]
            for k in keys:
                del _residue_trackers[k]
        _residue_trackers[session_id] = EmotionResidueTracker()
    return _residue_trackers[session_id]
