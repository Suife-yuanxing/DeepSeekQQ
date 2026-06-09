"""网络梗词典模块。

功能：
- 维护精选网络梗词典
- 根据情绪、好感度、话题匹配合适的梗
- 低概率注入到 prompt 中，让 LLM 自然运用
"""
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ============================================================
# 网络梗词典
# ============================================================

MEMES = [
    {
        "word": "绝绝子",
        "meaning": "太绝了/太好了",
        "example": "你这波操作绝绝子",
        "mood": ["开心", "兴奋"],
        "affection_min": 0,
        "keywords": ["厉害", "棒", "强", "好", "赞"],
    },
    {
        "word": "破防了",
        "meaning": "心理防线被突破，被感动或被打击",
        "example": "你这么说我都破防了",
        "mood": ["感动", "难过"],
        "affection_min": 0,
        "keywords": ["感动", "伤心", "哭", "心疼"],
    },
    {
        "word": "真香",
        "meaning": "之前拒绝后来接受（打脸定律）",
        "example": "之前说不想玩现在不是在玩吗，真香",
        "mood": ["傲娇", "得意"],
        "affection_min": 20,
        "keywords": ["不要", "不想", "算了", "后来", "其实"],
    },
    {
        "word": "CPU干烧了",
        "meaning": "脑子转不过来/想不通",
        "example": "等等让我想想，CPU干烧了",
        "mood": ["困惑", "无语"],
        "affection_min": 0,
        "keywords": ["什么", "为什么", "怎么", "想不通", "不懂"],
    },
    {
        "word": "DNA动了",
        "meaning": "本能反应被触发",
        "example": "看到好吃的DNA动了",
        "mood": ["兴奋", "期待"],
        "affection_min": 0,
        "keywords": ["好吃", "想要", "喜欢", "看到", "心动"],
    },
    {
        "word": "嘴替",
        "meaning": "说出了我想说的话",
        "example": "你就是我的嘴替",
        "mood": ["认同", "开心"],
        "affection_min": 50,
        "keywords": ["对", "没错", "是的", "说得对", "就是"],
    },
    {
        "word": "薄纱",
        "meaning": "轻松碾压/秒杀",
        "example": "这波薄纱对面",
        "mood": ["兴奋", "崇拜"],
        "affection_min": 0,
        "keywords": ["赢", "赢了", "打败", "厉害", "第一"],
    },
    {
        "word": "寄",
        "meaning": "完了/凉了/没救了",
        "example": "这下寄了",
        "mood": ["无语", "难过"],
        "affection_min": 0,
        "keywords": ["完了", "失败", "不行", "糟糕", "完蛋"],
    },
    {
        "word": "摆烂",
        "meaning": "放弃努力/躺平",
        "example": "今天不想动，摆烂了",
        "mood": ["无聊", "傲娇"],
        "affection_min": 20,
        "keywords": ["累", "不想", "算了", "随便", "放弃"],
    },
    {
        "word": "卷",
        "meaning": "内卷/过度竞争",
        "example": "你也太卷了吧",
        "mood": ["无语", "嫌弃"],
        "affection_min": 20,
        "keywords": ["努力", "学习", "工作", "加班", "拼命"],
    },
    {
        "word": "整活",
        "meaning": "搞事情/玩花样",
        "example": "你又要整活了是吧",
        "mood": ["兴奋", "期待"],
        "affection_min": 0,
        "keywords": ["玩", "搞", "试试", "试试看", "新"],
    },
    {
        "word": "YYDS",
        "meaning": "永远的神（极度夸赞）",
        "example": "你做的这个YYDS",
        "mood": ["崇拜", "兴奋"],
        "affection_min": 0,
        "keywords": ["厉害", "强", "好", "棒", "优秀"],
    },
    {
        "word": "遥遥领先",
        "meaning": "领先很多（调侃式夸赞）",
        "example": "你这技术遥遥领先啊",
        "mood": ["开心", "调侃"],
        "affection_min": 50,
        "keywords": ["厉害", "强", "第一", "最好"],
    },
    {
        "word": "双向奔赴",
        "meaning": "互相付出/互相喜欢",
        "example": "我们这算双向奔赴吗",
        "mood": ["感动", "害羞"],
        "affection_min": 100,
        "keywords": ["喜欢", "爱", "一起", "互相"],
    },
    {
        "word": "搭子",
        "meaning": "一起做某事的伙伴",
        "example": "你是我最好的聊天搭子",
        "mood": ["开心", "日常"],
        "affection_min": 50,
        "keywords": ["一起", "聊天", "玩", "陪伴"],
    },
    {
        "word": "已读不回",
        "meaning": "看了消息不回复",
        "example": "你已读不回是不是不爱我了",
        "mood": ["委屈", "撒娇"],
        "affection_min": 100,
        "keywords": ["没回", "不理", "不回", "忙"],
    },
    {
        "word": "精神状态良好",
        "meaning": "反话，表示精神状态不稳定/发疯中",
        "example": "今天精神状态良好（发疯中）",
        "mood": ["无语", "搞笑"],
        "affection_min": 0,
        "keywords": ["累", "崩溃", "疯", "无语", "烦"],
    },
    {
        "word": "i人",
        "meaning": "内向的人/社恐",
        "example": "你肯定是i人吧，这么安静",
        "mood": ["日常", "调侃"],
        "affection_min": 0,
        "keywords": ["安静", "不说话", "社恐", "内向", "宅"],
    },
]


# ============================================================
# 梗选择逻辑
# ============================================================

# 各好感度等级的触发概率
_MEME_TRIGGER_RATES = [
    (200, 0.35),   # 亲密：35%
    (50, 0.25),    # 熟人：25%
    (20, 0.15),    # 认识：15%
    (0, 0.05),     # 陌生人：5%
]


def pick_meme(
    user_msg: str,
    emotion_state=None,
    bot_mood: Dict[str, Any] = None,
    affection_score: float = 0,
) -> Optional[Dict[str, str]]:
    """根据当前对话状态选择一个合适的梗。

    触发条件（全部满足）：
    1. 概率检查通过
    2. 情绪匹配
    3. 好感度匹配
    4. 话题/关键词匹配（加分项）

    Returns:
        梗字典 {"word": ..., "meaning": ..., "example": ...} 或 None
    """
    # 概率检查
    trigger_rate = 0.05
    for threshold, rate in _MEME_TRIGGER_RATES:
        if affection_score >= threshold:
            trigger_rate = rate
            break

    if random.random() > trigger_rate:
        return None

    # 获取当前情绪标签
    current_moods = []
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        current_moods.append(bot_mood["dominant"])
    if emotion_state and emotion_state.confidence >= 0.4:
        # 从 VA 映射到情绪标签
        v, a = emotion_state.valence, emotion_state.arousal
        if v > 0.3:
            current_moods.append("开心")
        elif v < -0.3:
            current_moods.append("难过")
        if a > 0.7:
            current_moods.append("兴奋")
        if emotion_state.dominant:
            current_moods.append(emotion_state.dominant)

    # 筛选候选梗
    candidates = []
    for meme in MEMES:
        # 好感度过滤
        if affection_score < meme["affection_min"]:
            continue

        score = 0

        # 情绪匹配（+3 分）
        if current_moods and any(m in meme["mood"] for m in current_moods):
            score += 3

        # 关键词匹配（+2 分）
        if any(kw in user_msg for kw in meme.get("keywords", [])):
            score += 2

        # 有匹配就加入候选
        if score > 0:
            candidates.append((score, meme))

    if not candidates:
        return None

    # 按分数排序，取 top 3 中随机选一个
    candidates.sort(key=lambda x: x[0], reverse=True)
    top = candidates[:3]
    chosen = random.choice(top)[1]

    logger.info(f"[梗] 触发: {chosen['word']} (情绪={current_moods}, 好感={affection_score:.0f})")
    return chosen
