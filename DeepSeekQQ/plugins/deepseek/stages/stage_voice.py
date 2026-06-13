"""Stage: 语音识别 — STT 语音转文字 + 语音情绪特征提取。"""
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from ..config import STT_ENABLED
from ..handler_helpers import make_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..stt import recognize_voice
from ..voice import get_voice_tracker


@stage("voice_recognition")
async def _stage_voice(ctx: ChatContext) -> Optional[str]:
    if not STT_ENABLED:
        return None
    has_voice = any(seg.type == "record" for seg in ctx.event.get_message())
    if has_voice:
        # 通知语音上下文追踪器：用户发了语音
        tracker = get_voice_tracker()
        tracker.user_sent_voice_message(ctx.user_id)

        if not ctx.raw_msg:
            recognized = await recognize_voice(ctx.event)
            if recognized:
                ctx.raw_msg = recognized
                logger.info(f"[STT] 语音识别结果: {ctx.raw_msg[:50]}")

                # 语音情绪识别（P1）：异步提取音频特征
                from ..stt import download_voice
                from ..stt import extract_voice_url
                voice_url = extract_voice_url(ctx.event)
                if voice_url:
                    local_path = await download_voice(voice_url)
                    if local_path:
                        from ..voice_emotion import extract_voice_features
                        features = await extract_voice_features(local_path)
                        if features:
                            ctx.voice_features = features
                            logger.info(f"[语音情绪] {features.get('estimated_emotion', '未知')} | 音量:{features.get('rms_volume', 0):.0f}")
            else:
                logger.info("[STT] 语音识别失败或无内容")
                try:
                    await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("听不太清楚呢...能打字告诉我吗？")))
                except Exception as e:
                    logger.warning(f"[STT] 发送语音失败提示失败: {e}")
                return _SKIP
    # 检测用户文字中是否提及语音相关词（用于上下文追踪）
    voice_mention_kw = ["发语音", "语音", "说话", "听听", "打电话", "讲话", "讲讲话"]
    if any(kw in ctx.raw_msg for kw in voice_mention_kw):
        tracker = get_voice_tracker()
        tracker.user_mentioned_voice(ctx.user_id)
    return None
