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
# ============================================================

EMOTION_EXPRESSION_VARIANTS = {
    "吃醋": [
        # 变体1：酸溜溜
        "语气酸酸的，回复变短，可能带点刺，但不是真的生气",
        # 变体2：假装不在意
        "表面说'随便你'，但语气明显在吃醋",
        # 变体3：直接表达
        "有点小吃醋，但用撒娇的方式说出来",
        # 变体4：傲娇
        "明明在意但嘴硬说'我才不在乎呢'"
    ],
    "担心": [
        # 变体1：直接关心
        "语气关心但有点急，想确认对方没事",
        # 变体2：含蓄担心
        "表面平静但问题变多了，想了解情况",
        # 变体3：焦虑
        "语气有点焦虑，重复确认细节"
    ],
    "得意": [
        # 变体1：含蓄自夸
        "语气轻快，含蓄地自夸但不明显",
        # 变体2：直接炫耀
        "开心地分享成就，期待夸奖",
        # 变体3：傲娇
        "表面说'也没什么啦'但明显很开心"
    ],
    "撒娇": [
        # 变体1：直接撒娇
        "语气变软变甜，想要陪伴",
        # 变体2：间接暗示
        "说'好无聊啊'但其实是想聊天",
        # 变体3：小委屈
        "有点小委屈，觉得被忽略了"
    ],
    "小脾气": [
        # 变体1：哼哼
        "回复变短，带点'哼'的语气",
        # 变体2：假装生气
        "假装生气但很容易哄好",
        # 变体3：撒娇式
        "是撒娇式的小脾气，不是真的生气"
    ]
}


def get_emotion_expression(emotion: str, affection: float) -> str:
    """获取情绪表达（考虑好感度和随机性）"""
    variants = EMOTION_EXPRESSION_VARIANTS.get(emotion, [])

    if not variants:
        # 默认表达
        return _EMOTION_EXPRESSION_MAP.get(emotion, "正常语气")

    # 好感度影响：高好感度更直接，低好感度更含蓄
    if affection > 150:
        # 偏好直接表达
        direct_variants = [v for v in variants if '直接' in v or '撒娇' in v]
        if direct_variants:
            return random.choice(direct_variants)
    elif affection < 50:
        # 偏好含蓄表达
        subtle_variants = [v for v in variants if '含蓄' in v or '表面' in v]
        if subtle_variants:
            return random.choice(subtle_variants)

    # 随机选择
    return random.choice(variants)


# ============================================================
# 情绪传染 — 用户情绪影响 bot 情绪
# ============================================================

# 传染基础系数
_CONTAGION_BASE = 0.15

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
