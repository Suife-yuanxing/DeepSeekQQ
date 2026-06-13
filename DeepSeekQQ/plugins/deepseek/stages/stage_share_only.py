"""Stage: 仅分享回复 — 处理只有分享链接/图片没有文字的纯分享消息。"""
import random
import re
from typing import Optional

from nonebot.adapters.onebot.v11 import Message

from ..api import call_deepseek_api
from ..handler_helpers import make_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..share_parser import get_recent_shares
from ..utils import filter_novel_actions


async def _handle_link_share(ctx: ChatContext):
    """处理纯链接分享的 LLM 回复生成。"""
    # 获取分享内容
    recent = get_recent_shares(ctx.session_id)
    last_share = recent[-1] if recent else None

    # 构建分享内容描述
    share_desc = ""
    fetch_failed = False

    if last_share:
        share_type = last_share.get("type", "链接")
        share_source = last_share.get("source", "")
        share_summary = last_share.get("summary", "")
        fetch_failed = last_share.get("fetch_failed", False)

        if fetch_failed:
            share_desc = f"用户发了一个{share_type}链接，但内容无法读取。"
        elif share_summary and len(share_summary) > 10:
            share_desc = f"用户发了一个{share_type}：{share_source}\n内容摘要：{share_summary[:300]}"
        else:
            share_desc = f"用户发了一个{share_type}链接：{share_source}"
    else:
        share_desc = "用户发了一个链接，没有其他文字。"

    # 构建系统提示
    # 判断是否为视频平台分享（需主动讨论而非仅确认）
    is_video_share = (
        last_share and (
            (last_share.get("restricted")
             and last_share.get("platform") in ("douyin", "bilibili"))
            or last_share.get("type") in ("视频内容", "视频文件")
        )
    )

    from ..prompt import get_minimal_persona
    if is_video_share:
        share_sys = get_minimal_persona(
            "用户给你发了一个视频分享，没有说其他话。"
            "回复1-3句话，主动评论/吐槽/讨论这个视频的内容（基于标题和描述）。"
            "不要只说「看到了」「收到」「让我看看」这种废话，要说点有内容的。"
            "只输出回复内容。"
        )
    else:
        share_sys = get_minimal_persona(
            "用户给你发了一个链接/分享，没有说其他话。"
            "回复1句话表示你看到了。只输出回复内容。"
        )

    # 如果内容无法读取，添加反编造规则
    if fetch_failed:
        share_sys += (
            "\n\n重要：这个链接的内容无法读取（可能是视频或需要登录）。"
            "你没有看到任何内容，所以绝对不要编造内容！"
            "直接说「我这边打不开这个链接诶」「没看到内容哦」或类似的话。"
        )

    share_messages = [
        {"role": "system", "content": share_sys},
        {"role": "user", "content": share_desc}
    ]
    try:
        share_reply = await call_deepseek_api(share_messages, temperature=1.0)
        share_reply = filter_novel_actions(share_reply).strip()
        if len(share_reply) > 3:
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(share_reply)))
        else:
            raise ValueError("回复太短")
    except Exception:
        if fetch_failed:
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("我这边打不开这个链接诶")))
        else:
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("喵？什么东西，让我看看~")))


@stage("share_only_reply")
async def _stage_share_only(ctx: ChatContext) -> Optional[str]:
    if not ctx.raw_msg and ctx.has_share:
        recent = get_recent_shares(ctx.session_id)
        last_share = recent[-1] if recent else None
        # 图片分享走LLM回复流程，不在此阶段跳过
        if last_share and last_share.get("type") == "图片":
            return None

        # B2 fix: 表情包分享不再绕过 pipeline，改为设置 raw_msg 走正常流程
        if last_share and last_share.get("type") == "表情":
            emoji_text = last_share.get("summary", "")
            emoji_match = re.search(r'用户发送了(?:QQ表情|QQ商城表情|QQ内置表情|表情)[：:]?\s*(.+?)]', emoji_text)
            emoji_name = emoji_match.group(1).strip() if emoji_match else "表情"
            safe_emoji = emoji_name.replace("{", "").replace("}", "").replace("system", "").replace("assistant", "").replace("user", "")[:20]
            sticker_emotion = last_share.get("sticker_emotion", "")

            # 构建上下文提示
            context_hint = ""
            recent_msgs = getattr(ctx, 'recent_memories', []) or []
            if recent_msgs:
                user_msgs = [m["content"] for m in recent_msgs[-6:] if m.get("role") == "user"][-3:]
                if user_msgs:
                    context_hint = f"\n聊天上下文：{' | '.join(user_msgs)}"
            emotion_hint = f"\n这个表情的情绪是「{sticker_emotion}」" if sticker_emotion else ""

            ctx.raw_msg = f"用户给你发了一个QQ表情「{safe_emoji}」，没有说其他话。{context_hint}{emotion_hint}"
            ctx.emoji_share_name = safe_emoji
            ctx.emoji_share_emotion = sticker_emotion
            return None  # 不跳过 pipeline，走正常 LLM 回复流程

        # 视频平台分享（抖音/B站）：群聊中也总是回复，让bot主动分析视频
        is_video_share = (
            last_share and (
                (last_share.get("restricted")
                 and last_share.get("platform") in ("douyin", "bilibili")
                 and last_share.get("type") == "网页")
                or last_share.get("type") in ("视频内容", "视频文件")
            )
        )
        if is_video_share or not ctx.is_group or ctx.event.is_tome() or random.random() < 0.3:
            if last_share:
                await _handle_link_share(ctx)
        return _SKIP
    return None
