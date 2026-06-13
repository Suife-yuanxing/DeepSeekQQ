"""Stage: 语音通话 — 检测进入/退出语音通话意图。仅私聊生效。"""
from typing import Optional

from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..voice import send_farewell_voice
from ..voice import send_greeting_voice
from ..voice_call import detect_voice_intent
from ..voice_call import enter_voice_mode
from ..voice_call import exit_voice_mode
from ..voice_call import is_in_voice_mode
from ..voice_call import touch_activity


@stage("voice_call")
async def _stage_voice_call(ctx: ChatContext) -> Optional[str]:
    """语音通话模式：检测进入/退出意图，切换状态。

    仅私聊生效。群聊消息直接跳过。
    """
    if ctx.is_group:
        return None

    session_id = ctx.session_id
    intent = detect_voice_intent(ctx.raw_msg)

    if intent == "enter":
        enter_voice_mode(session_id)
        ctx.voice_mode = True
        # fire-and-forget 发接听语音
        from ..utils import safe_task
        safe_task(send_greeting_voice(ctx.bot, ctx.event))
        if not ctx.raw_msg or len(ctx.raw_msg) <= 3:
            # 纯触发词（如"打电话"），不发额外回复
            return _SKIP
        # 触发词+内容（如"打电话 你在干嘛"），去掉触发词继续处理
        for kw in ["语音通话", "语音聊天", "接电话", "打电话", "开语音", "通话"]:
            if kw in ctx.raw_msg:
                ctx.raw_msg = ctx.raw_msg.replace(kw, "").strip()
                break

    elif intent == "exit":
        if exit_voice_mode(session_id):
            from ..utils import safe_task
            safe_task(send_farewell_voice(ctx.bot, ctx.event))
        ctx.voice_mode = False
        if not ctx.raw_msg or len(ctx.raw_msg) <= 3:
            # 纯退出词（如"挂了"）
            return _SKIP

    elif is_in_voice_mode(session_id):
        # 已在语音模式中，更新活跃时间
        touch_activity(session_id)
        ctx.voice_mode = True

    return None
