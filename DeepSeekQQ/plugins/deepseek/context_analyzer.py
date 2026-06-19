"""上下文分析器 + 情绪引擎（Phase 1 + Phase 2 合并）。

一次 DeepSeek API 调用同时完成：
1. 上下文理解：话题连续性、指代消解、用户意图
2. 情绪分析：VA模型（效价+唤醒度）、情绪类别、置信度

替代原有的关键词匹配方案，实现语义级理解。
"""
import asyncio
import json
import math
import re
import time
from datetime import datetime
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from . import api
from .constants import EMOTION_INERTIA
from .database import decay_user_mood
from .database import get_bot_mood
from .database import get_catgirl_mood
from .database import get_db
from .database import get_user_mood
from .database import update_bot_mood
from .database import update_user_mood
from .db_affection import get_affection
from .emotion_classifier import apply_emotional_contagion_with_buffer
from .emotion_classifier import quick_emotion_check
from .emotion_deep import apply_emotional_contagion
from .emotion_deep import get_gradual_recovery
from .emotion_deep import maybe_trigger_mood_swing
from .utils import clean_json_text
from .utils import safe_task

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
    secondary: str = ""       # 复合情绪标签（如"害羞"）
    is_compound: bool = False # 是否复合情绪
    quick_emotion: str = ""       # 规则快速判断的情绪标签
    quick_confidence: float = 0.0 # 规则判断的置信度


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

# 复合情绪VA调和值（Phase 3）
COMPOUND_EMOTION_BLENDS = {
    "开心但害羞": (0.5, 0.6, "明明高兴但不好意思表现出来"),
    "生气又委屈": (-0.65, 0.55, "又气又想哭的感觉"),
    "期待但紧张": (0.3, 0.7, "既期待又有点怕"),
    "感动又心酸": (0.2, 0.45, "被感动到了但又有点酸酸的"),
    "嫌弃但好笑": (-0.1, 0.4, "嘴上嫌弃但其实想笑"),
    "担心又无奈": (-0.35, 0.35, "担心但又没办法"),
}

# 情绪衰减配置
DECAY_HALF_LIFE_SECONDS = 1800  # 30分钟半衰期（激动情绪衰减到一半）


def apply_environmental_modifiers(emotion: 'EmotionState') -> 'EmotionState':
    """根据时间/星期施加微妙的情绪修正（Phase 3）。

    修正量很小（±0.05），不影响主要情绪，只是增加真实感。
    """
    hour = datetime.now().hour
    weekday = datetime.now().weekday()

    v_mod, a_mod = 0.0, 0.0

    # 深夜：唤醒度降低（困了）
    if hour >= 23 or hour <= 3:
        a_mod -= 0.05
    # 清晨：唤醒度稍高但效价中性
    elif 5 <= hour <= 7:
        a_mod += 0.02
    # 周一早上：轻微负面
    if weekday == 0 and hour < 10:
        v_mod -= 0.03
    # 周五晚上/周六：情绪更好
    if weekday in [4, 5] and hour >= 18:
        v_mod += 0.03
    # 周末懒散
    if weekday in [5, 6] and hour < 10:
        a_mod -= 0.02

    emotion.valence = max(-1.0, min(1.0, emotion.valence + v_mod))
    emotion.arousal = max(0.0, min(1.0, emotion.arousal + a_mod))
    return emotion


# ============================================================
# 核心分析函数
# ============================================================

def _build_analysis_prompt(user_msg: str, history: List[Dict[str, Any]], shares: Optional[List[Dict[str, Any]]] = None) -> str:
    """构建合并分析 prompt"""
    # 取最近3条消息作为上下文
    recent = history[-6:] if len(history) > 6 else history
    history_text = "\n".join([
        f"{'用户' if m['role'] == 'user' else '念念'}：{m['content'][:80]}"
        for m in recent
    ])

    # 当前消息中的分享/图片信息（帮助指代消解）
    current_context = ""
    if shares:
        from .vision import extract_vision_text
        image_shares = [s for s in shares if s.get("type") == "图片"]
        link_shares = [s for s in shares if s.get("type") in ("网页", "链接")]
        if image_shares:
            vision_text = extract_vision_text(image_shares[-1].get("summary", ""))
            if vision_text:
                current_context += f"\n【用户本条消息附带的图片内容】{vision_text[:200]}"
            else:
                current_context += "\n【用户本条消息附带了一张图片】"
        if link_shares:
            for ls in link_shares[-2:]:
                src = ls.get("source", "")
                summary = ls.get("summary", "")
                if summary:
                    current_context += f"\n【用户本条消息附带的链接】{src}: {summary[:200]}"

    return f"""分析以下对话，同时返回上下文理解和情绪判断。

【最近对话】
{history_text}{current_context}

【用户最新消息】
{user_msg}

请严格按以下JSON格式返回，不要有任何其他文字：
```json
{{
  "context": {{
    "is_continuation": true/false,
    "topic_shift": 0.0-1.0,
    "topic": "当前话题简述（10字内）",
    "reference": "如果用户消息有指代词(它/那个/这个/他/她)，解析出指代对象，否则留空。注意：如果用户本条消息附带了图片，指代词很可能是指图片中的内容",
    "intent": "闲聊/提问/分享/指令/情绪表达"
  }},
  "emotion": {{
    "valence": -1.0到1.0,
    "arousal": 0.0到1.0,
    "type": "开心/兴奋/害羞/傲娇/平静/无聊/难过/生气/担心/害怕/嫌弃/期待/感动/无语",
    "confidence": 0.0到1.0,
    "intensity": 0.0到1.0,
    "secondary": "如果有复合情绪(如开心但害羞、生气又委屈)填次情绪，否则留空"
  }}
}}```"""


def _parse_analysis_response(raw: str) -> Optional[dict]:
    """解析LLM返回的JSON"""
    # 去除 markdown 代码块
    clean = clean_json_text(raw)
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
    shares: Optional[List[Dict[str, Any]]] = None,
) -> AnalysisResult:
    """一次 API 调用完成上下文分析 + 情绪分析。

    Args:
        user_msg: 用户消息文本
        history: 对话历史
        user_id: 用户ID
        shares: 当前消息中的分享/图片（用于指代消解）

    Returns:
        AnalysisResult 包含 ContextAnalysis 和 EmotionState
    """
    # 默认结果（分析失败时使用）
    default_context = ContextAnalysis()
    default_emotion = EmotionState()

    # 短消息或无历史时，跳过API调用用规则判断
    if len(user_msg.strip()) <= 2 and len(history) < 2:
        return AnalysisResult(context=default_context, emotion=default_emotion)

    # 快速规则预检（<1ms，补充 VA 模型）
    try:
        quick_label, quick_conf = quick_emotion_check(user_msg)
        if quick_label and quick_conf >= 0.6:
            logger.debug(f"[情绪] 快速规则命中: {quick_label} conf={quick_conf:.2f}")
    except Exception:
        quick_label, quick_conf = None, 0.0

    prompt = _build_analysis_prompt(user_msg, history, shares)

    try:
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON，不要有任何其他文字。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.2, task_type="analysis")
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

        # 复合情绪检测（Phase 3）
        secondary = emo_data.get("secondary", "")
        is_compound = bool(secondary)

        emotion = EmotionState(
            valence=final_valence,
            arousal=final_arousal,
            dominant=emo_type,
            confidence=confidence,
            intensity=intensity,
            secondary=secondary,
            is_compound=is_compound,
            quick_emotion=quick_label or "",
            quick_confidence=quick_conf or 0.0,
        )

        # Phase 3：应用环境情绪修正
        emotion = apply_environmental_modifiers(emotion)

        # 持久化用户情绪
        await update_user_mood(user_id, final_valence, final_arousal, emo_type)

        # Phase 3：异步记录情绪日志
        safe_task(_log_emotion(user_id, "private_" + user_id, emo_type, final_valence, final_arousal, user_msg))

        logger.info(
            f"[分析] 用户={user_id[:6]} 意图={context.user_intent} "
            f"话题延续={context.is_topic_continuation} "
            f"情绪={emo_type}{'+'+secondary if secondary else ''}(V={final_valence:.2f} A={final_arousal:.2f} conf={confidence:.2f})"
        )

        return AnalysisResult(context=context, emotion=emotion, raw_response=data)

    except Exception as e:
        logger.error(f"[分析] API调用异常: {e}")
        return AnalysisResult(context=default_context, emotion=default_emotion)


def _decay_factor(dt_seconds: float) -> float:
    """计算衰减因子：指数衰减，半衰期 DECAY_HALF_LIFE_SECONDS"""
    return math.exp(-0.693 * dt_seconds / DECAY_HALF_LIFE_SECONDS)


async def _log_emotion(user_id: str, session_id: str, emotion_label: str,
                       valence: float, arousal: float, trigger_text: str):
    """异步记录情绪快照到 emotion_log 表（Phase 3）。"""
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO emotion_log (user_id, session_id, emotion_label, valence, arousal, trigger_text, cause_chain, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, '', ?)""",
            (user_id, session_id, emotion_label, valence, arousal, trigger_text[:100], time.time())
        )
        await db.commit()
    except Exception as e:
        logger.debug(f"[情绪日志] 写入失败（不影响主流程）: {e}")


async def get_emotion_cause_chain(user_id: str, lookback: int = 10) -> str:
    """查询最近 N 条情绪日志，生成简单因果链（Phase 3）。"""
    try:
        db = await get_db()
        async with db.execute(
            """SELECT emotion_label, trigger_text, timestamp FROM emotion_log
               WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?""",
            (user_id, lookback)
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows or len(rows) < 3:
            return ""
        # 构建简单因果链：最近的情绪变化
        labels = [r["emotion_label"] for r in reversed(rows)]
        unique_labels = []
        for l in labels:
            if not unique_labels or l != unique_labels[-1]:
                unique_labels.append(l)
        if len(unique_labels) >= 3:
            return "→".join(unique_labels[-5:])
        return ""
    except Exception:
        return ""


# ============================================================
# 情绪 → Prompt 映射
# ============================================================

def emotion_to_prompt_hint(emotion: EmotionState) -> str:
    """将 VA 情绪状态转化为 prompt 中的语气提示。

    真人化 P3-4.1：VA→LLM 混合情绪模型。
    VA 模型不再直接分配离散标签，而是提供连续的情绪趋势描述。
    移除 14 种硬编码标签映射，改为自然语言描述情绪质量，
    让 LLM 自由诠释而非被限制在模板化的情绪框里。

    描述原则：
    - 不说「你现在是XXX」，而是描述情绪氛围和质量
    - 基于 VA 坐标生成连续、细腻的描述
    - 复合情绪有层次感，单一情绪有微妙变化
    """
    if emotion.confidence < 0.4:
        return ""  # 置信度太低不注入

    v = emotion.valence   # -1(消极) ~ +1(积极)
    a = emotion.arousal   # 0(平静) ~ 1(激动)
    intensity = emotion.intensity  # 0~1
    dominant = emotion.dominant

    # 构建自然语言情绪描述
    parts = []

    # === 1. 情绪氛围（基于VA连续空间） ===
    # 效价描述
    if v > 0.6:
        v_desc = "心情很好"
    elif v > 0.3:
        v_desc = "心情不错"
    elif v > 0.1:
        v_desc = "心情还行"
    elif v > -0.1:
        v_desc = "心情中性"
    elif v > -0.3:
        v_desc = "心情一般"
    elif v > -0.6:
        v_desc = "心情不太好"
    else:
        v_desc = "心情很差"

    # 唤醒度描述
    if a > 0.8:
        a_desc = "情绪很强烈"
    elif a > 0.6:
        a_desc = "情绪比较激动"
    elif a > 0.4:
        a_desc = "情绪中等"
    elif a > 0.2:
        a_desc = "情绪平和"
    else:
        a_desc = "情绪很平静"

    # 综合氛围
    if a > 0.6 and v > 0.3:
        atmosphere = "兴奋雀跃，话会变多语调变活泼"
    elif a > 0.6 and v < -0.3:
        atmosphere = "情绪激动但偏负面，可能有点不耐烦或赌气"
    elif a > 0.4 and v > 0.2:
        atmosphere = "轻松愉快，有点想调侃人"
    elif a > 0.4 and v < -0.2:
        atmosphere = "有点低落但不算严重，回复偏短偶尔嘴硬"
    elif a < 0.25 and v < -0.15:
        atmosphere = "懒洋洋的提不起劲，回复偏简短"
    elif a < 0.25 and v > 0.15:
        atmosphere = "安安静静地开心，不张扬但心里是暖的"
    elif abs(v) < 0.15 and a < 0.3:
        atmosphere = "平静中性，像日常闲聊"
    else:
        atmosphere = f"{v_desc}，{a_desc}"

    parts.append(atmosphere)

    # === 2. 情绪倾向（基于主导标签的自然描述） ===
    # 真人化4.1：不直接输出标签名，而是描述情绪质量
    emotion_quality_map = {
        "开心": "带着一点开心的底色",
        "兴奋": "有点按捺不住的兴奋感",
        "害羞": "有点不好意思，说话会略微扭捏",
        "傲娇": "嘴硬心软的傲娇感，表面冷淡心里在意",
        "难过": "带点淡淡的难过，不是大哭那种，就是提不起劲",
        "生气": "有点生气但没爆发，语气中能感觉到冷淡",
        "担心": "心里有点放不下，会多问几句",
        "害怕": "有点不安，回复会更小心",
        "嫌弃": "嘴上嫌弃但其实没那么讨厌",
        "期待": "心里有点小期待，等着对方说什么",
        "感动": "被触动到了，语气会变温柔",
        "无语": "有点无奈，但又懒得较真",
        "无聊": "无聊想找点乐子，可能会主动找话题",
        "吃醋": "有点小吃醋，语气会酸酸的但不直说",
    }

    if dominant and dominant in emotion_quality_map:
        quality = emotion_quality_map[dominant]
        parts.append(quality)

    # === 3. 复合情绪层次感 ===
    if emotion.is_compound and emotion.secondary:
        compound_key = f"{dominant}但{emotion.secondary}" if "但" not in emotion.secondary else emotion.secondary
        blend = COMPOUND_EMOTION_BLENDS.get(compound_key)
        if blend:
            parts.append(f"情绪有层次感：{blend[2]}")

    # === 4. 强度调节 ===
    if intensity > 0.8:
        parts.append("这种感受很明显，不需要刻意压制")
    elif intensity > 0.5:
        parts.append("感受中等，不需要刻意强调")
    else:
        parts.append("感受比较淡，自然流露即可")

    # 组装
    hint = "情绪氛围：" + "；".join(parts) + "。"
    # 关键：不给出「你应该怎样」的指令，让 LLM 根据情绪氛围自由表达
    hint += "不要直接说出情绪名称，让语气自然反映以上氛围。"

    return hint


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
        "keywords": [
            "滚", "烦死了", "闭嘴", "讨厌", "你烦不烦", "别说了", "不想理你",
            "无语", "sb", "傻逼", "闭嘴吧", "别吵", "吵死了", "好烦",
            "离我远点", "不想和你说话", "你很啰嗦", "你是不是有病", "废话真多",
        ],
        "valence": -0.7, "arousal": 0.8, "reason": "被用户凶了",
    },
    "难过": {
        "keywords": [
            "不想聊了", "没意思", "算了", "无所谓", "随便吧", "你走吧",
            "不想说", "无聊", "你不懂", "行吧行吧", "随你便", "当我没说",
        ],
        "valence": -0.6, "arousal": 0.3, "reason": "感觉被冷落了",
    },
    "开心": {
        "keywords": [
            "喜欢你", "你真好", "可爱", "乖", "想你了", "爱你", "宝贝",
            "最棒了", "辛苦了", "最爱你了", "你最强", "有你在真好",
            "你真聪明", "你真可爱", "不愧是你", "好喵", "厉害",
        ],
        "valence": 0.7, "arousal": 0.6, "reason": "被夸奖了",
    },
    "害羞": {
        "keywords": [
            "好看", "漂亮", "美女", "心动", "表白", "在一起", "亲一个",
            "抱抱我", "今晚有空吗", "约会", "想你", "你真美", "想抱你",
            "想牵你的手", "你身材真好",
        ],
        "valence": 0.3, "arousal": 0.65, "reason": "被撩了",
    },
    "吃醋": {
        "keywords": [
            "她是谁", "那个女的", "和谁聊", "挺亲密的", "也不找我",
            "只回我", "和别人聊天", "群里那个", "新来的谁",
        ],
        "valence": -0.3, "arousal": 0.5, "reason": "看到主人和别人聊天，吃醋了",
    },
    "担心": {
        "keywords": [
            "病了", "不舒服", "医院", "头疼", "发烧", "感冒", "难受",
            "不开心", "心情不好", "要死了", "好累", "没睡好",
        ],
        "valence": -0.4, "arousal": 0.55, "reason": "担心主人的状态",
    },
    "得意": {
        "keywords": [
            "你还挺厉害", "可以啊", "真棒", "最强", "就靠你了",
            "还是你懂", "有道理", "你说得对", "竟然被你猜到了",
        ],
        "valence": 0.5, "arousal": 0.4, "reason": "被夸了，得意起来",
    },
}

# 安抚关键词（可以加速消解负面情绪）
_COMFORT_KEYWORDS = [
    "对不起", "抱歉", "别生气", "我错了", "抱抱", "乖", "不生气了",
    "心疼", "安慰", "好了好了", "好啦好啦", "你最好了", "不气不气",
    "我逗你的", "开个玩笑", "真的错了", "原谅我", "给你买",
    "请你吃", "乖啦", "听话", "么么哒", "mua",
]


async def update_bot_emotion(user_msg: str, user_emotion: EmotionState, user_id: str = "") -> Dict[str, Any]:
    """更新bot自己的情绪状态。

    逻辑：
    1. 检查用户消息是否触发了新的情绪
    2. 如果bot当前有负面情绪且用户没有安抚，情绪持续
    3. 如果用户安抚了，加速衰减负面情绪
    4. 自然衰减：负面情绪随时间减弱
    5. 情绪传染：用户情绪影响 bot 情绪（user_id 用于缓冲防抖）
    6. 渐进恢复：生气→傲娇→平静
    """
    old_mood = await get_bot_mood()
    now = time.time()

    # 计算旧情绪的衰减
    dt = now - old_mood.get("last_updated", now)
    duration = BOT_MOOD_DURATION.get(old_mood["dominant"], 600)

    # 自然衰减：超过持续时间后回归平静（渐进恢复）
    if dt > duration and old_mood["dominant"] != "平静":
        old_emotion = old_mood["dominant"]
        old_intensity = abs(old_mood.get("valence", 0.5))
        await update_bot_mood(0.0, 0.2, "平静", "自然消退")
        # 真人化 P3-4.2：记录情绪残留
        _record_emotion_recovery(old_emotion, old_intensity)
        logger.info(f"[Bot情绪] 自然消退: {old_mood['dominant']} -> 平静 (过了{int(dt)}秒)")
        result = {"dominant": "平静", "reason": "自然消退"}
        # 情绪传染：平静后也可能被用户情绪影响
        await _try_apply_contagion(result, user_emotion, old_mood, user_id)
        return result

    # 检查用户是否在安抚
    is_comforting = any(kw in user_msg for kw in _COMFORT_KEYWORDS)

    # 如果bot在负面情绪中且用户在安抚
    if old_mood["dominant"] in ("生气", "难过") and is_comforting:
        # 安抚后情绪减弱（缩短一半持续时间）
        decay_ratio = 0.5
        new_v = old_mood["valence"] * decay_ratio
        new_a = old_mood["arousal"] * decay_ratio
        if abs(new_v) < 0.15:
            old_emotion_name = old_mood["dominant"]
            old_intensity = abs(old_mood.get("valence", 0.5))
            await update_bot_mood(0.0, 0.2, "平静", "被安抚了")
            # 真人化 P3-4.2：记录情绪残留
            _record_emotion_recovery(old_emotion_name, old_intensity)
            logger.info(f"[Bot情绪] 被安抚消退: {old_mood['dominant']} -> 平静")
            return {"dominant": "平静", "reason": "被安抚了"}
        else:
            new_dominant = "傲娇" if old_mood["dominant"] == "生气" else "平静"
            await update_bot_mood(new_v, new_a, new_dominant, "被安抚了但还有点情绪")
            logger.info(f"[Bot情绪] 被安抚减弱: {old_mood['dominant']} -> {new_dominant}")
            return {"dominant": new_dominant, "reason": "被安抚了"}

    # 真人化 P1-1：关键词匹配降级为「仅记录不切换」，从累加器获取情绪状态
    # 消除 audit-2-1 的双重计算（关键词+LLM）
    triggered = None
    if old_mood["dominant"] == "平静" or dt > duration * 0.5:
        for emotion_name, trigger in _BOT_EMOTION_TRIGGERS.items():
            if any(kw in user_msg for kw in trigger["keywords"]):
                triggered = (emotion_name, trigger)
                break

    if triggered:
        emotion_name, trigger = triggered
        # 真人化 P1-1：关键词匹配改为喂入情绪累加器，不直接触发状态切换
        try:
            from .emotion_accumulator import quick_check_to_unit, get_accumulator
            from .database import get_session_id_for_user

            # 尝试获取 session_id
            acc_session_id = f"private_{user_id}" if user_id else ""
            if acc_session_id:
                unit = quick_check_to_unit(user_msg)
                accumulator = get_accumulator(acc_session_id)
                acc_result = accumulator.feed(unit)
                if acc_result:
                    # 累加器触发 → 切换到新情绪
                    new_emotion = acc_result["emotion"]
                    new_valence = acc_result["valence"]
                    new_arousal = acc_result["arousal"]
                    await update_bot_mood(new_valence, new_arousal, new_emotion, trigger["reason"])
                    logger.info(
                        f"[Bot情绪] 累加器触发: {emotion_name} → {new_emotion} "
                        f"(immediate={acc_result.get('immediate', False)})"
                    )
                    return {"dominant": new_emotion, "reason": trigger["reason"]}
                else:
                    # 累积中，暂不触发 → 保持当前状态
                    logger.debug(
                        f"[Bot情绪] 关键词命中但累积中: {emotion_name} "
                        f"(buffer={accumulator.buffer_size})"
                    )
            else:
                # 无 session_id，回退到直接触发
                await update_bot_mood(trigger["valence"], trigger["arousal"], emotion_name, trigger["reason"])
                logger.info(f"[Bot情绪] 触发新情绪（回退模式）: {emotion_name} ({trigger['reason']})")
                return {"dominant": emotion_name, "reason": trigger["reason"]}
        except Exception as e:
            # 累加器不可用时回退到直接触发
            logger.debug(f"[Bot情绪] 累加器不可用，回退直接触发: {e}")
            await update_bot_mood(trigger["valence"], trigger["arousal"], emotion_name, trigger["reason"])
            logger.info(f"[Bot情绪] 触发新情绪（降级模式）: {emotion_name} ({trigger['reason']})")
            return {"dominant": emotion_name, "reason": trigger["reason"]}

    # 检查累加器是否有待表现的延迟情绪（即使没有新关键词触发）
    if user_id:
        try:
            from .emotion_accumulator import get_accumulator
            acc_session_id = f"private_{user_id}"
            accumulator = get_accumulator(acc_session_id)
            if accumulator.is_pending:
                # 喂入一个 neutral unit 推进倒计时
                from .emotion_accumulator import EmotionUnit
                tick_unit = EmotionUnit(
                    label="neutral", valence=0.0, arousal=0.1,
                    intensity=0.05, confidence=0.1, source="tick",
                )
                acc_result = accumulator.feed(tick_unit)
                if acc_result:
                    new_emotion = acc_result["emotion"]
                    await update_bot_mood(
                        acc_result["valence"], acc_result["arousal"],
                        new_emotion, f"延迟情绪表现: {new_emotion}"
                    )
                    logger.info(f"[Bot情绪] 延迟情绪表现: {new_emotion}")
                    return {"dominant": new_emotion, "reason": f"延迟情绪: {new_emotion}"}
        except Exception:
            pass

    # 没有触发新情绪，返回当前状态（衰减后的）
    if old_mood["dominant"] != "平静":
        # 渐进恢复：检查是否处于恢复阶段
        recovery = get_gradual_recovery(old_mood["dominant"], old_mood.get("trigger_time", now), duration)
        if recovery:
            # 处于恢复阶段，返回恢复提示
            result = {
                "dominant": old_mood["dominant"],
                "reason": old_mood.get("trigger_reason", ""),
                "decaying": True,
                "recovery_stage": recovery["hint"],
                "recovery_label": recovery["stage_label"],
                "recovery_progress": recovery["progress"],
            }
            logger.info(f"[Bot情绪] 恢复中: {old_mood['dominant']} -> {recovery['stage_label']} ({recovery['progress']:.0%})")
            await _try_apply_contagion(result, user_emotion, old_mood, user_id)
            return result

        # 衰减旧情绪
        progress = dt / duration  # 0~1
        decayed_v = old_mood["valence"] * (1 - progress)
        decayed_a = old_mood["arousal"] * (1 - progress)
        if abs(decayed_v) < 0.1:
            old_emotion_name = old_mood["dominant"]
            old_intensity = abs(old_mood.get("valence", 0.5))
            await update_bot_mood(0.0, 0.2, "平静", "自然消退")
            # 真人化 P3-4.2：记录情绪残留
            _record_emotion_recovery(old_emotion_name, old_intensity)
            result = {"dominant": "平静", "reason": "自然消退"}
            await _try_apply_contagion(result, user_emotion, old_mood, user_id)
            return result
        result = {"dominant": old_mood["dominant"], "reason": old_mood.get("trigger_reason", ""), "decaying": True}
        await _try_apply_contagion(result, user_emotion, old_mood, user_id)
        return result

    # 平静状态：检查随机波动 + 情绪复发 + 情绪传染
    # B4 fix: 查询真实好感度，而非硬编码 0（修复高好感 mood 永不触发的问题）
    real_affection = 0
    if user_id:
        try:
            aff_data = await get_affection(str(user_id))
            real_affection = aff_data.get("score", 0)
        except Exception:
            pass
    swing = maybe_trigger_mood_swing(old_mood["dominant"], real_affection)
    if swing:
        await update_bot_mood(swing["valence"], swing["arousal"], swing["dominant"], swing["reason"])
        return {"dominant": swing["dominant"], "reason": swing["reason"], "swing_hint": swing.get("hint", "")}

    # 真人化 P3-4.2：检查情绪复发（rekindle）
    rekindle = _check_residue_rekindle()
    if rekindle:
        await update_bot_mood(
            rekindle.get("valence", 0), rekindle.get("arousal", 0.3),
            rekindle["emotion"], rekindle.get("reason", "情绪复发")
        )
        logger.info(f"[Bot情绪] 情绪复发: {rekindle['emotion']} (intensity={rekindle.get('intensity', 0):.2f})")
        return {"dominant": rekindle["emotion"], "reason": rekindle.get("reason", ""),
                "is_rekindle": True, "intensity": rekindle.get("intensity", 0)}

    result = {"dominant": "平静", "reason": ""}
    # 真人化 P3-4.2：附加残留提示
    residue_hint = _get_active_residue_hint()
    if residue_hint:
        result["residue_hint"] = residue_hint
    await _try_apply_contagion(result, user_emotion, old_mood, user_id)
    return result


# ============================================================
# 真人化 P3-4.2：情绪残留追踪（模块级，bot 全局情绪）
# ============================================================

# bot 情绪残留记录
_bot_residue_record: dict = {
    "emotion": "",           # 原始情绪标签
    "recovered_at": 0.0,     # 恢复时间戳
    "original_intensity": 0.0,  # 原始强度
}


def _record_emotion_recovery(emotion_label: str, original_intensity: float):
    """记录一次情绪恢复，开始残留追踪。"""
    import time as _time
    if emotion_label and emotion_label != "平静":
        _bot_residue_record["emotion"] = emotion_label
        _bot_residue_record["recovered_at"] = _time.time()
        _bot_residue_record["original_intensity"] = min(1.0, max(0.1, original_intensity))
        logger.debug(f"[情绪残留] 记录恢复: {emotion_label} (intensity={original_intensity:.2f})")


def _check_residue_rekindle() -> Optional[dict]:
    """检查 bot 是否有残留情绪复发。

    Returns:
        {"emotion": str, "valence": float, "arousal": float, "intensity": float, "reason": str} 或 None
    """
    from .emotion_deep import compute_residue_intensity, maybe_rekindle
    import time as _time

    if not _bot_residue_record["emotion"]:
        return None

    residue = compute_residue_intensity(
        _bot_residue_record["recovered_at"],
        _bot_residue_record["original_intensity"],
        _time.time(),
    )

    if residue < 0.02:
        return None

    hours_since = (_time.time() - _bot_residue_record["recovered_at"]) / 3600.0
    result = maybe_rekindle(_bot_residue_record["emotion"], residue, hours_since)
    if not result:
        return None

    # 添加 VA 坐标
    from .context_analyzer import EMOTION_VA_MAP
    va = EMOTION_VA_MAP.get(result["emotion"], (0.0, 0.3))
    result["valence"] = va[0] * result["intensity"]
    result["arousal"] = va[1] * result["intensity"]
    return result


def _get_active_residue_hint() -> str:
    """获取当前活跃的残留情绪提示文本。"""
    from .emotion_deep import compute_residue_intensity, get_residue_hint
    import time as _time

    if not _bot_residue_record["emotion"]:
        return ""

    residue = compute_residue_intensity(
        _bot_residue_record["recovered_at"],
        _bot_residue_record["original_intensity"],
        _time.time(),
    )

    if residue < 0.02:
        return ""

    return get_residue_hint(_bot_residue_record["emotion"], residue)


async def _try_apply_contagion(result: dict, user_emotion: EmotionState, old_mood: dict, user_id: str = ""):
    """尝试应用情绪传染到结果中（修改 result 字典）。

    使用 EmotionBuffer 防止单条消息误触发传染。
    """
    if user_emotion.confidence < 0.4:
        return

    # 有 user_id 时使用带缓冲的传染（需要连续 N 条同向情绪才触发）
    if user_id:
        try:
            user_label = user_emotion.dominant
            bot_mood_dict = {
                "valence": old_mood.get("valence", 0),
                "arousal": old_mood.get("arousal", 0.2),
                "dominant": old_mood.get("dominant", "平静"),
            }
            # 查询真实好感度（而非从 bot_mood 取不存在的 affection 键）
            real_affection = 0
            try:
                aff_data = await get_affection(str(user_id))
                real_affection = aff_data.get("score", 0)
            except Exception:
                pass
            buffered = apply_emotional_contagion_with_buffer(
                user_id, user_label, bot_mood_dict,
                real_affection,
            )
            if buffered:
                result["contagion"] = buffered
                return
        except Exception:
            pass  # fallback 到直接传染

    # 直接传染（无 user_id 或缓冲失败时的回退行为）
    contagion = apply_emotional_contagion(
        user_emotion.valence, user_emotion.arousal,
        old_mood.get("valence", 0), old_mood.get("arousal", 0.2),
        old_mood.get("dominant", "平静"),
    )
    if contagion:
        result["contagion"] = contagion
