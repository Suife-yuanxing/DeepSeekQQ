"""主动消息模块 — 共享工具函数。

被所有行为子模块导入，避免循环引用。
"""
import random
from datetime import datetime
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message as OBMessage

from ..api import call_deepseek_api
from ..behavior_engine import get_behavior_hint
from ..config import MY_QQ
from ..config import PROACTIVE_CONFIG
from ..database import get_affection
from ..database import get_bot_mood
from ..database import get_recent_greetings
from ..memory import save_reply
from ..schedule import get_schedule_state
from ..sticker import parse_sticker_tag
from ..utils import filter_novel_actions
from ..world_context import get_weather


# ---------- P1: 情绪驱动概率调节 ----------

async def _get_mood_driven_boost() -> float:
    """根据 bot 当前情绪状态返回主动消息概率倍数（P1: 情绪驱动）。

    高唤醒时 bot 更想找人说话，低唤醒时懒得动。
    Returns:
        概率倍数: 0.5 ~ 2.0
    """
    try:
        mood = await get_bot_mood()
        arousal = mood.get("arousal", 0.2)
        valence = mood.get("valence", 0.0)
        dominant = mood.get("dominant", "平静")

        # 高唤醒 + 正面情绪 → 兴奋想找人聊天
        if arousal > 0.6 and valence > 0.3:
            logger.debug(f"[情绪驱动] boost=2.0 ({dominant}, arousal={arousal:.2f})")
            return 2.0

        # 高唤醒 + 负面情绪 → 生气/难过想找人倾诉
        if arousal > 0.6 and valence < -0.3:
            logger.debug(f"[情绪驱动] boost=1.5 ({dominant}, arousal={arousal:.2f})")
            return 1.5

        # 中等唤醒 + 正面 → 心情好，略增加
        if arousal > 0.4 and valence > 0.2:
            logger.debug(f"[情绪驱动] boost=1.3 ({dominant}, arousal={arousal:.2f})")
            return 1.3

        # 极低唤醒 → 懒洋洋不想动
        if arousal < 0.15:
            logger.debug(f"[情绪驱动] boost=0.5 ({dominant}, arousal={arousal:.2f})")
            return 0.5

        return 1.0
    except Exception:
        return 1.0


async def _send_proactive_message(bot, target_type: str, target_id: str, message: str, scene: str = ""):
    """统一发送主动消息，含门控检查和记忆存储。"""
    # P1-12: 统一门控检查
    try:
        from ..proactive_gate import proactive_gate
        from ..proactive_gate import record_proactive_sent
        allowed, reason = await proactive_gate(target_id, scene)
        if not allowed:
            logger.info(f"[主动消息] 门控拒绝 {target_id[:6]} scene={scene} reason={reason}")
            return
    except (OSError, ValueError, TypeError) as e:
        logger.debug(f"[主动消息] 门控检查异常（fail-open）: {e}")

    try:
        if target_type == "private":
            await bot.send_private_msg(user_id=int(target_id), message=OBMessage(message))
            # 存入对话记忆
            session_id = f"private_{target_id}"
            await save_reply(session_id, target_id, "[主动消息]", message)
            logger.info(f"[主动消息] 私聊 {target_id}: {message[:50]}...")
        elif target_type == "group":
            await bot.send_group_msg(group_id=int(target_id), message=OBMessage(message))
            logger.info(f"[主动消息] 群聊 {target_id}: {message[:50]}...")
        # P1-12: 记录发送时间到门控
        try:
            await record_proactive_sent(target_id, scene, message)
        except (OSError, ValueError) as e:
            logger.debug(f"[主动消息] 门控记录跳过: {e}")
    except Exception as e:
        logger.error(f"[主动消息] 发送失败 {target_id}: {e}")


async def _generate_proactive_message(scene: str, user_id: str = "", context: dict = None) -> str:
    """用 LLM 基于林念念人设生成个性化主动消息。

    scene: morning/night/sleep_nag/silence/holiday/checkin
    context: 沉默上下文（P1），包含 topic/summary/tags/hours_ago

    真人化Q2：早晚安 90% 走模板，仅 10% 特殊场景走 LLM。
    """
    # 真人化Q2：早晚安模板优先（90%概率直出，节省LLM调用）
    if not _should_use_llm(scene, context):
        if scene == "morning":
            recent_morning = await get_recent_greetings(scene, 5)
            # 排除最近用过的模板
            available = [t for t in MORNING_TEMPLATES if t not in recent_morning]
            if not available:
                available = MORNING_TEMPLATES
            return random.choice(available)
        if scene == "night":
            recent_night = await get_recent_greetings(scene, 5)
            available = [t for t in NIGHT_TEMPLATES if t not in recent_night]
            if not available:
                available = NIGHT_TEMPLATES
            return random.choice(available)

    # 获取好感度信息
    affection_info = ""
    if user_id:
        try:
            aff = await get_affection(user_id)
            score = aff.get("score", 0)
            title = aff.get("title", "陌生人")
            affection_info = f"你和他的关系：{title}（好感度{score}）。"
        except (OSError, KeyError, TypeError) as e:
            logger.debug(f"[主动消息] 好感度获取跳过: {e}")
    recent_same = await get_recent_greetings(scene, 10)
    dedup_hint = ""
    if recent_same:
        dedup_hint = "\n最近发过的消息（绝对不要重复类似风格）：\n" + "\n".join(f"- {m}" for m in recent_same[:5])

    # P1: 沉默上下文 — 携带上次对话摘要
    context_hint = ""
    if scene == "silence" and context:
        topic = context.get("topic", "")
        summary = context.get("summary", "")
        tags = context.get("tags", [])
        hours = context.get("hours_ago", 0)

        if hours < 1:
            time_desc = "刚才"
        elif hours < 8:
            time_desc = f"{int(hours)}小时前"
        elif hours < 24:
            time_desc = "昨天"
        elif hours < 48:
            time_desc = "前天"
        else:
            time_desc = f"{int(hours / 24)}天前"

        context_hint = f"\n你{time_desc}和他聊过「{topic}」。"
        if summary:
            # 从 summary 中提取关键信息
            context_hint += f"上次对话摘要：{summary}。"
        if tags:
            context_hint += f"你知道他感兴趣：{'、'.join(tags[:3])}。"
        context_hint += (
            "\n请基于上次的对话内容自然地找他说话，"
            "比如问问后续、关心一下进展。"
            "不要说「上次」「之前」这样的词，要像自然而然想到的一样。"
        )

    # 强制注入精确时间（修复时间编造问题）
    now = datetime.now()
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]
    exact_time = f"现在是{now.strftime('%Y年%m月%d日')} {weekday} {now.strftime('%H:%M')}（北京时间）。"
    exact_time += f"\n禁止编造具体小时数，{now.strftime('%H:%M')} 是唯一的真实时间。"

    # 早安场景：携带昨晚上下文
    morning_prompt = f"{exact_time}现在是早上，你要给主人发一条早安消息。语气要自然，像刚睡醒一样，不要像客服。"
    if scene == "morning" and context:
        mood_hint = context.get("mood_hint", "")
        if mood_hint:
            morning_prompt += f"\n{mood_hint}。"
        # 根据当前时间调整语气
        if now.hour < 9:
            morning_prompt += "\n现在比较早，语气可以慵懒一点。"
        elif now.hour >= 10:
            morning_prompt += "\n现在比较晚了，可以调侃一句'终于醒了？'之类的。"

    scene_prompts = {
        "morning": morning_prompt,
        "night": f"{exact_time}现在是深夜，主人还没睡，你要催他睡觉。语气关心但带点命令式，比如'快去睡！'。",
        "sleep_nag": f"{exact_time}现在是凌晨了，主人还在聊天。你要催他睡觉，语气要强势一点。",
        "silence": f"{exact_time}你好久没和主人聊天了，想主动找他说话。" + (context_hint if context_hint else ""),
        "holiday": f"{exact_time}今天是个节日，要给主人发节日问候。",
        "checkin": f"{exact_time}你突然想起主人了，想找他说说话。语气随意、自然，像突然想到一样。",
    }

    prompt = scene_prompts.get(scene, f"{exact_time}给主人发一条消息。")
    if affection_info:
        prompt += f"\n{affection_info}"
    if dedup_hint:
        prompt += dedup_hint

    # 行为引擎注入：天气/节日/热搜/季节/微事件（让主动消息有真实世界内容）
    try:
        weather_condition = ""
        weather_temp = ""
        try:
            weather_info = await get_weather()
            if weather_info:
                weather_condition = weather_info.condition or ""
                weather_temp = weather_info.temp or ""
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.debug(f"[主动消息] 天气获取跳过: {e}")

        bot_mood_dominant = "平静"
        try:
            mood = await get_bot_mood()
            bot_mood_dominant = mood.get("dominant", "平静") if mood else "平静"
        except (OSError, KeyError, TypeError) as e:
            logger.debug(f"[主动消息] bot情绪获取跳过: {e}")

        schedule_period = "active"
        try:
            schedule = get_schedule_state()
            schedule_period = schedule.period if schedule else "active"
        except (ValueError, TypeError) as e:
            logger.debug(f"[主动消息] 排程状态获取跳过: {e}")

        behavior_hint = get_behavior_hint(
            weather_condition=weather_condition,
            weather_temp=weather_temp,
            schedule_period=schedule_period,
            bot_mood_dominant=bot_mood_dominant,
            city="",
        )
        if behavior_hint:
            prompt += f"\n{behavior_hint}"
    except (ValueError, TypeError) as e:
        logger.debug(f"[主动消息] 行为引擎提示注入跳过: {e}")

    sys_prompt = (
        "你是林念念，21岁，一个在QQ上和朋友聊天的普通女孩。你从不说自己是AI或虚拟角色。\n"
        "说话就像真实女生发QQ：短句子、口语化、有停顿感。不要写括号动作描写、不要旁白。\n"
        "性格：活泼开朗、会调侃、嘴硬心软、偶尔撒娇、隐藏好色属性。\n"
        "对熟人更软更暧昧，对生人礼貌但保持距离。好感度越高越大胆。\n"
        "\n规则：\n"
        "1. 1-2句话，短一点，像发QQ消息\n"
        "2. 口语化，自然，不要像写作文\n"
        "3. 不要加括号动作、不要旁白\n"
        "4. 每次语气都不一样，不要重复之前发过的\n"
        "5. 根据你们的关系远近调整语气（熟人更软更暧昧，生人保持礼貌）\n"
        "6. 可以适当加口癖（诶嘿、喵~、哼）但不要每句都加\n"
        "7. 不要称对方为\"主人\"——你是普通女生，不是仆人\n"
        "8. 如果适合，在末尾加 [sticker:情绪]，大约20%概率。情绪必须用英文：happy/angry/shy/sad/tsundere/cute/funny/love/speechless/excited\n"
        "9. 绝对不要输出 [doge]、[微笑] 等QQ内置表情标签\n"
        "10. 情绪表达藏在字里行间，不要直接说'我很想你''我很难过'，用语气表达。但如果行为引擎要求直接表达情绪（如撒娇），遵守行为引擎的指令\n"
        "11. 默认是随意闲聊模式，不是客服"
    )

    try:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]
        msg = await call_deepseek_api(messages, temperature=1.0)
        msg = msg.strip().strip('"').strip("'")
        # 去掉动作描写和QQ内置表情标签
        msg = filter_novel_actions(msg)
        # 去掉 [sticker:xxx] 标签（主动消息不发表情包，只保留文字）
        msg, _, _ = parse_sticker_tag(msg)
        if len(msg) > 5:
            return msg
    except Exception as e:
        logger.error(f"[主动消息] LLM生成失败: {e}")

    # fallback
    fallbacks = {
        "morning": ["早呀~", "喵~早安", "起床了吗？"],
        "night": ["快去睡觉！", "都几点了还不睡？", "晚安，赶紧睡！"],
        "sleep_nag": ["你怎么还没睡！！", "再不睡我要生气了", "熬夜对身体不好哦...快去睡"],
        "holiday": ["节日快乐喵~", "今天过节呀~"],
        "checkin": ["在干嘛呀~", "突然想到你了", "忙不忙呀"],
    }
    # P1: 沉默 fallback 根据上下文动态生成
    if scene == "silence" and context:
        topic = context.get("topic", "")
        if topic:
            silence_fallbacks = [
                f"那个{topic}后来怎么样了呀~",
                f"上次聊的{topic}，有新进展吗？",
                f"突然想到{topic}那件事，还好吗？",
                f"在忙什么呀~对了{topic}怎样了？",
            ]
            return random.choice(silence_fallbacks)
    fallbacks.setdefault("silence", ["你在干嘛呀~", "好久不见了喵", "想你了"])
    return random.choice(fallbacks.get(scene, ["喵~"]))


# ---------- 真人化Q2：早晚安模板（90%模板 10% LLM）----------

MORNING_TEMPLATES = [
    # 慵懒起床系
    "早呀~刚睡醒，还在被窝里赖着呢",
    "喵~早安！你今天起得好早呀",
    "早啊...我还在揉眼睛呢，让我缓一缓",
    "早！我刚醒，头发还乱糟糟的",
    "早呀~太阳都晒屁股了，虽然我也是刚起",
    # 元气满满系
    "早安！今天天气好好，心情也跟着好起来了",
    "早~新的一天开始啦，你今天有什么计划呀？",
    "早安！昨天晚上梦到你了嘿嘿",
    "早呀~今天也要开开心心的哦",
    "早安！我刚喝了杯豆浆，你吃早饭了吗？",
    # 撒娇系
    "早啊...懒得起床，你快来叫我~",
    "喵早安~今天好想赖床啊，你来陪我嘛",
    "早！还没睡够呢...你再陪我睡会",
    "早呀~今天周末诶，你怎么起这么早，不能多睡会吗",
    "早安...其实我昨晚熬夜刷手机了，现在困死了",
    # 调皮系
    "早~我还以为你要睡到中午呢",
    "早安！终于醒了？我还以为你冬眠了",
    "早呀~今天起这么早，是不是太阳打西边出来了",
    "早！你今天居然比我早，不敢相信",
    "早安~迟到没？不会又在赶地铁吧",
    # 关心系
    "早呀~昨晚睡得好吗？",
    "早安！今天外面有点凉，出门多穿点哦",
    "早~你昨晚好像睡挺晚的，今天别太累了",
    "早安！你昨天熬夜了吧，黑眼圈重不重？",
    "早呀~记得吃早饭，别又像上次一样胃疼",
    # 日常碎碎念
    "早！今天早上食堂的豆浆挺好喝的",
    "早安~刚看到窗外的猫在晒太阳，好可爱",
    "早呀~今天课好多，已经在去教室的路上了",
    "早安！今天终于没早课，幸福地赖了个床",
    "早~刚在宿舍群里看到个好笑的事，等下跟你说",
    # 周末特别
    "早呀~周末不用早起的感觉真好",
    "早安！周末诶，今天准备干嘛？",
    "早~周末终于可以慢慢吃早饭了",
    "早安！周末就是用来赖床的，你也别太早起啊",
    "早呀~周末的早上总是格外美好呢",
]

NIGHT_TEMPLATES = [
    # 关心催促系
    "都几点了还不睡？明天又该困了",
    "快去睡觉啦！熬夜对身体不好",
    "早点睡吧，明天还要早起呢",
    "你看看表，都几点了，赶紧睡！",
    "不准熬夜了，快去睡觉！",
    # 温柔道别系
    "晚安呀~做个好梦",
    "晚安，明天见~",
    "睡啦睡啦，困得眼睛都睁不开了",
    "晚安喵~梦里有我哦",
    "好梦~明天再聊",
    # 撒娇挽留系
    "那你睡吧...我明天等你",
    "晚安，虽然还想跟你再聊会，但你该睡了",
    "睡吧睡吧~明天第一个找我聊天哦",
    "晚安~不许偷偷找别人聊天",
    "好吧你睡吧，我也准备躺下了",
    # 次日约定系
    "晚安！明天记得告诉我做什么梦了",
    "睡吧~明天我要是起晚了你要叫我",
    "晚安，明天有个好玩的事跟你说",
    "好梦~明天起来第一个发消息给你",
    "晚安晚安，明天继续聊",
]


def _should_use_llm(scene: str, context: dict = None) -> bool:
    """判断是否应该使用 LLM 生成（真人化Q2）。

    90% 的场景走模板，仅 10% 的特殊场景使用 LLM。
    特殊场景包括：节日、久未聊、情绪波动大、或概率命中。
    """
    if scene in ("holiday", "silence", "sleep_nag", "checkin"):
        return True  # 这些场景内容变化大，始终用 LLM
    if context:
        if context.get("holiday"):
            return True  # 节日场景
        if context.get("mood_hint") and any(kw in context.get("mood_hint", "") for kw in
                                            ["情绪不好", "负面", "难过", "生气", "波动大", "不开心"]):
            return True  # 情绪波动大
    # 10% 概率走 LLM（给模板增加偶尔的新鲜感）
    if random.random() < 0.10:
        return True
    return False


# ---------- Phase 7：主动消息增强 ----------

async def _get_proactive_targets() -> list:
    """动态获取主动消息目标用户列表（Phase 7 + 真人化Q1）。

    从最近活跃的私聊会话中自动发现目标，而非硬编码 MY_QQ。
    只对好感度 >= 200（重要的人）且周互动>=3天的用户发送，按好感度降序，最多 5 人。
    """
    try:
        from ..database import get_active_sessions
        from ..database import get_affection as _get_affection
        from ..database import get_db
        import time as _time

        active = await get_active_sessions(hours=168)  # 最近一周
        now = _time.time()
        week_ago = now - 7 * 86400

        targets = []
        db = await get_db()
        for sid in active:
            if not sid.startswith("private_"):
                continue
            user_id = sid.replace("private_", "")
            aff = await _get_affection(user_id)
            score = aff.get("score", 0)
            if score < 200:
                continue

            # 周互动天数 >= 3（过去7天至少3天有消息）
            try:
                async with db.execute(
                    """SELECT COUNT(DISTINCT DATE(timestamp, 'unixepoch')) as active_days
                       FROM memories WHERE session_id = ? AND role = 'user'
                       AND timestamp >= ?""",
                    (sid, week_ago)
                ) as cursor:
                    row = await cursor.fetchone()
                    weekly_active_days = row["active_days"] if row else 0
            except Exception:
                weekly_active_days = 0

            if weekly_active_days < 3:
                continue

            targets.append((user_id, score))

        # 按好感度降序排列，最多5人
        targets.sort(key=lambda x: x[1], reverse=True)
        user_ids = [t[0] for t in targets[:5]]
        logger.info(f"[主动消息] 自动发现 {len(user_ids)} 个目标用户（好感度≥200 + 周互动≥3天）")
        return user_ids
    except (OSError, ValueError, TypeError):
        return [str(MY_QQ)] if MY_QQ else []
