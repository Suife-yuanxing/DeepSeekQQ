"""3D Think-then-Speak 双通道回复生成。

借鉴 PersonaForge ACL 2026 的"内部思考→外部表达"双阶段范式：
1. Think（思考）: 先做一次轻量 LLM 调用，分析用户意图、情绪、最佳回应策略
2. Speak（表达）: 将思考结果注入 system prompt，生成最终回复

触发条件：
- 复杂消息（complexity != "simple"）
- 消息长度 >= 5 字符
- 非纯指令性消息
- 可配置开关 THINK_THEN_SPEAK_ENABLED
"""
import logging
from typing import Dict
from typing import List
from typing import Optional

from .config import getattr as _cfg_getattr

logger = logging.getLogger("deepseek.thinker")

# === 配置 ===
THINK_THEN_SPEAK_ENABLED: bool = True  # 总开关
THINK_MAX_TOKENS: int = 200            # 思考阶段输出上限（节省成本）
THINK_TEMPERATURE: float = 0.3         # 思考阶段温度（低温度=更理性）
THINK_MIN_MSG_LENGTH: int = 5          # 短于此长度的消息跳过思考
THINK_SKIP_INTENTS = {"指令", "闲聊"}   # 这些意图跳过思考（简单回复不需要）


def should_think(user_msg: str, complexity: str, user_intent: str) -> bool:
    """判断是否需要触发思考阶段。

    Args:
        user_msg: 用户原始消息
        complexity: 消息复杂度 (simple/normal/complex)
        user_intent: 用户意图标签

    Returns:
        True 如果应该触发思考
    """
    if not THINK_THEN_SPEAK_ENABLED:
        return False
    if complexity == "simple":
        return False
    if len(user_msg.strip()) < THINK_MIN_MSG_LENGTH:
        return False
    if user_intent in THINK_SKIP_INTENTS:
        # 闲聊和简单指令不需要深度思考
        return False
    # 提问、分享、情绪表达 → 值得思考
    return True


def build_think_prompt(
    user_msg: str,
    recent_history: List[Dict[str, str]],
    emotion_dominant: str,
    topic_summary: str,
    user_intent: str,
    affection_score: int = 0,
) -> str:
    """构建思考阶段的 prompt。

    思考阶段关注：
    - 用户真正想表达什么（表层 vs 深层）
    - 有哪些可能的回应方向
    - 最佳回应策略是什么
    """
    # 最近3条历史
    history_text = ""
    for m in recent_history[-6:]:
        role = "用户" if m.get("role") == "user" else "念念"
        content = m.get("content", "")[:80]
        history_text += f"{role}：{content}\n"

    affection_desc = _describe_affection(affection_score)

    return f"""你正在准备回复用户消息。先快速分析一下：

【最近对话】
{history_text}

【用户最新消息】
{user_msg}

【背景信息】
- 用户意图: {user_intent}
- 当前话题: {topic_summary or "无"}
- 用户情绪: {emotion_dominant}
- 好感度: {affection_desc}

请在50字以内快速回答以下3个问题（用JSON格式）：
1. subtext: 用户这句话的深层含义/潜台词是什么？
2. strategy: 最佳回应策略（如：共情/调侃/追问/分析/安慰/分享）
3. tone_hint: 回复语气建议（如：温柔一点/活泼一点/认真一点/傲娇一点）

只输出JSON，不要其他文字：
```json
{{"subtext":"...", "strategy":"...", "tone_hint":"..."}}
```"""


def format_think_result(think_raw: str) -> str:
    """将思考结果格式化为 system prompt 注入文本。

    Args:
        think_raw: LLM 返回的思考原始文本

    Returns:
        格式化的注入文本，失败返回空字符串
    """
    if not think_raw or len(think_raw) < 10:
        return ""

    import json as _json
    import re as _re

    from .utils import clean_json_text

    try:
        clean = clean_json_text(think_raw)
        match = _re.search(r'\{[\s\S]*\}', clean)
        if match:
            data = _json.loads(match.group())
            subtext = data.get("subtext", "")
            strategy = data.get("strategy", "")
            tone_hint = data.get("tone_hint", "")

            parts = []
            if subtext:
                parts.append(f"用户深层含义：{subtext}")
            if strategy:
                parts.append(f"回应策略：{strategy}")
            if tone_hint:
                parts.append(f"语气建议：{tone_hint}")

            if parts:
                return "【内部思考】" + "；".join(parts) + "。（自然融入回复，不要说出来）"
    except (_json.JSONDecodeError, KeyError, IndexError) as e:
        logger.debug(f"[思考] JSON解析失败: {e}")

    # JSON 解析失败，用原文前100字
    clean_text = think_raw.strip()[:100]
    if clean_text:
        return f"【内部思考】{clean_text}（自然融入回复，不要说出来）"
    return ""


def _describe_affection(score: int) -> str:
    """好感度描述。"""
    if score >= 500:
        return "非常亲密，无话不谈"
    elif score >= 200:
        return "比较亲密，已经熟悉"
    elif score >= 100:
        return "逐渐熟悉中"
    elif score >= 50:
        return "有点在意对方"
    elif score >= 20:
        return "认识不久"
    else:
        return "陌生人"
