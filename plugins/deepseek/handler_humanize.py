"""拟人化处理 — 错别字纠正、改变主意、不确定表达。"""
import random


_TYPO_PAIRS = [
    ("的", "地"), ("怎么", "这么"), ("觉得", "决得"),
    ("好像", "号像"), ("不是", "不四"), ("真的", "真地"),
    ("可以", "可一"), ("有点", "有点电"),
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
