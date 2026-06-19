"""Stage: 作息中断 — 深夜/吃饭时间按概率跳过回复。"""
import random
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from ..handler_helpers import make_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("schedule_interrupt")
async def _stage_schedule_interrupt(ctx: ChatContext) -> Optional[str]:
    """作息规律：根据时间决定是否中断消息处理。真人化Q6：can_interrupt=False 时跳过非紧急主动消息。"""
    if not ctx.schedule:
        return None
    schedule = ctx.schedule

    # 凌晨 sleeping：30% 概率不回复
    if schedule.period == "sleeping" and random.random() < 0.3:
        logger.info("[作息] 深夜不回复（sleeping）")
        return _SKIP

    # 吃饭时间：15% 概率回"在吃饭"
    if schedule.period == "meal" and random.random() < 0.15:
        meal_msgs = ["在吃饭呢~等下聊", "先吃饭！", "等我吃完~", "正吃着呢~"]
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(random.choice(meal_msgs))))
        logger.info("[作息] 吃饭中断")
        return _SKIP

    # 真人化Q6：不可中断活动时，跳过非紧急主动消息
    if not ctx.can_interrupt and not ctx.is_proactive:
        # 非主动消息且不可中断 → 降低回复概率
        if random.random() < 0.5:
            logger.info(f"[作息] 活动不可中断（{ctx.activity_hint}），跳过回复")
            return _SKIP

    return None
