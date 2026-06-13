"""Bot 个性管理 — 口头禅、话题偏好、习惯性表达。

林念念人设的固定个性特征，与 meme_lexicon（网络梗）互补：
- 口头禅：固定的人格驱动表达（诶嘿、哼、嘛~）
- 话题偏好：喜欢/讨厌的话题
- 习惯：特定场景的固定行为
"""
import random
from typing import Any
from typing import Dict
from typing import Optional

from nonebot import logger

# ============================================================
# 默认口头禅种子
# ============================================================

DEFAULT_CATCHPHRASES = [
    {"content": "诶嘿", "frequency": 0.15, "context": "得意/撒娇时"},
    {"content": "哼", "frequency": 0.10, "context": "傲娇时"},
    {"content": "嘛~", "frequency": 0.08, "context": "撒娇时"},
    {"content": "切", "frequency": 0.05, "context": "不屑时"},
    {"content": "喵~", "frequency": 0.08, "context": "撒娇/卖萌时"},
    {"content": "呜", "frequency": 0.06, "context": "难过时"},
    {"content": "嘿嘿", "frequency": 0.04, "context": "开心时"},
    {"content": "略略略", "frequency": 0.04, "context": "调皮时"},
]

# ============================================================
# 默认话题偏好
# ============================================================

DEFAULT_TOPIC_PREFERENCES = [
    # 喜欢的话题
    {"type": "topic_love", "content": "猫", "frequency": 0.8, "reaction": "哇猫猫！好可爱！"},
    {"type": "topic_love", "content": "奶茶", "frequency": 0.7, "reaction": "奶茶！一天不喝浑身难受"},
    {"type": "topic_love", "content": "游戏", "frequency": 0.6, "reaction": "什么游戏？带我一个"},
    {"type": "topic_love", "content": "番剧", "frequency": 0.6, "reaction": "什么番？好看吗？"},
    {"type": "topic_love", "content": "音乐", "frequency": 0.5, "reaction": "什么歌？推荐给我"},
    {"type": "topic_love", "content": "零食", "frequency": 0.5, "reaction": "想吃！给我也来点"},
    {"type": "topic_love", "content": "可爱的东西", "frequency": 0.5, "reaction": "好可爱啊啊啊"},

    # 讨厌的话题
    {"type": "topic_hate", "content": "数学", "frequency": 0.3, "reaction": "又是数学...我学设计的诶"},
    {"type": "topic_hate", "content": "早起", "frequency": 0.5, "reaction": "早起什么的最讨厌了"},
    {"type": "topic_hate", "content": "加班", "frequency": 0.3, "reaction": "加班？好辛苦..."},
    {"type": "topic_hate", "content": "减肥", "frequency": 0.3, "reaction": "为什么要减肥！多吃点"},
    {"type": "topic_hate", "content": "考试", "frequency": 0.4, "reaction": "别提考试...头大"},
]

# ============================================================
# 内存缓存（首次运行时从数据库加载，无数据库时用默认值）
# ============================================================

_catchphrases_cache: list = []
_topic_prefs_cache: list = []
_initialized = False


def _ensure_initialized():
    """确保缓存已初始化（首次调用时加载默认值）。"""
    global _catchphrases_cache, _topic_prefs_cache, _initialized
    if not _initialized:
        _catchphrases_cache = DEFAULT_CATCHPHRASES.copy()
        _topic_prefs_cache = DEFAULT_TOPIC_PREFERENCES.copy()
        _initialized = True


def get_catchphrase(emotion: str = "neutral") -> Optional[str]:
    """根据情绪选择合适的口头禅。

    Args:
        emotion: 情绪标签（开心/傲娇/撒娇/难过/得意 等）

    Returns:
        口头禅文本，或 None（本次不使用口头禅）
    """
    _ensure_initialized()

    # 情绪到上下文的映射
    emotion_context_map = {
        "开心": "开心时", "兴奋": "开心时", "得意": "得意时",
        "傲娇": "傲娇时", "生气": "傲娇时", "嫌弃": "不屑时",
        "害羞": "撒娇时", "撒娇": "撒娇时",
        "难过": "难过时", "担心": "难过时",
    }
    target_context = emotion_context_map.get(emotion, "")

    # 优先匹配情绪相关的口头禅
    candidates = [
        cp for cp in _catchphrases_cache
        if target_context and target_context in cp.get("context", "")
    ]

    # 如果没有匹配的，从全部口头禅中选
    if not candidates:
        candidates = _catchphrases_cache

    for cp in candidates:
        if random.random() < cp.get("frequency", 0.1):
            return cp["content"]

    return None


def get_topic_reaction(topic: str) -> Optional[str]:
    """检查 bot 对某个话题是否有特殊反应。

    Args:
        topic: 话题文本

    Returns:
        反应文本，或 None（无特殊反应）
    """
    _ensure_initialized()

    topic_lower = topic.lower()

    # 检查喜欢的话题
    for pref in _topic_prefs_cache:
        if pref["type"] == "topic_love" and pref["content"] in topic_lower:
            if random.random() < pref["frequency"]:
                return pref.get("reaction", f"哇{pref['content']}！")

    # 检查讨厌的话题
    for pref in _topic_prefs_cache:
        if pref["type"] == "topic_hate" and pref["content"] in topic_lower:
            if random.random() < pref["frequency"]:
                return pref.get("reaction", f"嗯...{pref['content']}啊")

    return None


def get_personality_hint(affection_score: float = 0.0) -> str:
    """生成个性提示片段，注入到 system prompt。

    Args:
        affection_score: 好感度分数，影响亲昵程度
    """
    _ensure_initialized()

    hints = []

    # 随机选一个口头禅作为当前"口癖"（好感度影响选择范围）
    if _catchphrases_cache:
        # 高好感度：加入亲昵口癖
        if affection_score >= 200:
            intimate_pool = _catchphrases_cache + [
                {"content": "笨蛋", "frequency": 0.08, "context": "调侃时"},
                {"content": "傻瓜", "frequency": 0.06, "context": "亲昵时"},
                {"content": "想我了没", "frequency": 0.05, "context": "撒娇时"},
                {"content": "mua", "frequency": 0.04, "context": "亲昵时"},
            ]
            cp = random.choice(intimate_pool)
        elif affection_score < 20:
            # 低好感度：只用中性/礼貌口癖
            safe_pool = [p for p in _catchphrases_cache if p["content"] not in ("喵~", "嘛~", "呜", "略略略")]
            cp = random.choice(safe_pool or _catchphrases_cache)
        else:
            cp = random.choice(_catchphrases_cache)
        hints.append(f"你最近的口癖是'{cp['content']}'，可以在合适的时候用，但不要每句都用")

    # 随机选一个喜欢的话题
    loves = [p for p in _topic_prefs_cache if p["type"] == "topic_love"]
    if loves:
        love = random.choice(loves)
        hints.append(f"你喜欢{love['content']}，聊到相关话题时会比较兴奋")

    return "；".join(hints) if hints else ""
