"""Stage: 人性化处理 — 错别字、口吃、颜文字、反应词前缀、连发拆分。"""
import random
import re as _re
from typing import Optional

from nonebot import logger

from ..activity_sim import get_natural_activity_mention
from ..handler_humanize import introduce_mind_change
from ..handler_humanize import introduce_stutter
from ..handler_humanize import introduce_typo
from ..handler_humanize import introduce_uncertainty
from ..handler_humanize import maybe_add_kaomoji
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("humanize")
async def _stage_humanize(ctx: ChatContext) -> Optional[str]:
    if not ctx.reply_text:
        return None
    text = ctx.reply_text

    # B25: 保护 CQ 代码免受人性化处理破坏（phone_direct 等阶段可能生成 CQ 代码）
    _cq_placeholders = {}
    def _protect_cq(t: str) -> str:
        """用占位符替换 CQ 代码，防止人性化函数破坏它们。"""
        nonlocal _cq_placeholders
        _cq_placeholders.clear()
        cq_pattern = _re.compile(r'\[CQ:[^\]]+\]')
        for i, match in enumerate(cq_pattern.finditer(t)):
            placeholder = f"__CQPROTECT_{i}__"
            _cq_placeholders[placeholder] = match.group()
        for ph, cq in _cq_placeholders.items():
            t = t.replace(cq, ph, 1)  # replace first occurrence
        return t

    def _restore_cq(t: str) -> str:
        """还原被占位符替换的 CQ 代码。"""
        for ph, cq in _cq_placeholders.items():
            t = t.replace(ph, cq)
        return t

    _has_cq = _re.search(r'\[CQ:[^\]]+\]', text)
    if _has_cq:
        text = _protect_cq(text)
        logger.debug(f"[人性化] 检测到 {len(_cq_placeholders)} 个 CQ 代码，已保护")

    # 节奏增强：反应词前缀（上下文感知版）
    from ..handler_humanize import maybe_add_reaction_prefix
    emotion_v = ctx.analysis.emotion.valence if ctx.analysis else 0.0
    emotion_a = ctx.analysis.emotion.arousal if ctx.analysis else 0.5
    emotion_dom = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else "平静"

    # 好感度分数
    aff_score = ctx.affection.get("score", 0)

    # 传入用户消息和情绪，启用上下文感知反应词
    text = maybe_add_reaction_prefix(
        text, emotion_v,
        user_message=ctx.raw_msg,
        emotion=emotion_dom,
        affection_score=aff_score,
    )

    # 自然带出当前活动（5%概率，在typo/stutter之前）
    activity_mention = get_natural_activity_mention()
    if activity_mention:
        text = activity_mention + text

    # 原有人性化处理 + 好感度修正：越熟越随意
    typo_applied = False
    typo_chance = 0.025 if aff_score >= 200 else (0.005 if aff_score < 20 else 0.03)
    if random.random() < typo_chance:
        text = introduce_typo(text)
        typo_applied = True
    mind_change_chance = 0.025 if aff_score >= 200 else (0.005 if aff_score < 20 else 0.02)
    if random.random() < mind_change_chance:
        text = introduce_mind_change(text)
    if random.random() < 0.01 and len(text) > 10:
        text = introduce_uncertainty(text)

    # 口吃效果：与 typo 互斥（同一条消息只触发一种文字效果）
    stutter_chance = 0.06 if emotion_a > 0.7 else 0.03
    if aff_score >= 200:
        stutter_chance *= 1.3  # 熟人更随意
    if not typo_applied and random.random() < stutter_chance:
        text = introduce_stutter(text, emotion_a)

    # 颜文字：根据情绪在句尾加表情符号
    text = maybe_add_kaomoji(
        text,
        emotion_dominant=emotion_dom,
        emotion_valence=emotion_v,
        emotion_arousal=emotion_a,
        affection_score=ctx.affection.get("score", 0),
    )

    # 节奏增强：连发拆分
    from ..handler_humanize import maybe_split_to_bursts
    bursts = maybe_split_to_bursts(text, emotion_a, emotion_v)
    if bursts:
        # 用换行连接，后续 split_long_reply 会拆成多条消息
        text = "\n".join(bursts)

    # B25: 还原被保护的 CQ 代码
    if _has_cq:
        text = _restore_cq(text)

    ctx.reply_text = text
    return None
