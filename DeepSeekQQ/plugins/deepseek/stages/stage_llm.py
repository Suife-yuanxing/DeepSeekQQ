"""Stage: LLM 调用 — 构建 system prompt + 用户消息，调用 DeepSeek API 生成回复。"""
import time
from datetime import datetime
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from ..api import call_deepseek_api
from ..config import ANALYSIS_HISTORY_LIMIT
from ..config import CHAT_HISTORY_MULTIPLIER
from ..config import REPLY_LENGTH_CONFIG
from ..handler_helpers import detect_greeting_type
from ..handler_helpers import get_morning_time_hint
from ..handler_helpers import get_night_affection_hint
from ..handler_helpers import is_night_farewell
from ..handler_helpers import make_reply
from ..handler_helpers import parse_target_lines
from ..mcp_client import build_tools_prompt
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..prompt import build_system_prompt
from ..prompt import estimate_reply_length
from ..search import format_search_for_prompt
from ..share_parser import get_recent_shares
from ..share_prompt import build_analysis_prompt
from ..utils import filter_novel_actions
from ..utils import safe_task


@stage("llm_call")
async def _stage_llm(ctx: ChatContext) -> Optional[str]:
    # 手机命令已直接处理，跳过 LLM 避免编造回复
    if ctx.skip_llm:
        return None

    shares_now = get_recent_shares(ctx.session_id)

    # 场景路由 — 构建场景提示（prompt_templates 集成）
    scene_hint = ""
    if ctx.scenes:
        try:
            from ..prompt_templates import get_scene_templates
            from ..prompt_templates import get_template
            template_names = get_scene_templates(ctx.scenes)
            extra_hints = []
            for name in template_names:
                if name in ("greeting_mode", "emotional_mode", "question_mode"):
                    content = get_template(name)
                    if content:
                        extra_hints.append(content)
            if extra_hints:
                scene_hint = "\n".join(extra_hints)
        except Exception as e:
            logger.warning(f"[场景提示] 加载模板失败: {e}")

    analysis_keywords = [
        "怎么看", "怎么讲", "分析一下", "评价", "观点", "有什么想法",
        "说说", "讲讲", "如何理解", "什么意思", "详细介绍", "详细说说"
    ]
    valid_shares = [s for s in shares_now if s.get("summary")]
    is_asking_analysis = any(kw in ctx.raw_msg for kw in analysis_keywords) and valid_shares

    if is_asking_analysis:
        analysis_prompt = build_analysis_prompt(valid_shares, ctx.raw_msg)
        if analysis_prompt == "[小黑盒内容需要用户粘贴正文后才能分析]":
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~")))
            return _SKIP

        length_info = {"target_lines": 4, "style": "专业分析+个性点评"}
        search_ctx = format_search_for_prompt(ctx.search_result) if ctx.search_result else ""
        sys_prompt = build_system_prompt(
            ctx.affection, ctx.mood, length_info, ctx.relevant_tags, shares_now, ctx.raw_msg,
            context_analysis=ctx.analysis.context, emotion_state=ctx.analysis.emotion,
            search_context=search_ctx, reminder_context=ctx.reminder_context,
            world_context=ctx.world_context, bot_mood=ctx.bot_mood_result,
            user_prefs=ctx.user_prefs, session_recovery=ctx.session_recovery,
            disclosure_hint=ctx.disclosure_hint["text"] if ctx.disclosure_hint else None,
            affection_decay_hint=ctx.affection_decay_hint,
            milestone_hint=ctx.milestone_hint,
            schedule=ctx.schedule,
            voice_features=ctx.voice_features,
            shared_memory_hint=ctx.shared_memory_hint or None,
            private_meme_hint=ctx.private_meme_hint or None,
            date_hint=ctx.date_hint or None,
            topic_bridge=ctx.topic_bridge or None,
            icebreaker_hint=ctx.icebreaker_hint or None,
            topic_transition=ctx.topic_transition or None,
            emotion_recovery_hint=ctx.emotion_recovery_hint or None,
            emotion_memory_hint=ctx.emotion_memory_hint or None,
            group_social_hint=ctx.group_social_hint or None,
            group_meme_hint=ctx.group_meme_hint or None,
            group_role_hint=ctx.group_role_hint or None,
            behavior_hint=ctx.behavior_hint or None,
            nickname_hint=ctx.nickname_hint or None,
            interest_hint=ctx.interest_hint or None,
            growth_hint=ctx.growth_hint or None,
            catchphrase_hint=ctx.catchphrase_hint or None,
            catchphrase_influence_hint=ctx.catchphrase_influence_hint or None,
            reply_gap_hint=ctx.reply_gap_hint or None,
            bot_emotion_memory_hint=ctx.bot_emotion_memory_hint or None,
            fatigue_hint=ctx.fatigue_hint or None,
            group_heat_desc=ctx.group_heat_description or None,
            scene_hint=scene_hint or None,
            bot_self_summary=ctx.user_profile_summary or None,
            activity_hint=ctx.activity_hint or None,
            personality_drift_hints=ctx.personality_drift_hints or None,
            value_hints=ctx.value_hints or None,
            past_opinions_hint=ctx.past_opinions_hint or None,
            scroll_hint=ctx.scroll_hint or None,
            should_inject_feed=ctx.should_inject_feed,
            heat_state=ctx.heat_state or None,
        )
        sys_prompt += "\n回复风格：专业分析+个性点评。分析部分结构化、有深度，点评部分保持你念念的语气。绝对禁止括号动作描写。"
        if ctx.scratchpad:
            sys_prompt += f"\n\n【当前对话状态】{ctx.scratchpad}"
        messages = [{"role": "system", "content": sys_prompt}]
        for mem in ctx.recent_memories[-ANALYSIS_HISTORY_LIMIT:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        messages.append({"role": "user", "content": analysis_prompt})
    else:
        length_info = estimate_reply_length(ctx.raw_msg, ctx.recent_memories, ctx.bot_mood_result)
        length_info["target_lines"] = parse_target_lines(ctx.emotion_params["target_lines"])

        # 活跃度修正：根据情绪/作息动态调整回复长度
        from ..behavior_engine import get_verbosity_modifier
        schedule_period = ctx.schedule.period if ctx.schedule else "active"
        bot_mood_dom = ctx.bot_mood_result.get("dominant", "平静") if ctx.bot_mood_result else "平静"
        verbosity = get_verbosity_modifier(schedule_period, bot_mood_dom,
                                           affection_score=ctx.affection.get("score", 0))
        # 真人化Q6：不可中断活动时回复更短（可能"没看到消息"）
        if not ctx.can_interrupt:
            verbosity *= 0.6
        # 真人化 P1-1/P1-3：CausalContext 活动强度 → 回复速度/长度 + 活动过渡
        try:
            from ..causal_context import get_cc_safe
            cc = get_cc_safe(ctx.session_id)
            if cc and cc.activity_intensity > 0.7:
                verbosity *= 0.7  # 高投入活动 → 回复更短
                ctx.activity_intensity_high = True
            if cc and cc.is_absent:
                verbosity *= 0.5  # 缺席状态 → 大幅缩短
            # 活动过渡事件：最近一次活动切换可能作为聊天话题
            if cc:
                recent_activity_events = [
                    e for e in cc.get_recent_events(5)
                    if e.source == "activity_sim"
                ]
                if recent_activity_events:
                    latest = recent_activity_events[-1]
                    ctx.activity_transition = latest.cause
        except Exception:
            pass
        length_info["target_lines"] = max(1, round(length_info["target_lines"] * verbosity))
        ep = ctx.emotion_params
        if ep["temperature"] >= 1.0:
            length_info["style"] = "活泼轻快"
        elif ep["temperature"] <= 0.6:
            length_info["style"] = "冷淡简短"
        elif ep["temperature"] <= 0.7:
            length_info["style"] = "温柔低落"
        search_ctx = format_search_for_prompt(ctx.search_result) if ctx.search_result else ""
        sys_prompt = build_system_prompt(
            ctx.affection, ctx.mood, length_info, ctx.relevant_tags, shares_now, ctx.raw_msg,
            context_analysis=ctx.analysis.context, emotion_state=ctx.analysis.emotion,
            search_context=search_ctx, reminder_context=ctx.reminder_context,
            world_context=ctx.world_context, bot_mood=ctx.bot_mood_result,
            user_prefs=ctx.user_prefs, session_recovery=ctx.session_recovery,
            disclosure_hint=ctx.disclosure_hint["text"] if ctx.disclosure_hint else None,
            affection_decay_hint=ctx.affection_decay_hint,
            milestone_hint=ctx.milestone_hint,
            schedule=ctx.schedule,
            voice_features=ctx.voice_features,
            shared_memory_hint=ctx.shared_memory_hint or None,
            private_meme_hint=ctx.private_meme_hint or None,
            date_hint=ctx.date_hint or None,
            topic_bridge=ctx.topic_bridge or None,
            icebreaker_hint=ctx.icebreaker_hint or None,
            topic_transition=ctx.topic_transition or None,
            emotion_recovery_hint=ctx.emotion_recovery_hint or None,
            emotion_memory_hint=ctx.emotion_memory_hint or None,
            group_social_hint=ctx.group_social_hint or None,
            group_meme_hint=ctx.group_meme_hint or None,
            group_role_hint=ctx.group_role_hint or None,
            behavior_hint=ctx.behavior_hint or None,
            nickname_hint=ctx.nickname_hint or None,
            interest_hint=ctx.interest_hint or None,
            growth_hint=ctx.growth_hint or None,
            catchphrase_hint=ctx.catchphrase_hint or None,
            catchphrase_influence_hint=ctx.catchphrase_influence_hint or None,
            reply_gap_hint=ctx.reply_gap_hint or None,
            bot_emotion_memory_hint=ctx.bot_emotion_memory_hint or None,
            fatigue_hint=ctx.fatigue_hint or None,
            group_heat_desc=ctx.group_heat_description or None,
            scene_hint=scene_hint or None,
            bot_self_summary=ctx.user_profile_summary or None,
            activity_hint=ctx.activity_hint or None,
            personality_drift_hints=ctx.personality_drift_hints or None,
            value_hints=ctx.value_hints or None,
            past_opinions_hint=ctx.past_opinions_hint or None,
        )

        # 语音通话模式：注入口语化 prompt
        if ctx.voice_mode:
            sys_prompt += (
                "\n【语音通话模式】你现在正在和用户进行语音通话，你的回复会被转成语音说出去。\n"
                "回复应该更像真实的口语对话：更短的句子、更自然的语气、更多的语气词。\n"
                "想象你真的在打电话，回复就像你在电话里会说出来的话。\n"
                "不要长篇大论，不要结构化分析，不要括号动作描写。\n"
                "控制在2-3句话以内，像正常人打电话一样的节奏。"
            )

        # MCP 工具注入：让 LLM 知道可以调用哪些工具
        tools_prompt = build_tools_prompt()
        if tools_prompt:
            sys_prompt += tools_prompt

        # 手机操作强化提示：检测到手机相关请求时，强调必须使用工具
        import re as _re
        _phone_kw = ['截图', '截屏', '屏幕', '打开', '点击', '滑动', '输入', '手机', '桌面', '返回', '微信', '抖音']
        if any(kw in ctx.raw_msg for kw in _phone_kw):
            from ..mcp_client import check_phone_permission, ensure_phone_bridge as _ensure_bridge
            if check_phone_permission(ctx.user_id):
                _bridge = await _ensure_bridge()
                if _bridge:
                    sys_prompt += (
                        "\n\n⚠️ 【手机控制可用】手机已在线！你可以直接操控用户的手机屏幕。\n"
                        "对于任何涉及手机屏幕的操作（截图、打开应用、点击、滑动、输入文字、查看屏幕内容），"
                        "你必须在回复中使用 [tool:工具名] 格式来调用工具，而不是凭空想象屏幕内容。\n"
                        "例如：用户说「帮我截图微信」，你应该回复：\n"
                        "[tool:phone_open_app] {\"app_name\": \"微信\"} [/tool]\n"
                        "[tool:phone_screenshot] {} [/tool]\n"
                        "用户说「屏幕上有啥」，你应该回复：\n"
                        "[tool:phone_screenshot] {} [/tool]\n"
                        "绝对不要假装看到了屏幕！没有调用工具你就真的看不到。"
                    )

        from ..database import has_user_message_today
        greeting_type = detect_greeting_type(ctx.raw_msg, ctx.recent_memories)

        # 检查是否是道别后又来聊天（5分钟内）
        is_comeback_after_farewell = False
        if greeting_type is None:
            from ..database import get_last_farewell_time
            farewell_time = await get_last_farewell_time(ctx.session_id)
            if farewell_time:
                minutes_since = (time.time() - farewell_time) / 60
                if 0 < minutes_since < 5:
                    is_comeback_after_farewell = True

        if greeting_type == "morning":
            hour = datetime.now().hour
            time_hint = get_morning_time_hint(hour)
            is_first = not await has_user_message_today(ctx.session_id)
            greet_hint = f"\n【问候感知】用户在跟你说早安。当前时间 {hour}:00。{time_hint}"
            if is_first:
                greet_hint += " 这是用户今天第一条消息，可以自然地问候一下。"
            greet_hint += "\n回复要求：自然、口语化，不要每次都像客服一样'早安~今天也要元气满满哦'。根据时间调整语气。"
            sys_prompt += greet_hint
        elif greeting_type == "night":
            affection_hint = get_night_affection_hint(ctx.affection)
            farewell_confidence = is_night_farewell(ctx.raw_msg, ctx.recent_memories)["confidence"]

            if farewell_confidence >= 0.8:
                # 高置信度道别
                sys_prompt += (
                    f"\n【道别感知】用户在跟你说晚安/要睡了。{affection_hint}"
                    "\n回复要求：短、温暖、不要追问、不要开启新话题。"
                    "像关灯一样自然地道别。1句话就够了。"
                )
                # 记录道别时间
                from ..database import record_farewell
                safe_task(record_farewell(ctx.user_id, ctx.session_id))
                # 取消追问（修复：说了晚安后追问系统还在追问）
                from ..follow_up import suppress_followup
                suppress_followup(ctx.session_id)
                logger.info(f"[晚安] 取消追问 session={ctx.session_id[:8]}")
            else:
                # 低置信度（可能是抱怨）
                sys_prompt += (
                    f"\n【可能道别】用户说'困了'之类的，但不确定是否真要睡。{affection_hint}"
                    "\n回复要求：可以关心一下，但不要直接说晚安。"
                    "比如'那就早点休息呀~'、'困了就睡呗~'。如果他真要睡会再说的。"
                )
        elif is_comeback_after_farewell:
            # 晚安后又来聊天！
            sys_prompt += (
                "\n【调侃机会】用户刚才说了晚安/要睡了，结果又发消息了！"
                "这是一个很好的调侃机会。语气要调皮、得意。"
                "比如：'怎么还没睡？'、'不是说困了吗~'、'嘴上说睡了身体很诚实嘛~'"
                "\n回复要求：简短、调侃、不要太长。1-2句话。"
            )
        elif ctx.is_first_today:
            hour = datetime.now().hour
            if 6 <= hour < 12:
                sys_prompt += (
                    "\n【首条消息感知】这是用户今天第一条消息。"
                    "自然地问候一下，但不要刻意说'早安'，除非用户先说。"
                    "比如'你来啦~'、'今天这么早？'之类的自然过渡。"
                )
                from ..database import log_proactive
                safe_task(log_proactive(
                    ctx.user_id, "private", "[感知式早安]", scene="morning_triggered"
                ))

        # 图片回复策略（P2）：基于人设的个性化图片回应（独立于上述条件）
        from ..image_reply import get_image_reply_prompt
        from ..image_reply import is_emotional_share
        from ..image_reply import should_analyze_in_detail
        # 仅使用当前消息的图片/视频分享（按时间戳过滤，防止旧内容泄漏）
        cutoff = getattr(ctx, 'share_cutoff', 0)
        current_shares = [s for s in shares_now if s.get("time", 0) >= cutoff]
        image_shares = [s for s in current_shares if s.get("type") == "图片" and s.get("vision_text")]
        video_shares = [s for s in current_shares if s.get("type") in ("视频内容", "视频文件") and s.get("vision_text")]

        if image_shares:
            latest_image = image_shares[-1]
            image_type = latest_image.get("image_type", "unknown")
            vision_text = latest_image.get("vision_text", "")
            affection_score = ctx.affection.get("score", 0)

            # 判断图片分析深度和回复策略
            detailed = should_analyze_in_detail(ctx.raw_msg, len(image_shares))
            emotional = is_emotional_share(ctx.raw_msg)

            image_prompt = get_image_reply_prompt(
                image_type, vision_text, affection_score, ctx.raw_msg, ctx.bot_mood_result
            )
            if image_prompt:
                if detailed:
                    image_prompt += "\n用户希望你详细分析这张图片，多说一些。"
                elif emotional:
                    image_prompt += "\n用户在分享有趣/好看的内容，附和一下就好，不用分析。"
                sys_prompt += f"\n{image_prompt}"

        # 真人化 P1-5/P1-2：非语言信号 + 情绪表达提示
        try:
            nvh = getattr(ctx, 'nonverbal_hint', None)
            if nvh:
                sys_prompt += f"\n\n{nvh}"
        except Exception:
            pass
        try:
            expr = getattr(ctx, 'emotion_expression', None)
            if expr and expr.get("should_express"):
                if expr["style"] == "explicit":
                    sys_prompt += f"\n\n【情绪表达】{expr['hint']}"
                elif expr["style"] == "micro":
                    sys_prompt += f"\n\n【微表达】{expr['hint']}"
        except Exception:
            pass

        if ctx.scratchpad:
            sys_prompt += f"\n\n【当前对话状态】{ctx.scratchpad}"

        # P2-3: OOC 注入提示（最近1小时内 ≥3 次 OOC 时提醒）
        try:
            from ..ooc_classifier import get_ooc_injection_hint
            ooc_hint = get_ooc_injection_hint(ctx.user_id)
            if ooc_hint:
                sys_prompt += f"\n\n{ooc_hint}"
                logger.debug(f"[OOC注入] user={ctx.user_id[:6]}: {ooc_hint[:80]}...")
        except Exception:
            pass

        messages = [{"role": "system", "content": sys_prompt}]
        # P0-2: 历史消息数 3x 提升（6 → 18），配合 28K token 预算
        history_limit = REPLY_LENGTH_CONFIG["context_depth"] * CHAT_HISTORY_MULTIPLIER * 3

        # 智能上下文选择（替代简单的保留最近N条）
        from ..context_optimizer import fit_messages_to_budget
        from ..context_optimizer import select_context_messages
        selected_memories = select_context_messages(ctx.recent_memories, ctx.raw_msg, history_limit)
        for mem in selected_memories:
            messages.append({"role": mem["role"], "content": mem["content"]})
        # 构造用户消息：有图片/视频时始终注入描述（无论是否有文字）
        user_msg_content = ctx.raw_msg
        # P1-2: 截断超长用户消息，防止撑爆 context
        from ..config import MAX_USER_MSG_CHARS
        if len(user_msg_content) > MAX_USER_MSG_CHARS:
            user_msg_content = user_msg_content[:MAX_USER_MSG_CHARS] + "…（消息太长已截断）"
            logger.info(f"[截断] 用户消息 {len(ctx.raw_msg)}→{MAX_USER_MSG_CHARS} 字符")
        if image_shares:
            vision_desc = image_shares[-1].get("vision_text", "")
            if vision_desc:
                img_info = f"[用户发送了一张图片，视觉模型已识别内容：{vision_desc[:300]}]"
                user_msg_content = f"{user_msg_content}\n{img_info}" if user_msg_content else img_info
        if video_shares:
            video_desc = video_shares[-1].get("vision_text", "")
            if video_desc:
                vid_info = f"[用户发送了一段视频，视觉模型已分析关键帧：{video_desc[:300]}]"
                user_msg_content = f"{user_msg_content}\n{vid_info}" if user_msg_content else vid_info
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": user_msg_content})

        # Token 预算管理：先尝试语义压缩，再硬截断
        from ..context_compressor import compress_context
        from ..context_compressor import estimate_messages_tokens
        from ..config import MAX_INPUT_TOKENS
        est_tokens = estimate_messages_tokens(messages)
        # BUGFIX: 仅当消息 token 超过预算 50% 时才触发压缩，避免浪费 API 调用
        if est_tokens > MAX_INPUT_TOKENS * 0.5:
            messages, compressed = await compress_context(
                ctx.session_id, messages, call_deepseek_api
            )
            if compressed:
                logger.debug(f"[上下文] 语义压缩完成 session={ctx.session_id[:20]}...")
        messages = fit_messages_to_budget(messages, sys_prompt)

        # 上下文优化统计（调试用）
        from ..context_optimizer import get_context_stats
        ctx_stats = get_context_stats(ctx.recent_memories, selected_memories, sys_prompt)
        if ctx_stats.get("token_saved", 0) > 0:
            logger.debug(f"[上下文] 压缩率={ctx_stats['compression_ratio']:.1%} 节省={ctx_stats['token_saved']}tokens")

    # 3D: Think-then-Speak — 复杂消息先做内部思考再生成回复
    think_hint = ""
    try:
        from ..thinker import build_think_prompt
        from ..thinker import format_think_result
        from ..thinker import should_think
        intent = ctx.analysis.context.user_intent if ctx.analysis else "闲聊"
        if should_think(ctx.raw_msg, ctx.complexity, intent):
            think_prompt = build_think_prompt(
                ctx.raw_msg,
                ctx.recent_memories,
                ctx.analysis.emotion.dominant if ctx.analysis else "平静",
                ctx.analysis.context.topic_summary if ctx.analysis else "",
                intent,
                ctx.affection.get("score", 0),
            )
            think_raw = await call_deepseek_api(
                [{"role": "user", "content": think_prompt}],
                temperature=0.3,
                task_type="analysis",
                max_tokens=200,
            )
            think_hint = format_think_result(think_raw)
            if think_hint:
                # 注入到 system prompt 末尾
                messages[0]["content"] += f"\n\n{think_hint}"
                logger.debug(f"[Think] 思考注入: {think_hint[:80]}...")
    except Exception as e:
        logger.debug(f"[Think] 思考阶段失败（不影响主回复）: {e}")

    try:
        ctx.reply_text = await call_deepseek_api(
            messages,
            temperature=ctx.emotion_params["temperature"],
            task_type="chat",
            max_tokens=ctx.emotion_params["max_tokens"],
        )
    except Exception as e:
        logger.error(f"[LLM] API 调用失败: {e}")
        ctx.reply_text = "唔…我脑子转不过来了，等下再聊~"
    ctx.reply_text = filter_novel_actions(ctx.reply_text)
    return None
