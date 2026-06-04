"""主消息处理器"""
import os
import asyncio
import random
import re
from pathlib import Path
from typing import List, Dict, Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, Message, MessageSegment

from .config import REPLY_LENGTH_CONFIG, RANDOM_REPLY_CHANCE, ANALYSIS_HISTORY_LIMIT, CHAT_HISTORY_MULTIPLIER
from .prompt import _build_system_prompt, estimate_reply_length
from .utils import split_long_reply, get_session_id, check_rate_limit, filter_novel_actions
from .memory import save_and_get_context, save_reply, apply_affection_delta, save_and_get_context_with_history
from .share_parser import extract_and_cache_shares, get_recent_shares
from .share_prompt import build_analysis_prompt
from .api import call_deepseek_api
from .voice import send_voice, should_send_voice
from .stt import recognize_voice
from .context_analyzer import analyze_context_and_emotion, AnalysisResult, update_bot_emotion
from .search import should_search, search, format_search_for_prompt, extract_search_query
from .reminder import is_reminder_request, create_reminder, list_reminders, cancel_reminder_by_id, get_pending_reminders_context, _generate_reminder_reply
from .world_context import build_world_context_prompt, extract_city_from_message
from .media import split_reply_and_links, extract_shareable_from_search, build_rich_message
from .sticker import parse_sticker_tag, select_sticker, should_send_sticker_fallback, filter_sticker_tag, select_sticker_with_search
from nonebot import logger


async def handle_chat(bot: Bot, event: MessageEvent):
    try:
        await _handle_chat_inner(bot, event)
    except Exception as e:
        import traceback
        logger.error(f"[handle_chat] 严重异常: {e}")
        traceback.print_exc()
        try:
            await bot.send(event, Message("呜...脑袋有点乱，让我缓缓..."))
        except Exception:
            pass


async def _handle_chat_inner(bot: Bot, event: MessageEvent):
    raw_msg = event.get_message().extract_plain_text().strip()
    session_id = get_session_id(event)
    is_group = isinstance(event, GroupMessageEvent)
    user_id = str(event.user_id)

    # 语音识别：如果用户发了语音消息，先转为文字
    has_voice = any(seg.type == "record" for seg in event.get_message())
    if has_voice and not raw_msg:
        recognized_text = await recognize_voice(event)
        if recognized_text:
            raw_msg = recognized_text
            logger.info(f"[STT] 语音识别结果: {raw_msg[:50]}")
        else:
            logger.info("[STT] 语音识别失败或无内容")
            # 识别失败时回复提示
            try:
                await bot.send(event, Message("听不太清楚呢...能打字告诉我吗？"))
            except Exception:
                pass
            return

    if not check_rate_limit(user_id):
        logger.info(f"[限流] 用户 {user_id} 请求过快，已忽略")
        return

    has_share = await extract_and_cache_shares(event, session_id)

    if not raw_msg and not has_share:
        return

    if not raw_msg and has_share:
        if not is_group or event.is_tome() or random.random() < 0.3:
            recent = get_recent_shares(session_id)
            last_share = recent[-1] if recent else None
            if last_share and last_share.get("type") == "表情":
                emoji_text = last_share.get("summary", "")
                import re as _re
                emoji_match = _re.search(r'用户发送了(?:QQ表情|QQ商城表情|QQ内置表情|表情)[：:]?\s*(.+?)]', emoji_text)
                emoji_name = emoji_match.group(1).strip() if emoji_match else "表情"
                # 用 LLM 生成猫娘个性化回应
                emotion_prompt = f"用户给你发了一个QQ表情「{emoji_name}」，没有说其他话。"
                emoji_sys = (
                    "你是一只猫娘，正在QQ上和人聊天。用户只给你发了一个表情，没有文字。"
                    "根据表情的含义，用你的性格（猫系、会调侃、嘴硬、偶尔撒娇）回复1-2句。"
                    "口语化、短句、像发QQ消息。不要加括号动作。"
                    "如果适合发表情包，在末尾加 [sticker:情绪]（happy/angry/shy/sad/tsundere/cute/funny/love/speechless/excited）。大约20%概率加。绝对不要输出 [doge]、[微笑] 等QQ内置表情标签。"
                )
                emoji_messages = [
                    {"role": "system", "content": emoji_sys},
                    {"role": "user", "content": emotion_prompt}
                ]
                reply_text = await call_deepseek_api(emoji_messages, temperature=1.0)
                reply_text = filter_novel_actions(reply_text)
                # 解析表情包标签并发送
                clean_reply, sticker_kept = filter_sticker_tag(reply_text, session_id)
                if sticker_kept:
                    send_text, sticker_emotion = parse_sticker_tag(clean_reply)
                else:
                    send_text = clean_reply
                    sticker_emotion = should_send_sticker_fallback(reply_text)
                if send_text.strip():
                    parts = split_long_reply(send_text)
                    for i, part in enumerate(parts):
                        if i > 0:
                            await asyncio.sleep(random.uniform(0.8, 1.5))
                        await bot.send(event, Message(part))
                if sticker_emotion:
                    sticker_path = await select_sticker_with_search(sticker_emotion)
                    if sticker_path:
                        await asyncio.sleep(0.8)
                        await bot.send(event, MessageSegment.image(file=Path(sticker_path)))
                        logger.info(f"[表情包] 回应表情: {sticker_emotion} -> {os.path.basename(sticker_path)}")
                # 保存回复
                await save_reply(session_id, user_id, f"[表情:{emoji_name}]", send_text)
            else:
                # 用 LLM 生成猫娘对分享链接的个性化回应
                share_sys = (
                    "你是一只猫娘，正在QQ上和人聊天。用户给你发了一个链接/分享，没有说其他话。"
                    "用你的性格（猫系、会调侃、嘴硬、偶尔撒娇、有点小好色）回复1句话，表示你看到了。"
                    "口语化、短句、像发QQ消息。不要加括号动作。只输出回复内容。"
                )
                share_messages = [
                    {"role": "system", "content": share_sys},
                    {"role": "user", "content": "用户发了一个链接，没有其他文字。回复一句表示你看到了。"}
                ]
                try:
                    share_reply = await call_deepseek_api(share_messages, temperature=1.0)
                    share_reply = filter_novel_actions(share_reply).strip()
                    if len(share_reply) > 3:
                        await bot.send(event, Message(share_reply))
                    else:
                        raise ValueError("回复太短")
                except Exception:
                    await bot.send(event, Message("喵？什么东西，让我看看~"))
        return

    shares_now = get_recent_shares(session_id)
    latest_share = shares_now[-1] if shares_now else None
    
    # 小黑盒处理：用户粘贴正文后，立即分析，不需要再发一次消息
    if latest_share and latest_share.get("needs_paste") and latest_share.get("platform") == "小黑盒":
        if raw_msg and len(raw_msg) > 100 and not any(kw in raw_msg for kw in ["讲了什么", "是什么", "怎么看", "这个呢"]):
            # 用户粘贴了正文，保存后继续正常流程分析
            latest_share["summary"] = raw_msg[:2000]
            latest_share["needs_paste"] = False
            latest_share["restricted"] = False
            logger.info(f"[分享] 用户补充了小黑盒正文，长度: {len(raw_msg)}，继续分析...")
            # 不 return，继续下面的分析流程
        elif any(kw in raw_msg for kw in ["讲了什么", "是什么", "内容", "说了什么", "这个呢", "怎么看", "分析一下", "评价"]):
            await bot.send(event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~"))
            return

    if is_group:
        is_at_me = event.is_tome()
        nicknames = ["猫娘", "kitty", "喵喵", "在吗", "bot", "机器人"]
        has_nickname = any(nick in raw_msg for nick in nicknames)
        should_reply = is_at_me or has_nickname or (random.random() < RANDOM_REPLY_CHANCE)
        if not should_reply:
            return
        if is_at_me:
            raw_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', raw_msg).strip()
            if not raw_msg:
                raw_msg = "在吗"

    await apply_affection_delta(user_id, raw_msg)

    # Phase 1+2: 先获取历史，再做上下文+情绪分析
    recent_memories, relevant_tags, affection, mood, history_for_analysis = \
        await save_and_get_context_with_history(session_id, user_id, raw_msg)

    analysis = await analyze_context_and_emotion(raw_msg, history_for_analysis, user_id)

    # 更新bot自己的情绪状态
    bot_mood_result = await update_bot_emotion(raw_msg, analysis.emotion)

    # 指代消解
    if analysis.context.referenced_entity:
        logger.info(f"[指代消解] 检测到指代: {analysis.context.referenced_entity}")

    # Phase 4: 备忘录/提醒检测（优先级最高，直接回复并返回）
    reminder_intent = is_reminder_request(raw_msg)
    if reminder_intent == "create":
        reply_text = await create_reminder(user_id, session_id, raw_msg)
        await bot.send(event, Message(reply_text))
        return
    elif reminder_intent == "list":
        reply_text = await list_reminders(user_id)
        await bot.send(event, Message(reply_text))
        return
    elif reminder_intent == "cancel":
        # 尝试从消息中提取 reminder ID
        import re as _re
        id_match = _re.search(r'(\d+)', raw_msg)
        if id_match:
            reply_text = await cancel_reminder_by_id(user_id, int(id_match.group(1)))
        else:
            reply_text = await _generate_reminder_reply("no_reminder")
        await bot.send(event, Message(reply_text))
        return

    # Phase 3: 联网搜索
    search_result = None
    is_explicit_search = False
    search_decision = should_search(raw_msg)
    if search_decision.get("need_search"):
        is_explicit_search = search_decision.get("is_explicit", False)
        query = extract_search_query(raw_msg)
        search_result = await search(query)
        if search_result:
            logger.info(f"[搜索] 找到 {len(search_result.results)} 条结果 | 显式={is_explicit_search}")

    # Phase 4: 获取待提醒上下文
    reminder_context = await get_pending_reminders_context(user_id)

    # Phase 6: 世界上下文（天气）- 动态读取用户城市
    user_city = extract_city_from_message(raw_msg)
    # 也从记忆标签中查找用户所在城市
    if not user_city:
        for tag in relevant_tags:
            tag_str = str(tag)
            for city_name in ["上海", "北京", "广州", "深圳", "杭州", "成都", "武汉", "南京", "重庆", "西安", "苏州", "天津"]:
                if city_name in tag_str:
                    user_city = city_name
                    break
            if user_city:
                break
    world_context = await build_world_context_prompt(user_city)

    analysis_keywords = [
        "怎么看", "怎么讲", "分析一下", "评价", "观点", "有什么想法",
        "说说", "讲讲", "如何理解", "什么意思", "详细介绍", "详细说说"
    ]
    valid_shares_now = [s for s in shares_now if s.get("summary")]
    is_asking_analysis = any(kw in raw_msg for kw in analysis_keywords) and valid_shares_now

    if is_asking_analysis:
        analysis_prompt = build_analysis_prompt(valid_shares_now, raw_msg)
        if analysis_prompt == "[小黑盒内容需要用户粘贴正文后才能分析]":
            await bot.send(event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~"))
            return

        length_info = {"target_lines": 4, "style": "专业分析+个性点评"}
        search_ctx = format_search_for_prompt(search_result) if search_result else ""
        sys_prompt = _build_system_prompt(
            affection, mood, length_info, relevant_tags, shares_now, raw_msg,
            context_analysis=analysis.context, emotion_state=analysis.emotion,
            search_context=search_ctx, reminder_context=reminder_context,
            world_context=world_context, bot_mood=bot_mood_result,
        )
        sys_prompt += "\n回复风格：专业分析+个性点评。分析部分结构化、有深度，点评部分保持你的猫娘语气。绝对禁止括号动作描写。"

        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = ANALYSIS_HISTORY_LIMIT
        for mem in recent_memories[-history_limit:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        messages.append({"role": "user", "content": analysis_prompt})
    else:
        length_info = estimate_reply_length(raw_msg, recent_memories, bot_mood_result)
        search_ctx = format_search_for_prompt(search_result) if search_result else ""
        sys_prompt = _build_system_prompt(
            affection, mood, length_info, relevant_tags, shares_now, raw_msg,
            context_analysis=analysis.context, emotion_state=analysis.emotion,
            search_context=search_ctx, reminder_context=reminder_context,
            world_context=world_context, bot_mood=bot_mood_result,
        )
        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = REPLY_LENGTH_CONFIG["context_depth"] * CHAT_HISTORY_MULTIPLIER
        for mem in recent_memories[-history_limit:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": raw_msg})

    reply_text = await call_deepseek_api(messages)
    reply_text = filter_novel_actions(reply_text)

    # 保存回复到数据库
    await save_reply(session_id, user_id, raw_msg, reply_text)

    # 解析表情包标签 + 概率后置过滤
    reply_text_filtered, sticker_kept = filter_sticker_tag(reply_text, session_id)
    if sticker_kept:
        # LLM 加了标签且被保留
        clean_text, sticker_emotion = parse_sticker_tag(reply_text_filtered)
    else:
        # LLM 没加标签，或被过滤掉了
        clean_text = reply_text_filtered
        sticker_emotion = None
        # fallback：LLM 没加标签时，低概率补发
        sticker_emotion = should_send_sticker_fallback(reply_text, analysis.emotion.dominant if analysis.emotion.confidence >= 0.4 else None)

    # 提取回复中的链接和搜索结果（始终用 clean_text，确保不含 sticker 标签）
    text_for_links, reply_urls = split_reply_and_links(clean_text)
    search_items = extract_shareable_from_search(search_result) if search_result else []

    send_as_voice = should_send_voice(raw_msg, clean_text, recent_memories)
    if send_as_voice:
        logger.warning(f"[决策] 上下文判断发语音，跳过文字: {clean_text[:30]}...")
        await send_voice(bot, event, clean_text)
        if reply_urls or search_items:
            rich_msg = build_rich_message("", reply_urls, search_items, show_links=is_explicit_search)
            if rich_msg:
                await asyncio.sleep(1.5)
                await bot.send(event, rich_msg)
    else:
        logger.info(f"[决策] 上下文判断发文字: {clean_text[:30]}...")
        final_text = clean_text
        if reply_urls or search_items:
            rich_msg = build_rich_message(final_text, reply_urls, search_items, show_links=is_explicit_search)
            parts = split_long_reply(str(rich_msg))
            for i, part in enumerate(parts):
                if i > 0:
                    await asyncio.sleep(random.uniform(1.0, 2.5))
                await bot.send(event, Message(part))
        else:
            parts = split_long_reply(final_text)
            for i, part in enumerate(parts):
                if i > 0:
                    await asyncio.sleep(random.uniform(1.0, 2.5))
                await bot.send(event, Message(part))

    # 发送表情包
    if sticker_emotion:
        sticker_path = await select_sticker_with_search(sticker_emotion)
        if sticker_path:
            await asyncio.sleep(0.8)
            await bot.send(event, MessageSegment.image(file=Path(sticker_path)))
            logger.info(f"[表情包] 发送: {sticker_emotion} -> {os.path.basename(sticker_path)}")
