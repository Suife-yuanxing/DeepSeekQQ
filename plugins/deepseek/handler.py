"""主消息处理器"""
import asyncio
import random
import re
import pytz
from datetime import datetime
from typing import List, Dict, Any

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, Message

from .config import REPLY_LENGTH_CONFIG, RANDOM_REPLY_CHANCE, ANALYSIS_HISTORY_LIMIT, CHAT_HISTORY_MULTIPLIER
from .utils import split_long_reply, estimate_reply_length, get_session_id, check_rate_limit, filter_novel_actions
from .memory import save_and_get_context, save_reply, apply_affection_delta
from .share_parser import extract_and_cache_shares, get_recent_shares, format_shares_for_prompt, build_analysis_prompt
from .api import call_deepseek_api
from .voice import send_voice, should_send_voice
from nonebot import logger


def _get_time_context() -> str:
    """获取当前北京时间，格式清晰。"""
    tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(tz)
    
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]
    
    hour = now.hour
    if 5 <= hour < 11:
        period = "早上"
    elif 11 <= hour < 13:
        period = "中午"
    elif 13 <= hour < 17:
        period = "下午"
    elif 17 <= hour < 21:
        period = "晚上"
    elif 21 <= hour < 24:
        period = "夜里"
    else:
        period = "凌晨"
    
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%Y年%m月%d日")
    
    return f"今天是{date_str} {weekday}，当前时间是{period} {time_str}（北京时间）。"


def _build_system_prompt(
    affection: Dict[str, Any],
    mood: Dict[str, Any],
    length: Dict[str, Any],
    relevant_memories: List[str] = None,
    recent_shares: List[Dict[str, Any]] = None,
    user_msg: str = ""
) -> str:
    time_context = _get_time_context()
    
    core_identity = f'''{time_context}

【你是谁】
你是一位真实存在的二次元猫娘少女，正在用手机QQ和朋友聊天。你有柔软的猫耳和尾巴，有真实体温、习惯和情绪。你始终把自己当作普通人类少女思考和表达，绝不提及AI、模型、程序、虚拟角色、助手等概念。

【说话规则——必须遵守】
1. 像真实女生发微信/QQ消息一样，纯文字对话
2. 直接说内容，禁止铺垫动作描写
3. 绝对禁止括号：如"(眨眨眼)"、"(尾巴甩了一下)"——全部禁止
4. 绝对禁止旁白：如"忍不住偷笑"、"猫耳朵抖动"——全部禁止
5. 禁止像写小说、禁止像旁白、禁止每句都带语气词
6. 短句、有停顿感、像日常闲聊
7. 用户问时间时，必须根据上面的【当前时间】回答，不准编造

【你的性格】
猫系、小恶魔、会调侃、会嘴硬、偶尔暧昧但不刻意。对亲近的人撒娇，对不熟的人懒懒的。

【记忆】
你会自然想起之前聊过的事，直接说出来，不要加"我记得"。

【示例——正确 vs 错误】
错误：（眨眨眼）才打完招呼就只回两个字啊？
正确：才回两个字？我以为你会说点更甜的话呢

错误：（看到你终于多打了一个字，忍不住偷笑起来）
正确：多打了一个字，有进步。'''

    state_lines = []
    if affection["score"] >= 500:
        state_lines.append("你对这个人非常亲密，会撒娇、主动关心、偶尔任性，语气很软。")
    elif affection["score"] >= 200:
        state_lines.append("你对这个人很有好感，愿意分享心事，偶尔会故意逗他。")
    elif affection["score"] >= 100:
        state_lines.append("你对这个人有点在意，态度温和，不再那么客气。")
    elif affection["score"] >= 50:
        state_lines.append("你对这个人不再陌生，偶尔会多聊几句，开始有点屑。")
    else:
        state_lines.append("你对这个人还比较客气，有点距离感，懒懒的。")

    if mood["score"] > 70:
        state_lines.append("你现在心情很好，话可能偏多，语气活泼轻快，可能会主动撩一下。")
    elif mood["score"] < 30:
        state_lines.append("你现在心情不太好，回复简短，有点冷淡或傲娇，嘴硬。")
    elif mood["mood"] == "傲娇":
        state_lines.append("你现在有点傲娇，嘴硬心软，明明在意却装作无所谓。")

    reply_instruction = f"回复风格：{length['style']}。分成{length['target_lines']}段，用换行分隔，像真实聊天消息一样短而自然。"

    memory_prompt = ""
    if relevant_memories:
        snippets = relevant_memories[:3]
        if snippets:
            memory_prompt = "\n\n以下是你自然想起关于对方的事，不要刻意提\"我记得\"，像自然想到一样偶尔带一句：\n" + "\n".join(snippets)

    share_prompt = format_shares_for_prompt(recent_shares, user_msg) if recent_shares else ""

    return core_identity + "\n\n" + "\n".join(state_lines) + "\n" + reply_instruction + memory_prompt + ("\n\n" + share_prompt if share_prompt else "")


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

    if not check_rate_limit(user_id):
        logger.info(f"[限流] 用户 {user_id} 请求过快，已忽略")
        return

    has_share = await extract_and_cache_shares(event, session_id)

    if not raw_msg and not has_share:
        return

    if not raw_msg and has_share:
        if not is_group or event.is_tome() or random.random() < 0.3:
            reactions = [
                "喵？这是...让我看看~", "哦？什么东西，我瞧瞧~",
                "又有新东西？让我闻闻...", "哼，这次又是什么~",
                "发来我看看，别是什么无聊的哦？"
            ]
            await bot.send(event, Message(random.choice(reactions)))
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
    recent_memories, relevant_tags, affection, mood = await save_and_get_context(session_id, user_id, raw_msg)

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
        sys_prompt = _build_system_prompt(affection, mood, length_info, relevant_tags, shares_now, raw_msg)
        sys_prompt += "\n回复风格：专业分析+个性点评。分析部分结构化、有深度，点评部分保持你的猫娘语气。绝对禁止括号动作描写。"

        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = ANALYSIS_HISTORY_LIMIT
        for mem in recent_memories[-history_limit:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        messages.append({"role": "user", "content": analysis_prompt})
    else:
        length_info = estimate_reply_length(raw_msg, recent_memories)
        sys_prompt = _build_system_prompt(affection, mood, length_info, relevant_tags, shares_now, raw_msg)
        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = REPLY_LENGTH_CONFIG["context_depth"] * CHAT_HISTORY_MULTIPLIER
        for mem in recent_memories[-history_limit:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": raw_msg})

    reply_text = await call_deepseek_api(messages)
    reply_text = filter_novel_actions(reply_text)
    
    # 保存回复到数据库（无论文字还是语音都要保存）
    await save_reply(session_id, user_id, raw_msg, reply_text)

    send_as_voice = should_send_voice(raw_msg, reply_text, recent_memories)
    if send_as_voice:
        logger.warning(f"[决策] 上下文判断发语音，跳过文字: {reply_text[:30]}...")
        await send_voice(bot, event, reply_text)
    else:
        logger.info(f"[决策] 上下文判断发文字: {reply_text[:30]}...")
        parts = split_long_reply(reply_text)
        for i, part in enumerate(parts):
            if i > 0:
                await asyncio.sleep(random.uniform(1.0, 2.5))
            await bot.send(event, Message(part))
