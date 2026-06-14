"""P2-10: Qwen 情绪分类器链（keyword → qwen → llm 三层级联）。

策略：
1. Keyword 快速匹配（已有，context_analyzer）
2. Qwen3:0.5b 本地分类器（新增，不消耗 API 调用）
3. Full DeepSeek LLM 分析（已有，仅复杂/不确定时使用）

节省 API 调用成本，同时提升情绪检测精度。
"""
import asyncio
import json
import logging
import re
from typing import Optional

from nonebot import logger

# ============================================================
# Qwen 情绪分类器 prompt
# ============================================================

EMOTION_CLASSIFY_PROMPT = """你是一个情绪分析助手。分析用户消息的情绪，只输出 JSON。

情绪标签（只用这些）：
- 开心: 分享快乐、笑、哈哈、高兴
- 难过: 伤心、哭、难过、失落
- 生气: 愤怒、不满、抱怨、讨厌
- 担心: 担心、焦虑、紧张
- 期待: 期待、好奇、想要
- 撒娇: 撒娇、卖萌、请求
- 困惑: 疑问、不解、啥意思
- 平淡: 陈述事实、闲聊、没有明显情绪
- 傲娇: 嘴硬、口是心非

输出格式：{"emotion": "标签", "confidence": 0.0-1.0, "intensity": 0.0-1.0}

只输出 JSON，不要其他文字。"""

# ============================================================
# Keyword 快速匹配（零成本，作为第一层过滤）
# ============================================================

_KEYWORD_EMOTIONS = {
    "开心": ["哈哈", "笑死", "😂", "🤣", "开心", "高兴", "爽", "nice", "太棒了", "好耶", "嘿嘿"],
    "难过": ["😭", "哭", "难过", "伤心", "失落", "emo", "想哭", "泪", "抑郁"],
    "生气": ["滚", "烦", "气死", "讨厌", "傻逼", "无语", "离谱", "恼火", "暴躁"],
    "担心": ["担心", "怕", "焦虑", "紧张", "不安", "怎么办", "慌了"],
    "期待": ["期待", "想要", "好奇", "等不及", "快", "马上"],
    "撒娇": ["喵~", "呜呜", "哼", "人家", "要抱抱", "亲亲", "想你了"],
    "困惑": ["？", "什么", "怎么", "为啥", "啥意思", "不懂", "没明白"],
    "傲娇": ["才没有", "不关你事", "随便", "笨蛋", "哼", "白痴"],
}

# Qwen 分类器缓存（避免重复调用）
_classify_cache: dict = {}
_CACHE_MAX = 200


async def classify_emotion_qwen(user_msg: str) -> Optional[dict]:
    """P2-10: 使用 Qwen3:0.5b 本地分类器分析情绪。

    比 keyword 更准确，比 DeepSeek LLM 更便宜。

    Returns:
        {"emotion": str, "confidence": float, "intensity": float} 或 None
    """
    if not user_msg or len(user_msg.strip()) < 2:
        return None

    # Ollama 未启用时直接返回 None，级联自动回退到 keyword + DeepSeek
    from .config import OLLAMA_ENABLED
    if not OLLAMA_ENABLED:
        return None

    msg_key = user_msg.strip()[:100]
    if msg_key in _classify_cache:
        return _classify_cache[msg_key]

    try:
        from .local_llm import call_ollama_chat

        messages = [
            {"role": "system", "content": EMOTION_CLASSIFY_PROMPT},
            {"role": "user", "content": user_msg[:300]},
        ]

        raw = await call_ollama_chat(messages, temperature=0.1, max_tokens=100)
        if not raw:
            return None

        # 解析 JSON
        match = re.search(r'\{[^}]+\}', raw)
        if not match:
            return None

        result = json.loads(match.group())
        if not isinstance(result, dict) or "emotion" not in result:
            return None

        # 缓存结果
        if len(_classify_cache) >= _CACHE_MAX:
            oldest = next(iter(_classify_cache))
            del _classify_cache[oldest]
        _classify_cache[msg_key] = result

        return result

    except json.JSONDecodeError:
        return None
    except Exception as e:
        logger.debug(f"[EmotionQwen] 分类异常: {e}")
        return None


def classify_emotion_keyword(user_msg: str) -> Optional[dict]:
    """Keyword 快速情绪匹配（零成本第一层）。"""
    if not user_msg:
        return None

    msg = user_msg.strip()
    scores = {}

    for emotion, keywords in _KEYWORD_EMOTIONS.items():
        matches = sum(1 for kw in keywords if kw in msg)
        if matches > 0:
            # 匹配数越多，越可能是该情绪
            scores[emotion] = min(0.9, matches * 0.25)

    if not scores:
        return None

    # 取最高分
    best = max(scores, key=scores.get)
    return {
        "emotion": best,
        "confidence": scores[best],
        "intensity": min(1.0, scores[best] + 0.2),
        "source": "keyword",
    }


async def classify_emotion_cascade(user_msg: str, context_analysis=None) -> dict:
    """P2-10: 三层级联情绪分类器。

    1. Keyword 匹配 — 高置信度 (>0.7) 直接返回
    2. Qwen3:0.5b 本地分类 — 中等置信度
    3. 回退到已有 context_analysis

    Returns:
        {"emotion": str, "confidence": float, "intensity": float, "source": str}
    """
    # 第1层: Keyword 快速匹配
    kw_result = classify_emotion_keyword(user_msg)
    if kw_result and kw_result["confidence"] > 0.7:
        logger.debug(f"[情绪级联] keyword → {kw_result['emotion']} (conf={kw_result['confidence']:.2f})")
        return kw_result

    # 第2层: Qwen 本地分类器
    try:
        qwen_result = await asyncio.wait_for(
            classify_emotion_qwen(user_msg),
            timeout=3.0,  # 3秒超时，不阻塞主流程
        )
        if qwen_result and qwen_result.get("confidence", 0) > 0.5:
            qwen_result["source"] = "qwen"
            logger.debug(f"[情绪级联] qwen → {qwen_result['emotion']} (conf={qwen_result['confidence']:.2f})")
            return qwen_result
    except asyncio.TimeoutError:
        logger.debug("[情绪级联] qwen 超时，回退到 keyword/llm")
    except Exception as e:
        logger.debug(f"[情绪级联] qwen 异常: {e}")

    # 第3层: Keyword 低置信度结果 或 回退
    if kw_result:
        kw_result["source"] = "keyword_fallback"
        return kw_result

    # 最终回退：已有的 context_analysis
    if context_analysis and hasattr(context_analysis, 'emotion'):
        ea = context_analysis.emotion
        return {
            "emotion": getattr(ea, 'dominant', '平静'),
            "confidence": getattr(ea, 'confidence', 0.3),
            "intensity": abs(getattr(ea, 'valence', 0)),
            "source": "llm_fallback",
        }

    return {"emotion": "平淡", "confidence": 0.3, "intensity": 0.1, "source": "default"}


# 用于 handler 集成的便捷函数
async def get_emotion_enhanced(user_msg: str, context_analysis=None) -> str:
    """获取增强的情绪标签（字符串），供 handler 使用。"""
    result = await classify_emotion_cascade(user_msg, context_analysis)
    return result.get("emotion", "平静")
