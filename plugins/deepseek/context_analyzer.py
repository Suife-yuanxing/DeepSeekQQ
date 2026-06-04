"""上下文分析器 + 情绪引擎（Phase 1 + Phase 2 合并）。

一次 DeepSeek API 调用同时完成：
1. 上下文理解：话题连续性、指代消解、用户意图
2. 情绪分析：VA模型（效价+唤醒度）、情绪类别、置信度

替代原有的关键词匹配方案，实现语义级理解。
"""
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

from nonebot import logger

from . import api
from .database import (
    get_user_mood, update_user_mood, get_catgirl_mood,
    decay_user_mood, get_bot_mood, update_bot_mood
)

# ============================================================
# 数据结构
# ============================================================

@dataclass
class ContextAnalysis:
    """上下文分析结果"""
    is_topic_continuation: bool = True    # 是否延续上文话题
    topic_shift_score: float = 0.0        # 话题转移程度 0~1
    topic_summary: str = ""               # 当前话题摘要
    referenced_entity: str = ""           # 指代消解结果
    user_intent: str = "闲聊"             # 闲聊/提问/分享/指令/情绪表达
    raw: dict = field(default_factory=dict)


@dataclass
class EmotionState:
    """情绪状态（VA模型）"""
    valence: float = 0.0      # 效价: -1(消极) ~ +1(积极)
    arousal: float = 0.2      # 唤醒度: 0(平静) ~ 1(激动)
    dominant: str = "平静"     # 主导情绪标签
    confidence: float = 0.5   # 分析置信度
    intensity: float = 0.0    # 情绪强度 0~1


@dataclass
class AnalysisResult:
    """合并分析结果"""
    context: ContextAnalysis
    emotion: EmotionState
    raw_response: dict = field(default_factory=dict)


# ============================================================
# 情绪维度映射 (Valence-Arousal)
# ============================================================

EMOTION_VA_MAP = {
    "开心": (0.7, 0.6),
    "兴奋": (0.9, 0.85),
    "害羞": (0.3, 0.65),
    "傲娇": (0.1, 0.5),
    "平静": (0.0, 0.15),
    "无聊": (-0.2, 0.1),
    "难过": (-0.6, 0.3),
    "生气": (-0.7, 0.8),
    "担心": (-0.4, 0.55),
    "害怕": (-0.5, 0.7),
    "嫌弃": (-0.3, 0.4),
    "期待": (0.6, 0.7),
    "感动": (0.5, 0.5),
    "无语": (-0.2, 0.2),
}

# 情绪惯性系数：保留旧情绪的比例
EMOTION_INERTIA = 0.65

# 情绪衰减配置
DECAY_HALF_LIFE_SECONDS = 1800  # 30分钟半衰期（激动情绪衰减到一半）


# ============================================================
# 核心分析函数
# ============================================================

def _build_analysis_prompt(user_msg: str, history: List[Dict[str, Any]]) -> str:
    """构建合并分析 prompt"""
    # 取最近3条消息作为上下文
    recent = history[-6:] if len(history) > 6 else history
    history_text = "\n".join([
        f"{'用户' if m['role'] == 'user' else '猫娘'}：{m['content'][:80]}"
        for m in recent
    ])

    return f"""分析以下对话，同时返回上下文理解和情绪判断。

【最近对话】
{history_text}

【用户最新消息】
{user_msg}

请严格按以下JSON格式返回，不要有任何其他文字：
```json
{{
  "context": {{
    "is_continuation": true/false,
    "topic_shift": 0.0-1.0,
    "topic": "当前话题简述（10字内）",
    "reference": "如果用户消息有指代词(它/那个/这个/他/她)，解析出指代对象，否则留空",
    "intent": "闲聊/提问/分享/指令/情绪表达"
  }},
  "emotion": {{
    "valence": -1.0到1.0,
    "arousal": 0.0到1.0,
    "type": "开心/兴奋/害羞/傲娇/平静/无聊/难过/生气/担心/害怕/嫌弃/期待/感动/无语",
    "confidence": 0.0到1.0,
    "intensity": 0.0到1.0
  }}
}}```"""


def _parse_analysis_response(raw: str) -> Optional[dict]:
    """解析LLM返回的JSON"""
    # 去除 markdown 代码块
    clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
    # 尝试提取 JSON 对象
    match = re.search(r'\{[\s\S]*\}', clean)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


async def analyze_context_and_emotion(
    user_msg: str,
    history: List[Dict[str, Any]],
    user_id: str,
) -> AnalysisResult:
    """一次 API 调用完成上下文分析 + 情绪分析。

    Returns:
        AnalysisResult 包含 ContextAnalysis 和 EmotionState
    """
    # 默认结果（分析失败时使用）
    default_context = ContextAnalysis()
    default_emotion = EmotionState()

    # 短消息或无历史时，跳过API调用用规则判断
    if len(user_msg.strip()) <= 2 and len(history) < 2:
        return AnalysisResult(context=default_context, emotion=default_emotion)

    prompt = _build_analysis_prompt(user_msg, history)

    try:
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON，不要有任何其他文字。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.2)
        data = _parse_analysis_response(raw)

        if not data:
            logger.warning(f"[分析] JSON解析失败: {raw[:100]}")
            return AnalysisResult(context=default_context, emotion=default_emotion)

        # 解析上下文
        ctx_data = data.get("context", {})
        context = ContextAnalysis(
            is_topic_continuation=ctx_data.get("is_continuation", True),
            topic_shift_score=float(ctx_data.get("topic_shift", 0.0)),
            topic_summary=ctx_data.get("topic", ""),
            referenced_entity=ctx_data.get("reference", ""),
            user_intent=ctx_data.get("intent", "闲聊"),
            raw=ctx_data,
        )

        # 解析情绪
        emo_data = data.get("emotion", {})
        raw_valence = float(emo_data.get("valence", 0.0))
        raw_arousal = float(emo_data.get("arousal", 0.2))
        emo_type = emo_data.get("type", "平静")
        confidence = float(emo_data.get("confidence", 0.5))
        intensity = float(emo_data.get("intensity", 0.0))

        # 应用情绪惯性：与上一次情绪混合
        old_mood = await get_user_mood(user_id)
        if old_mood and old_mood.get("last_updated"):
            dt = time.time() - old_mood["last_updated"]
            # 自然衰减旧情绪
            decayed_v = old_mood["valence"] * _decay_factor(dt)
            decayed_a = old_mood["arousal"] * _decay_factor(dt)

            # 惯性混合
            final_valence = decayed_v * EMOTION_INERTIA + raw_valence * (1 - EMOTION_INERTIA)
            final_arousal = decayed_a * EMOTION_INERTIA + raw_arousal * (1 - EMOTION_INERTIA)
        else:
            final_valence = raw_valence
            final_arousal = raw_arousal

        # 钳位
        final_valence = max(-1.0, min(1.0, final_valence))
        final_arousal = max(0.0, min(1.0, final_arousal))

        emotion = EmotionState(
            valence=final_valence,
            arousal=final_arousal,
            dominant=emo_type,
            confidence=confidence,
            intensity=intensity,
        )

        # 持久化用户情绪
        await update_user_mood(user_id, final_valence, final_arousal, emo_type)

        logger.info(
            f"[分析] 用户={user_id[:6]} 意图={context.user_intent} "
            f"话题延续={context.is_topic_continuation} "
            f"情绪={emo_type}(V={final_valence:.2f} A={final_arousal:.2f} conf={confidence:.2f})"
        )

        return AnalysisResult(context=context, emotion=emotion, raw_response=data)

    except Exception as e:
        logger.error(f"[分析] API调用异常: {e}")
        return AnalysisResult(context=default_context, emotion=default_emotion)


def _decay_factor(dt_seconds: float) -> float:
    """计算衰减因子：指数衰减，半衰期 DECAY_HALF_LIFE_SECONDS"""
    return math.exp(-0.693 * dt_seconds / DECAY_HALF_LIFE_SECONDS)


# ============================================================
# 情绪 → Prompt 映射
# ============================================================

def emotion_to_prompt_hint(emotion: EmotionState) -> str:
    """将 VA 情绪状态转化为 prompt 中的语气提示"""
    if emotion.confidence < 0.4:
        return ""  # 置信度太低不注入

    v, a = emotion.valence, emotion.arousal
    dominant = emotion.dominant

    # 高唤醒度情绪
    if a > 0.7:
        if v > 0.3:
            return "你现在很兴奋，话比较多，语气活泼轻快。"
        elif v < -0.3:
            return "你现在情绪有点激动，可能不太耐烦。"
        else:
            return "你现在精神很好，聊天比较活跃。"

    # 中等唤醒度
    if a > 0.35:
        if v > 0.3:
            return "你现在心情不错，语气轻快，可能会主动调侃。"
        elif v < -0.3:
            return "你现在有点低落，回复偏简短，偶尔嘴硬。"
        else:
            return "你现在有点傲娇，嘴硬心软。"

    # 低唤醒度（平静/无聊）
    if v < -0.2:
        return "你现在有点懒洋洋的，回复偏简短冷淡。"
    if dominant == "害羞":
        return "你现在有点害羞，说话会稍微扭捏。"

    return ""


def emotion_to_mood_label(emotion: EmotionState) -> Dict[str, Any]:
    """将 VA 状态映射回旧的 mood 格式（兼容）"""
    v, a = emotion.valence, emotion.arousal
    score = (v + 1) / 2 * 100  # -1~1 → 0~100

    if v > 0.3 and a > 0.5:
        mood = "开心"
    elif v < -0.3 and a > 0.5:
        mood = "生气"
    elif v < -0.2:
        mood = "傲娇"
    else:
        mood = "平淡"

    return {"mood": mood, "score": round(score, 1)}


# ============================================================
# Bot 情绪状态机（让bot的情绪像人类一样持续变化）
# ============================================================

# 情绪持续时间配置（秒）
BOT_MOOD_DURATION = {
    "生气": 900,     # 生气持续 ~15分钟
    "难过": 1800,    # 难过持续 ~30分钟
    "害羞": 300,     # 害羞持续 ~5分钟
    "开心": 600,     # 开心持续 ~10分钟
    "兴奋": 600,     # 兴奋持续 ~10分钟
    "担心": 1200,    # 担心持续 ~20分钟
}

# 触发bot情绪变化的关键词
_BOT_EMOTION_TRIGGERS = {
    "生气": {
        "keywords": ["滚", "烦死了", "闭嘴", "讨厌", "你烦不烦", "别说了", "不想理你", "无语", "sb", "傻逼"],
        "valence": -0.7, "arousal": 0.8, "reason": "被用户凶了",
    },
    "难过": {
        "keywords": ["不想聊了", "没意思", "算了", "无所谓", "随便吧", "你走吧", "不想说"],
        "valence": -0.6, "arousal": 0.3, "reason": "感觉被冷落了",
    },
    "开心": {
        "keywords": ["喜欢你", "你真好", "可爱", "乖", "想你了", "爱你", "宝贝", "最棒了", "辛苦了"],
        "valence": 0.7, "arousal": 0.6, "reason": "被夸奖了",
    },
    "害羞": {
        "keywords": ["好看", "漂亮", "美女", "心动", "表白", "在一起", "亲一个", "抱抱我"],
        "valence": 0.3, "arousal": 0.65, "reason": "被撩了",
    },
}

# 安抚关键词（可以加速消解负面情绪）
_COMFORT_KEYWORDS = ["对不起", "抱歉", "别生气", "我错了", "抱抱", "乖", "不生气了", "心疼", "安慰", "好了好了"]


async def update_bot_emotion(user_msg: str, user_emotion: EmotionState) -> Dict[str, Any]:
    """更新bot自己的情绪状态。

    逻辑：
    1. 检查用户消息是否触发了新的情绪
    2. 如果bot当前有负面情绪且用户没有安抚，情绪持续
    3. 如果用户安抚了，加速衰减负面情绪
    4. 自然衰减：负面情绪随时间减弱
    """
    old_mood = await get_bot_mood()
    now = time.time()

    # 计算旧情绪的衰减
    dt = now - old_mood.get("last_updated", now)
    duration = BOT_MOOD_DURATION.get(old_mood["dominant"], 600)

    # 自然衰减：超过持续时间后回归平静
    if dt > duration and old_mood["dominant"] != "平静":
        await update_bot_mood(0.0, 0.2, "平静", "自然消退")
        logger.info(f"[Bot情绪] 自然消退: {old_mood['dominant']} -> 平静 (过了{int(dt)}秒)")
        return {"dominant": "平静", "reason": "自然消退"}

    # 检查用户是否在安抚
    is_comforting = any(kw in user_msg for kw in _COMFORT_KEYWORDS)

    # 如果bot在负面情绪中且用户在安抚
    if old_mood["dominant"] in ("生气", "难过") and is_comforting:
        # 安抚后情绪减弱（缩短一半持续时间）
        decay_ratio = 0.5
        new_v = old_mood["valence"] * decay_ratio
        new_a = old_mood["arousal"] * decay_ratio
        if abs(new_v) < 0.15:
            await update_bot_mood(0.0, 0.2, "平静", "被安抚了")
            logger.info(f"[Bot情绪] 被安抚消退: {old_mood['dominant']} -> 平静")
            return {"dominant": "平静", "reason": "被安抚了"}
        else:
            new_dominant = "傲娇" if old_mood["dominant"] == "生气" else "平静"
            await update_bot_mood(new_v, new_a, new_dominant, "被安抚了但还有点情绪")
            logger.info(f"[Bot情绪] 被安抚减弱: {old_mood['dominant']} -> {new_dominant}")
            return {"dominant": new_dominant, "reason": "被安抚了"}

    # 检查是否触发新情绪（只有当旧情绪已衰减或平静时才触发新情绪）
    triggered = None
    if old_mood["dominant"] == "平静" or dt > duration * 0.5:
        for emotion_name, trigger in _BOT_EMOTION_TRIGGERS.items():
            if any(kw in user_msg for kw in trigger["keywords"]):
                triggered = (emotion_name, trigger)
                break

    if triggered:
        emotion_name, trigger = triggered
        await update_bot_mood(trigger["valence"], trigger["arousal"], emotion_name, trigger["reason"])
        logger.info(f"[Bot情绪] 触发新情绪: {emotion_name} ({trigger['reason']})")
        return {"dominant": emotion_name, "reason": trigger["reason"]}

    # 没有触发新情绪，返回当前状态（衰减后的）
    if old_mood["dominant"] != "平静":
        # 衰减旧情绪
        progress = dt / duration  # 0~1
        decayed_v = old_mood["valence"] * (1 - progress)
        decayed_a = old_mood["arousal"] * (1 - progress)
        if abs(decayed_v) < 0.1:
            await update_bot_mood(0.0, 0.2, "平静", "自然消退")
            return {"dominant": "平静", "reason": "自然消退"}
        return {"dominant": old_mood["dominant"], "reason": old_mood.get("trigger_reason", ""), "decaying": True}

    return {"dominant": "平静", "reason": ""}
