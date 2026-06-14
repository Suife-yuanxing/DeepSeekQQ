"""Stage: 群聊过滤 — 群消息的@提及/昵称匹配/热度/气氛综合决策。"""
import random
import re
import time
from typing import Optional

from nonebot import logger

from ..config import RANDOM_REPLY_CHANCE
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("group_filter")
async def _stage_group_filter(ctx: ChatContext) -> Optional[str]:
    if not ctx.is_group:
        return None

    # 群聊热度状态机：每条消息都会更新热度
    from ..group_heat import heat_manager
    is_at_me = ctx.event.is_tome()
    heat_state = await heat_manager.on_message(ctx.session_id, is_at_bot=is_at_me)

    # 始终响应: @我
    if is_at_me:
        ctx.raw_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', ctx.raw_msg).strip()
        if not ctx.raw_msg:
            ctx.raw_msg = "在吗"
        # 将热度状态注入上下文，供 prompt 使用
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
        return None

    # 昵称匹配
    nicknames = ["念念", "kitty", "bot", "机器人"]
    if any(nick in ctx.raw_msg for nick in nicknames):
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
        return None

    # 热度活跃状态下，有一定概率主动插话
    if heat_state == "active" and heat_manager.should_interject(ctx.session_id):
        logger.info(f"[群聊] 热度活跃插话 (heat={heat_manager.get_heat(ctx.session_id):.2f})")
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
        return None

    # 气氛感知（替代简单的随机回复）
    from ..group_atmosphere import should_join_conversation
    from ..db_group import get_recent_group_messages
    # 获取最近真实群聊消息（Bug 4 修复：之前传入的是单条假数据）
    group_id = getattr(ctx.event, 'group_id', None)
    if group_id:
        recent = await get_recent_group_messages(str(group_id), limit=30)
    else:
        recent = []
    # 注：memories 表不存储群成员真实 user_id，多用户检测暂时受限
    # 但使用真实消息时间戳已能正确判断冷场/节奏空隙
    decision = should_join_conversation(recent, ctx.bot.self_id)

    if decision["should_reply"]:
        # 根据置信度决定是否回复
        if random.random() < decision["confidence"] * 0.5:
            logger.info(f"[群聊] 参与对话: {decision['reason']}")
            ctx.group_heat_state = heat_state
            ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
            return None
    elif random.random() < RANDOM_REPLY_CHANCE:
        # 保留原有的小概率随机回复
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
        return None

    return _SKIP
