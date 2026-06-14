"""Stage: 后处理 — 保存回复、OOC检查、承诺追踪、表情包、语音、发送消息。"""
import asyncio
import os
import random
from pathlib import Path
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageSegment

from .._audio_utils import validate_file
from ..handler_helpers import make_reply
from ..handler_helpers import make_quote_reply
from ..handler_helpers import should_quote
from ..media import build_rich_message
from ..media import extract_shareable_from_search
from ..media import split_reply_and_links
from ..memory import save_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..sticker import filter_sticker_tag
from ..sticker import parse_sticker_tag
from ..sticker import select_sticker_with_search
from ..sticker import should_send_sticker_fallback
from ..time_validator import validate_time_in_reply
from ..utils import calc_message_delay
from ..utils import safe_task
from ..utils import split_long_reply
from ..voice import should_send_voice
from ..voice import send_voice
from ..voice import generate_voice_file
from ..voice import send_voice_file
from ..voice import get_voice_tracker
from ..pipeline import _set_typing_status


async def _record_expressed_opinions(ctx: ChatContext):
    """从回复中记录bot表达的相关立场（fire-and-forget）。"""
    try:
        from ..opinion_tracker import record_opinion

        # 提取话题关键词（在bot回复中匹配）
        from ..values import find_relevant_values
        relevant = find_relevant_values(ctx.reply_text)
        if not relevant:
            return

        for rv in relevant[:3]:  # 最多记录3条
            agreement = "neutral"
            if any(kw in ctx.reply_text for kw in ["确实", "没错", "对对", "同意", "赞同", "你说得对"]):
                agreement = "agree"
            elif any(kw in ctx.reply_text for kw in ["不过", "但是", "可是", "我觉得", "不是", "不对", "不一定"]):
                agreement = "disagree"

            await record_opinion(
                user_id=ctx.user_id,
                topic=rv["topic"],
                bot_stance=rv["opinion"][:100],
                user_stance=ctx.raw_msg[:100],
                agreement_level=agreement,
            )
    except Exception as e:
        logger.debug(f"[意见追踪] 记录失败（非关键）: {e}")


@stage("post_process")
async def _stage_post(ctx: ChatContext) -> Optional[str]:
    await save_reply(ctx.session_id, ctx.user_id, ctx.raw_msg, ctx.reply_text, ctx.bot_mood_result)

    # P2-3: OOC 分类器（异步，不阻塞回复流）
    try:
        from ..ooc_classifier import schedule_ooc_check
        schedule_ooc_check(ctx.user_id, ctx.reply_text, ctx.bot_name if hasattr(ctx, 'bot_name') else "林念念")
    except Exception as e:
        logger.debug(f"[OOC] 调度异常: {e}")

    # 承诺追踪：从回复中提取承诺
    from ..promise_tracker import process_bot_reply
    safe_task(process_bot_reply(ctx.reply_text, ctx.user_id, ctx.session_id))

    # 意见追踪：记录bot表达的立场（fire-and-forget）
    if ctx.value_hints or ctx.past_opinions:
        safe_task(_record_expressed_opinions(ctx))

    sticker_chance = ctx.emotion_params.get("sticker_chance", 0.25)
    reply_filtered, sticker_kept = filter_sticker_tag(ctx.reply_text, ctx.session_id, keep_probability=sticker_chance)
    sticker_scene = ""
    if sticker_kept:
        clean_text, sticker_emotion, sticker_scene = parse_sticker_tag(reply_filtered)
    else:
        clean_text = reply_filtered
        fallback_chance = sticker_chance * 0.6
        sticker_emotion = should_send_sticker_fallback(
            ctx.reply_text,
            ctx.analysis.emotion.dominant if ctx.analysis.emotion.confidence >= 0.4 else None,
            fallback_chance=fallback_chance,
        )

    # 时间自检：修正 LLM 输出中不合理的时间表达
    clean_text = validate_time_in_reply(clean_text)

    text_for_links, reply_urls = split_reply_and_links(clean_text)
    search_items = extract_shareable_from_search(ctx.search_result) if ctx.search_result else []

    use_quote = should_quote(
        ctx.event, ctx.bot.self_id, ctx.raw_msg,
        ctx.is_group, ctx.is_explicit_search, ctx.recent_memories
    )
    first_sent = False

    # 打字延迟上下文（真人化：传入对方消息和复杂度）
    typing_ctx = {
        "is_first_reply": True,
        "is_question": "?" in ctx.raw_msg or "？" in ctx.raw_msg,
        "emotion_arousal": ctx.analysis.emotion.arousal if ctx.analysis else 0.5,
        "schedule_speed": ctx.schedule.reply_speed if ctx.schedule else 1.0,
        "is_quick_reply": ctx.complexity == "simple",
        "user_msg": ctx.raw_msg,                    # 对方消息，用于计算阅读时间
        "complexity": ctx.complexity,                # simple/normal/complex
        "is_night": ctx.schedule.period == "sleeping" if ctx.schedule else False,
    }

    bot_mood_dom = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else "平静"
    voice_tracker = get_voice_tracker()
    send_as_voice = should_send_voice(
        ctx.raw_msg, clean_text, ctx.recent_memories,
        voice_mode=ctx.voice_mode,
        affection_score=ctx.affection.get("score", 0),
        bot_mood_dominant=bot_mood_dom,
        voice_tracker=voice_tracker,
        user_id=ctx.user_id,
    )
    voice_max_len = 200 if ctx.voice_mode else 0

    # === 发消息前取消"正在输入"状态（更自然：打完字→取消输入→发送）===
    await _set_typing_status(ctx.bot, ctx.event, False)

    # 语音模式：优先发语音，失败才回退文字
    if ctx.voice_mode:
        voice_emotion = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else None
        voice_path = await generate_voice_file(clean_text, emotion=voice_emotion, max_length=200)
        if voice_path and validate_file(voice_path, 100):
            logger.info(f"[语音通话] 发送语音: {clean_text[:30]}...")
            await send_voice_file(ctx.bot, ctx.event, voice_path)
            voice_tracker.voice_sent(ctx.user_id)
            # 语音模式下纯语音，不发送文字/链接/表情包/图片
            return None
        else:
            logger.warning(f"[语音通话] 语音生成失败，回退到文字: {clean_text[:30]}...")
            # 继续下面的文字发送流程
    elif send_as_voice:
        logger.warning(f"[决策] 上下文判断发语音，跳过文字: {clean_text[:30]}...")
        voice_emotion = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else None
        await send_voice(ctx.bot, ctx.event, clean_text, emotion=voice_emotion, max_length=voice_max_len)
        voice_tracker.voice_sent(ctx.user_id)
        if reply_urls or search_items:
            rich_msg = build_rich_message("", reply_urls, search_items, show_links=ctx.is_explicit_search)
            if rich_msg:
                await asyncio.sleep(1.5)
                if use_quote:
                    await ctx.bot.send(ctx.event, make_quote_reply(ctx.event, rich_msg))
                else:
                    await ctx.bot.send(ctx.event, rich_msg)
                first_sent = True
    else:
        logger.info(f"[决策] 上下文判断发文字: {clean_text[:30]}...")
        if reply_urls or search_items:
            rich_msg = build_rich_message(clean_text, reply_urls, search_items, show_links=ctx.is_explicit_search)
            parts = split_long_reply(str(rich_msg))
            for i, part in enumerate(parts):
                if i == 0:
                    # 首条：完整延迟（阅读+思考+打字）
                    await asyncio.sleep(calc_message_delay(part, typing_ctx))
                else:
                    # 追加：burst 延迟（1.5~3.5秒，模拟打完又想到要补）
                    typing_ctx["is_first_reply"] = False
                    await asyncio.sleep(random.uniform(1.5, 3.5))
                if not first_sent and use_quote:
                    await ctx.bot.send(ctx.event, make_quote_reply(ctx.event, Message(part)))
                    first_sent = True
                else:
                    await ctx.bot.send(ctx.event, Message(part))
                    first_sent = True
        else:
            parts = split_long_reply(clean_text)
            for i, part in enumerate(parts):
                if i == 0:
                    # 首条：完整延迟（阅读+思考+打字）
                    await asyncio.sleep(calc_message_delay(part, typing_ctx))
                else:
                    # 追加：burst 延迟（2.5~6秒）
                    typing_ctx["is_first_reply"] = False
                    await asyncio.sleep(random.uniform(2.5, 6.0))
                if not first_sent and use_quote:
                    await ctx.bot.send(ctx.event, make_quote_reply(ctx.event, Message(part)))
                    first_sent = True
                else:
                    await ctx.bot.send(ctx.event, Message(part))
                    first_sent = True

    if sticker_emotion:
        sticker_path = await select_sticker_with_search(sticker_emotion, sticker_scene)
        if sticker_path:
            await asyncio.sleep(0.8)
            await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(sticker_path)))
            logger.info(f"[表情包] 发送: {sticker_emotion}|{sticker_scene} -> {os.path.basename(sticker_path)}")

    if ctx.image_path and os.path.exists(ctx.image_path):
        await asyncio.sleep(1.0)
        await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(ctx.image_path)))
        logger.info(f"[图片] 发送: {os.path.basename(ctx.image_path)}")

    # 记录bot消息类型，用于追问系统
    from ..follow_up import classify_bot_message
    from ..follow_up import record_bot_message
    if ctx.reply_text and not ctx.is_group:
        msg_type = classify_bot_message(ctx.reply_text)
        record_bot_message(ctx.session_id, ctx.reply_text, msg_type)

        # 晚安关键词检测：bot 回复包含晚安关键词时自动取消追问
        # 注意：此处检查的是最终回复文本（含 MCP 输出），如果工具输出误含晚安关键词会误触发，但影响极小
        night_keywords = ["晚安", "快睡", "去睡", "睡觉吧", "好梦", "明天见"]
        if any(kw in ctx.reply_text for kw in night_keywords):
            from ..follow_up import suppress_followup
            suppress_followup(ctx.session_id)
            logger.info(f"[晚安] bot回复包含晚安关键词，取消追问 session={ctx.session_id[:8]}")

    # === 对话疲劳：抑制追问 + 自然收尾 ===
    if ctx.fatigue_level >= 2 and not ctx.is_group:
        from ..follow_up import suppress_followup
        suppress_followup(ctx.session_id)
        logger.info(f"[疲劳感知] 抑制追问 | level={ctx.fatigue_level}")

    if ctx.fatigue_level >= 3 and not ctx.is_group:
        from ..conversation_fatigue import get_closing_message
        closing = get_closing_message(ctx.fatigue_level, ctx.schedule)
        if closing:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(closing)))
            logger.info(f"[疲劳感知] 发送收尾消息: {closing}")

    # 随机行为（P2）：3% 概率突然分享点什么
    from ..message_actions import maybe_share_something
    safe_task(maybe_share_something(ctx.bot, ctx.event, share_chance=0.03))

    # 情绪快照保存（P1）：会话结束时保存用户情绪供下次关心
    from ..db_mood import save_mood_snapshot
    safe_task(save_mood_snapshot(ctx.user_id, ctx.session_id))

    return None
