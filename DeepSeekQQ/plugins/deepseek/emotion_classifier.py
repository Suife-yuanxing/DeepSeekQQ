"""情绪分级处理 — 规则粗筛 + LLM细判，降低API调用成本。"""
import re
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

from nonebot import logger

# 情绪关键词库（轻量级规则判断）
EMOTION_KEYWORDS = {
    'angry': ['滚', '有病', '烦死了', '你够了', '闭嘴', '白痴', '废物', '垃圾', '去死', '傻逼'],
    'anxious': ['急', '快', '怎么办', '来不及', '害怕', '担心', '紧张', '完了', '糟糕'],
    'positive': ['开心', '高兴', '哈哈', '嘿嘿', '太好了', '棒', '喜欢', '爱', '❤️', '😊', '🎉', '好耶', '赞'],
    'negative': ['难过', '伤心', '生气', '好烦', '烦死了', '讨厌', '累', '不想', '算了', '💔', '😢', '😠', '唉'],
    # Bug 10 修复：移除单字 '烦'（会误匹配"麻烦你"等礼貌请求），改为更精确的词组
    'excited': ['啊啊啊', '太棒了', '牛逼', '666', '厉害', '绝了', '好强', '冲冲冲'],
    'shy': ['害羞', '不好意思', '>//<', '讨厌啦', '别说了', '羞'],
}


def quick_emotion_check(text: str) -> Tuple[Optional[str], float]:
    """规则快速判断情绪（<1ms），返回 (情绪标签, 置信度)"""
    text_lower = text.lower()

    # 检查愤怒（优先级最高）
    angry_hits = sum(1 for kw in EMOTION_KEYWORDS['angry'] if kw in text_lower)
    if angry_hits >= 2:
        return 'angry', 0.9
    if angry_hits == 1 and len(text) < 10:  # 短消息+脏话=高概率愤怒
        return 'angry', 0.8

    # 检查焦虑
    anxious_hits = sum(1 for kw in EMOTION_KEYWORDS['anxious'] if kw in text_lower)
    if anxious_hits >= 2:
        return 'anxious', 0.7

    # 检查兴奋
    excited_hits = sum(1 for kw in EMOTION_KEYWORDS['excited'] if kw in text_lower)
    if excited_hits >= 2:
        return 'excited', 0.8

    # 检查害羞
    shy_hits = sum(1 for kw in EMOTION_KEYWORDS['shy'] if kw in text_lower)
    if shy_hits >= 1:
        return 'shy', 0.7

    # 检查正面/负面倾向
    pos_hits = sum(1 for kw in EMOTION_KEYWORDS['positive'] if kw in text_lower)
    neg_hits = sum(1 for kw in EMOTION_KEYWORDS['negative'] if kw in text_lower)

    if pos_hits > neg_hits + 1:
        return 'positive', 0.6
    if neg_hits > pos_hits + 1:
        return 'negative', 0.6

    # 无法快速判断
    return None, 0.0


def classify_text_emotion(text: str, use_llm: bool = True) -> Dict[str, Any]:
    """两级情绪判断：规则粗筛 + LLM细判

    Returns:
        {"emotion": str, "confidence": float, "source": "keyword"|"llm"}
    """
    # 第一级：规则快速判断
    quick_emotion, quick_confidence = quick_emotion_check(text)

    # 高置信度直接返回，省去LLM调用
    if quick_confidence >= 0.8:
        return {
            'emotion': quick_emotion,
            'confidence': quick_confidence,
            'source': 'keyword'
        }

    # 第二级：LLM细判（未来可接入BERT轻量模型，当前回退到规则结果）
    # TODO: 接入轻量级情感分类模型替代规则匹配

    # 回退到规则结果
    return {
        'emotion': quick_emotion or 'neutral',
        'confidence': quick_confidence or 0.3,
        'source': 'keyword'
    }


# 情绪传染防抖缓冲区
class EmotionBuffer:
    """情绪传染缓冲区，防止单条消息误触发传染"""

    def __init__(self, window_size: int = 5, threshold: int = 3):
        self.window_size = window_size
        self.threshold = threshold  # 连续N条同向情绪才触发传染
        self.history: list = []

    def add_emotion(self, emotion: str):
        """添加一条情绪记录"""
        self.history.append(emotion)
        # 保持窗口大小
        if len(self.history) > self.window_size:
            self.history = self.history[-self.window_size:]

    def should_contagion(self) -> Tuple[bool, str]:
        """判断是否应该触发传染"""
        if len(self.history) < self.threshold:
            return False, ''

        # 检查最近N条是否同向
        recent = self.history[-self.threshold:]

        # 情绪分类
        positive_emotions = {'happy', 'excited', 'positive', '开心', '兴奋'}
        negative_emotions = {'sad', 'angry', 'anxious', 'negative', '难过', '生气'}

        pos_count = sum(1 for e in recent if e in positive_emotions)
        neg_count = sum(1 for e in recent if e in negative_emotions)

        if pos_count >= self.threshold:
            return True, 'positive'
        if neg_count >= self.threshold:
            return True, 'negative'

        return False, ''


# 全局缓冲区管理
_emotion_buffers: Dict[str, EmotionBuffer] = {}


def get_emotion_buffer(user_id: str) -> EmotionBuffer:
    """获取用户的情绪缓冲区"""
    if user_id not in _emotion_buffers:
        _emotion_buffers[user_id] = EmotionBuffer()
    return _emotion_buffers[user_id]


def apply_emotional_contagion_with_buffer(
    user_id: str,
    user_emotion: str,
    bot_mood: Dict[str, Any],
    affection: float
) -> Optional[Dict[str, float]]:
    """带缓冲的情绪传染。

    每个用户独立缓冲区：需要同一用户连续N条同向情绪消息才触发传染，
    避免单条消息误触发。

    Args:
        user_id: 用户唯一标识（QQ号）
        user_emotion: 用户当前情绪标签
        bot_mood: bot 当前情绪状态
        affection: 好感度分数
    """
    buffer = get_emotion_buffer(user_id)
    buffer.add_emotion(user_emotion)

    should_contagion, contagion_type = buffer.should_contagion()

    if not should_contagion:
        return None  # 不触发传染

    # 调用原有的传染逻辑
    from .emotion_deep import apply_emotional_contagion
    user_valence = 0.5 if contagion_type == 'positive' else -0.5
    user_arousal = 0.6

    return apply_emotional_contagion(
        user_valence=user_valence,
        user_arousal=user_arousal,
        bot_valence=bot_mood.get('valence', 0.0),
        bot_arousal=bot_mood.get('arousal', 0.5),
        bot_dominant=bot_mood.get('dominant', '平静'),
        affection_score=affection
    )
