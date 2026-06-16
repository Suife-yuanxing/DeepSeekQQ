"""拟人化处理 — 错别字纠正、改变主意、不确定表达、节奏增强、颜文字。"""
import random
import re
from typing import Dict
from typing import List
from typing import Optional

_TYPO_PAIRS = [
    ("的", "地"), ("怎么", "这么"), ("觉得", "决得"),
    ("好像", "号像"), ("不是", "不四"), ("真的", "真地"),
    ("可以", "可一"), ("有点", "有点点"),  # 修复："有点电"不是合理中文
    ("知道", "知到"), ("突然", "突燃"),  # 新增合理错字对
]


def introduce_typo(text: str) -> str:
    """插入一个错别字并自我纠正。"""
    if len(text) < 8:
        return text
    pairs = list(_TYPO_PAIRS)
    random.shuffle(pairs)
    for correct, typo in pairs:
        if correct in text:
            text = text.replace(correct, typo, 1)
            correctors = [
                f" 啊不对，{correct}",
                f" ...打错了，{correct}",
                f" 呃不是，{correct}",
            ]
            text += random.choice(correctors)
            return text
    return text


# ============================================================
# 口吃/重复字符效果
# ============================================================

# 句首可重复的单字（中文常见口吃模式）
_STUTTER_STARTERS = set("我你就这也那还不过可要是有对好真太")

# 语气词重复池
_STUTTER_INTERJECTIONS = ["嗯", "哈", "啊", "呃", "唔"]

# 否定重复池
_STUTTER_NEGATIONS = ["不", "没"]


def introduce_stutter(text: str, arousal: float = 0.5) -> str:
    """模拟口吃/重复字符效果。

    触发条件由调用方控制（3%基础概率，高arousal翻倍到6%）。
    与 typo 互斥（调用方保证同一消息只触发一种）。
    """
    if len(text) < 4:
        return text

    roll = random.random()

    # 30%: 句首重复（第一个字重复2-3次）
    if roll < 0.30 and text[0] in _STUTTER_STARTERS:
        repeat_count = random.randint(2, 3)
        return text[0] * repeat_count + text[1:]

    # 25%: 语气词重复（嗯嗯嗯、哈哈哈哈）
    if roll < 0.55:
        for interj in _STUTTER_INTERJECTIONS:
            if interj in text[:8]:
                count = random.randint(3, 6)
                return text.replace(interj, interj * count, 1)

    # 25%: 否定重复（不不不、没有没有）
    if roll < 0.80:
        for neg in _STUTTER_NEGATIONS:
            idx = text.find(neg)
            if idx >= 0 and idx < 8:
                if neg == "不" and text[idx:idx+2] not in ("不是", "不过", "不然"):
                    count = random.randint(3, 5)
                    return text[:idx] + neg * count + text[idx+1:]
                elif neg == "没" and text[idx:idx+2] in ("没有", "没想", "没关"):
                    return text[:idx] + "没" * random.randint(3, 4) + text[idx+1:]

    # fallthrough: 如果前三类都不匹配，随机选一个重分布（不再空操作）
    subtarget = random.random()
    if subtarget < 0.5:
        # 句首重复
        if text[0] in _STUTTER_STARTERS:
            repeat_count = random.randint(2, 3)
            return text[0] * repeat_count + text[1:]
    else:
        # 语气词重复
        for interj in _STUTTER_INTERJECTIONS:
            if interj in text[:8]:
                count = random.randint(3, 6)
                return text.replace(interj, interj * count, 1)

    return text


_MIND_CHANGE_PIVOTS = [
    "等等，其实...",
    "算了不说了，",
    "嗯让我想想...",
    "不对不对，",
    "等下，",
    "啊算了，",
]


def introduce_mind_change(text: str) -> str:
    """模拟改变主意或犹豫。"""
    if len(text) < 10:
        return text
    return random.choice(_MIND_CHANGE_PIVOTS) + text[0].lower() + text[1:]


_UNCERTAINTY_PREFIXES = [
    "不太确定但...",
    "好像是...",
    "我记得大概是...",
    "印象中...",
    "感觉...",
]


def introduce_uncertainty(text: str) -> str:
    """添加自然的不确定前缀。"""
    return random.choice(_UNCERTAINTY_PREFIXES) + text[0].lower() + text[1:]


# ============================================================
# 节奏增强：反应词前缀 + 连发拆分
# ============================================================

# 反应词：根据情绪状态在回复前加一个短词
_REACTION_PREFIXES_POSITIVE = ["诶", "哦？", "嗯~", "噢", "诶嘿"]
_REACTION_PREFIXES_NEGATIVE = ["呃", "啊...", "嗯。", "噢。"]
_REACTION_PREFIXES_NEUTRAL = ["哦", "嗯", "噢", "啊"]

# 上下文感知反应词（根据语义选择）
REACTION_WORDS = {
    'question': ['诶？', '嗯？', '哦？', '啊？'],
    'surprise': ['哇', '诶嘿', '哦~', '天哪'],
    'thinking': ['嗯...', '唔...', '这个嘛...', '我想想...'],
    'agreement': ['嗯嗯', '对对', '是呢', '没错'],
    'realization': ['哦~', '原来如此', '懂了', '这样啊'],
    'hesitation': ['emmm', '额...', '那个...', '怎么说呢...'],
}


def select_contextual_reaction(
    user_message: str,
    bot_reply: str,
    emotion: str
) -> Optional[str]:
    """根据上下文选择反应词"""
    # 检测用户消息类型
    is_question = '?' in user_message or '？' in user_message
    is_surprise = any(kw in user_message for kw in ['居然', '竟然', '没想到', '天哪'])
    is_sharing = len(user_message) > 30  # 长消息通常是分享

    # 检测bot回复内容
    reply_is_answer = any(kw in bot_reply for kw in ['因为', '所以', '其实', '就是'])
    reply_is_agreement = any(kw in bot_reply for kw in ['对', '没错', '是的', '嗯'])

    # 选择反应词
    if is_question and reply_is_answer:
        category = 'thinking'
    elif is_surprise:
        category = 'surprise'
    elif is_sharing:
        category = 'realization'
    elif reply_is_agreement:
        category = 'agreement'
    elif emotion in ('hesitant', 'confused'):
        category = 'hesitation'
    else:
        # 默认：不加反应词
        return None

    # 从对应类别随机选择
    reactions = REACTION_WORDS.get(category, [])
    if reactions:
        return random.choice(reactions)

    return None


def maybe_add_reaction_prefix(text: str, emotion_valence: float = 0.0,
                               user_message: str = "", emotion: str = "平静",
                               affection_score: float = 0.0) -> str:
    """10% 概率在回复前加一个反应词前缀（上下文感知版）。

    模拟真人看到消息后的第一反应：
    - "诶？你怎么知道的"
    - "噢对对对"
    - "嗯...让我想想"

    好感度影响反应词频率：高好感更随意，低好感更克制。
    """
    from .config import HUMANIZE_REACTION_PREFIX_HIGH, HUMANIZE_REACTION_PREFIX_MID, HUMANIZE_REACTION_PREFIX_LOW
    # 好感度修正触发概率
    if affection_score >= 200:
        trigger_chance = HUMANIZE_REACTION_PREFIX_HIGH  # 熟人更随意
    elif affection_score < 20:
        trigger_chance = HUMANIZE_REACTION_PREFIX_LOW  # 生人保持礼貌
    else:
        trigger_chance = HUMANIZE_REACTION_PREFIX_MID

    if random.random() > trigger_chance:
        return text
    if len(text) < 5:
        return text

    # 优先使用上下文感知反应词
    if user_message:
        contextual = select_contextual_reaction(user_message, text, emotion)
        if contextual:
            sep = random.choice([" ", "，"])
            return contextual + sep + text

    # 回退到原有逻辑
    if emotion_valence > 0.2:
        prefix = random.choice(_REACTION_PREFIXES_POSITIVE)
    elif emotion_valence < -0.2:
        prefix = random.choice(_REACTION_PREFIXES_NEGATIVE)
    else:
        prefix = random.choice(_REACTION_PREFIXES_NEUTRAL)

    # 前缀和正文之间加空格或逗号
    sep = random.choice([" ", "，"])
    return prefix + sep + text


def maybe_split_to_bursts(text: str, emotion_arousal: float = 0.5,
                          emotion_valence: float = 0.0) -> List[str]:
    """根据情绪状态决定是否将回复拆成多条连发消息。

    兴奋时 15% 概率连发 2-3 条，模拟真人追着说。
    Returns: 空列表表示不拆分。
    """
    from .dialogue_rhythm import should_split_to_bursts
    return should_split_to_bursts(text, emotion_arousal, emotion_valence)


# ============================================================
# 颜文字系统 — 根据情绪添加表情符号
# ============================================================

_KAOMOJI_HAPPY = ["qwq", "owo", ">w<", "嘻嘻", "嘿嘿", "哈哈", "开心~"]
_KAOMOJI_EXCITED = ["啊啊啊", "！！！", "好耶", "冲冲冲", "芜湖~", "耶！"]
_KAOMOJI_SHY = [">_<", "///", "呜呜", "嗯...", "害羞", "qwq"]
_KAOMOJI_ANGRY = ["哼！", "气死", "(╯‵□′)╯︵┻━┻", "可恶", "啊啊啊气死了"]
_KAOMOJI_SAD = ["呜呜呜", "qwq", "唉...", "难过", "T_T", "委屈"]
_KAOMOJI_TSUNDERE = ["哼~", "切~", "别瞎说", "谁要你管", "略略略"]
_KAOMOJI_CUTE = ["喵~", "呜喵", "嗷呜", "诶嘿~", "w"]
_KAOMOJI_TEASE = ["嘿嘿~", "哟~", "诶嘿", "嘻嘻", "♡", "w"]


def maybe_add_kaomoji(text: str, emotion_dominant: str = "平静",
                      emotion_valence: float = 0.0,
                      emotion_arousal: float = 0.5,
                      affection_score: float = 0.0) -> str:
    """根据情绪状态在回复末尾添加颜文字。

    概率：8%（平静）~ 15%（高情绪）
    颜文字加在句尾，用空格或直接拼接。
    """
    # 已经有颜文字/表情符号就不加了
    if re.search(r'[><_╱╲╯︵┻━♡qwQWOPop]{2,}', text):
        return text

    from .config import (
        HUMANIZE_KAOMOJI_EXCITED, HUMANIZE_KAOMOJI_HAPPY, HUMANIZE_KAOMOJI_SHY,
        HUMANIZE_KAOMOJI_ANGRY, HUMANIZE_KAOMOJI_SAD, HUMANIZE_KAOMOJI_TSUNDERE,
        HUMANIZE_KAOMOJI_TEASE, HUMANIZE_KAOMOJI_DEFAULT,
    )
    # 根据情绪选词库
    if emotion_dominant in ("开心", "得意"):
        pool = _KAOMOJI_HAPPY
        chance = HUMANIZE_KAOMOJI_HAPPY
    elif emotion_dominant == "兴奋":
        pool = _KAOMOJI_EXCITED
        chance = HUMANIZE_KAOMOJI_EXCITED
    elif emotion_dominant in ("害羞", "撒娇"):
        pool = _KAOMOJI_SHY
        chance = HUMANIZE_KAOMOJI_SHY
    elif emotion_dominant == "生气":
        pool = _KAOMOJI_ANGRY
        chance = HUMANIZE_KAOMOJI_ANGRY
    elif emotion_dominant in ("难过", "担心"):
        pool = _KAOMOJI_SAD
        chance = HUMANIZE_KAOMOJI_SAD
    elif emotion_dominant in ("傲娇", "冷淡"):
        pool = _KAOMOJI_TSUNDERE
        chance = HUMANIZE_KAOMOJI_TSUNDERE
    elif affection_score >= 200 and emotion_valence > 0:
        # 高好感度 + 正面情绪 → 撩人/可爱
        pool = _KAOMOJI_TEASE + _KAOMOJI_CUTE
        chance = HUMANIZE_KAOMOJI_TEASE
    else:
        pool = _KAOMOJI_HAPPY
        chance = HUMANIZE_KAOMOJI_DEFAULT

    if random.random() > chance:
        return text

    kaomoji = random.choice(pool)

    # 加在句尾：如果以标点结尾，替换标点；否则直接拼接
    if text and text[-1] in "。！？~…":
        text = text[:-1] + kaomoji
    else:
        text = text + " " + kaomoji

    return text
