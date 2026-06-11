"""话题追问系统 — 让bot像真人一样会追问没回复的消息。

当bot发了提问/分享/调侃等消息，用户没回复时，过一段时间自然地追问。
根据追问次数递进情绪：期待 → 好奇 → 委屈/傲娇。
"""
import random
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11 import MessageEvent

from .api import call_deepseek_api
from .memory import save_reply
from .utils import filter_novel_actions
from .utils import get_session_id

# ============================================================
# 消息类型定义
# ============================================================

MSG_TYPE_QUESTION = "question"      # 提问："你今天吃了什么呀？"
MSG_TYPE_SHARE = "share"            # 分享："我刚看了个超好笑的视频"
MSG_TYPE_TEASE = "tease"            # 调侃/傲娇："切，谁想你了"
MSG_TYPE_EMOTIONAL = "emotional"    # 情绪表达："好无聊啊..."
MSG_TYPE_TOPIC = "topic"            # 抛话题："你觉得xxx怎么样？"
MSG_TYPE_REPLY = "reply"            # 普通回复（不追问）
MSG_TYPE_GREETING = "greeting"      # 问候（不追问）


# ============================================================
# 追问配置
# ============================================================

# 各类型的追问延迟（秒）和策略
FOLLOW_UP_CONFIG = {
    MSG_TYPE_QUESTION: {
        "min_delay": 300,      # 5分钟
        "max_delay": 900,      # 15分钟
        "max_follow_ups": 2,
        "emotion_progression": ["期待", "好奇", "委屈"],
    },
    MSG_TYPE_SHARE: {
        "min_delay": 600,      # 10分钟
        "max_delay": 1200,     # 20分钟
        "max_follow_ups": 1,
        "emotion_progression": ["期待", "好奇"],
    },
    MSG_TYPE_TEASE: {
        "min_delay": 180,      # 3分钟
        "max_delay": 480,      # 8分钟
        "max_follow_ups": 2,
        "emotion_progression": ["傲娇", "委屈", "生气"],
    },
    MSG_TYPE_EMOTIONAL: {
        "min_delay": 300,      # 5分钟
        "max_delay": 600,      # 10分钟
        "max_follow_ups": 1,
        "emotion_progression": ["期待", "委屈"],
    },
    MSG_TYPE_TOPIC: {
        "min_delay": 480,      # 8分钟
        "max_delay": 900,      # 15分钟
        "max_follow_ups": 1,
        "emotion_progression": ["好奇", "委屈"],
    },
}


# ============================================================
# 会话状态追踪
# ============================================================

@dataclass
class SessionFollowUpState:
    """单个会话的追问状态。"""
    last_bot_msg: str = ""              # bot最后一条消息内容
    last_bot_msg_type: str = ""         # 消息类型
    last_bot_msg_time: float = 0.0      # 发送时间
    follow_up_count: int = 0            # 已追问次数
    next_follow_up_time: float = 0.0    # 下次追问时间
    current_emotion: str = "期待"       # 当前情绪
    user_replied: bool = True           # 用户是否已回复


# 全局状态：session_id -> SessionFollowUpState
_session_states: Dict[str, SessionFollowUpState] = {}
_MAX_SESSION_STATES = 500


def get_session_state(session_id: str) -> SessionFollowUpState:
    """获取或创建会话状态。"""
    if session_id not in _session_states:
        # 容量保护：清理已回复且超过 1 小时的旧状态
        if len(_session_states) >= _MAX_SESSION_STATES:
            now = time.time()
            to_remove = [
                sid for sid, s in _session_states.items()
                if s.user_replied and (now - s.last_bot_msg_time) > 3600
            ]
            for sid in to_remove[:len(to_remove) // 2]:
                del _session_states[sid]
        _session_states[session_id] = SessionFollowUpState()
    return _session_states[session_id]


def record_bot_message(session_id: str, msg_text: str, msg_type: str):
    """记录bot发送的消息，用于后续追问判断。

    在bot发送消息后调用，记录消息内容和类型。
    """
    state = get_session_state(session_id)

    # 普通回复和问候不追踪
    if msg_type in (MSG_TYPE_REPLY, MSG_TYPE_GREETING):
        return

    state.last_bot_msg = msg_text
    state.last_bot_msg_type = msg_type
    state.last_bot_msg_time = time.time()
    state.follow_up_count = 0
    state.user_replied = False

    # 计算下次追问时间
    config = FOLLOW_UP_CONFIG.get(msg_type)
    if config:
        delay = random.uniform(config["min_delay"], config["max_delay"])
        state.next_follow_up_time = state.last_bot_msg_time + delay
        state.current_emotion = config["emotion_progression"][0]

    logger.info(f"[追问] 记录bot消息 | type={msg_type} | 下次追问: {int(delay)}s后 | session={session_id[:8]}")


def record_user_reply(session_id: str):
    """记录用户回复，取消追问。"""
    state = get_session_state(session_id)
    if not state.user_replied and state.follow_up_count > 0:
        # 用户终于回复了，可以有情绪反转
        logger.info(f"[追问] 用户已回复 | 追问次数={state.follow_up_count} | session={session_id[:8]}")
    state.user_replied = True
    state.follow_up_count = 0
    state.last_bot_msg = ""
    state.last_bot_msg_type = ""


def suppress_followup(session_id: str):
    """强制取消该 session 的所有待发追问（用于对话疲劳收尾）。"""
    state = get_session_state(session_id)
    if state.follow_up_count > 0 or not state.user_replied:
        logger.info(f"[追问] 疲劳抑制 | 取消追问 | session={session_id[:8]}")
    state.user_replied = True
    state.follow_up_count = 0
    state.last_bot_msg = ""
    state.last_bot_msg_type = ""
    state.pending_emotion = None


# ============================================================
# 消息类型判断
# ============================================================

def classify_bot_message(reply_text: str) -> str:
    """判断bot回复的消息类型。

    基于关键词和标点符号判断，简单但够用。
    """
    text = reply_text.strip()

    # 问候
    if any(kw in text for kw in ["早安", "晚安", "早上好", "晚上好", "睡了吗", "起来了"]):
        return MSG_TYPE_GREETING

    # 提问：包含问号
    if "？" in text or "?" in text:
        return MSG_TYPE_QUESTION

    # 调侃/傲娇
    tease_keywords = ["切", "哼", "才不是", "谁想你", "才不要", "笨蛋", "傻", "略略略",
                      "不理你", "讨厌", "烦人", "滚", "去你的"]
    if any(kw in text for kw in tease_keywords):
        return MSG_TYPE_TEASE

    # 情绪表达
    emotional_keywords = ["好无聊", "好累", "好困", "好饿", "好难过", "好开心",
                          "想你", "无聊", "寂寞", "难过", "开心", "兴奋"]
    if any(kw in text for kw in emotional_keywords):
        return MSG_TYPE_EMOTIONAL

    # 分享：较长的消息，可能是分享内容
    if len(text) > 30:
        return MSG_TYPE_SHARE

    # 抛话题
    topic_keywords = ["你觉得", "你认为", "你怎么看", "你喜不喜欢", "你有没有",
                      "想不想", "要不要", "我们去", "一起"]
    if any(kw in text for kw in topic_keywords):
        return MSG_TYPE_TOPIC

    # 默认：普通回复
    return MSG_TYPE_REPLY


# ============================================================
# 追问检查（定时任务调用）
# ============================================================

async def check_follow_ups(bot: Bot):
    """检查所有会话，对超时未回复的用户发送追问。

    由loop_manager每2分钟调用一次。
    """
    now = time.time()
    checked = 0
    followed_up = 0

    for session_id, state in list(_session_states.items()):
        # 跳过：用户已回复 / 没有追踪消息 / 还没到追问时间
        if state.user_replied:
            continue
        if not state.last_bot_msg_type:
            continue
        if now < state.next_follow_up_time:
            continue

        config = FOLLOW_UP_CONFIG.get(state.last_bot_msg_type)
        if not config:
            continue

        # 检查是否超过最大追问次数
        if state.follow_up_count >= config["max_follow_ups"]:
            # 不再追问，重置状态
            state.user_replied = True
            continue

        checked += 1

        # 更新情绪
        progressions = config["emotion_progression"]
        if state.follow_up_count < len(progressions):
            state.current_emotion = progressions[state.follow_up_count]

        # 生成追问消息
        try:
            follow_up_msg = await _generate_follow_up(
                state.last_bot_msg,
                state.last_bot_msg_type,
                state.current_emotion,
                state.follow_up_count,
            )

            if follow_up_msg:
                # 发送追问
                user_id = session_id.split("_")[-1] if "_" in session_id else session_id
                from nonebot.adapters.onebot.v11 import Message as OBMessage
                await bot.send_private_msg(user_id=int(user_id), message=OBMessage(follow_up_msg))
                await save_reply(session_id, user_id, "[追问]", follow_up_msg)

                state.follow_up_count += 1
                # 计算下次追问时间
                if state.follow_up_count < config["max_follow_ups"]:
                    delay = random.uniform(config["min_delay"], config["max_delay"])
                    state.next_follow_up_time = now + delay
                followed_up += 1

                logger.info(f"[追问] 已追问 | 第{state.follow_up_count}次 | emotion={state.current_emotion} | session={session_id[:8]}")

        except Exception as e:
            logger.error(f"[追问] 生成/发送失败: {e}")

    if checked > 0:
        logger.debug(f"[追问] 检查完成 | 检查={checked} | 已追问={followed_up}")


# ============================================================
# LLM 生成追问消息
# ============================================================

async def _generate_follow_up(
    original_msg: str,
    msg_type: str,
    emotion: str,
    follow_up_count: int,
) -> Optional[str]:
    """用LLM生成自然的追问消息。"""

    # 情绪描述
    emotion_hints = {
        "期待": "有点期待用户的回复，语气轻快",
        "好奇": "好奇用户在干嘛，语气试探",
        "委屈": "觉得被忽略了，语气有点委屈",
        "傲娇": "嘴上说不在意但其实想要回复",
        "生气": "有点小脾气，但不要太凶",
    }
    emotion_hint = emotion_hints.get(emotion, "自然地追问")

    # 追问次数描述
    if follow_up_count == 0:
        count_hint = "这是第一次追问，语气自然随意"
    else:
        count_hint = "之前已经追问过一次了，这次语气更强烈一点"

    prompt = (
        "你是林念念，一个普通的21岁女孩，正在和朋友聊天。\n"
        f"你之前发了一条消息：「{original_msg[:100]}」\n"
        f"这条消息的类型是{msg_type}。\n"
        f"用户没有回复。\n\n"
        f"现在你想追问一下。当前情绪：{emotion}（{emotion_hint}）。\n"
        f"{count_hint}。\n\n"
        "要求：\n"
        "1. 生成一条简短的追问消息，1句话\n"
        "2. 要自然口语化，像真人朋友没收到回复时会说的话\n"
        "3. 不要说「你怎么不回我」「在吗」这种太直白的\n"
        "4. 可以用颜文字、省略号、语气词\n"
        "5. 根据情绪调整语气\n\n"
        "只输出追问消息内容，不要其他任何文字。"
    )

    try:
        result = await call_deepseek_api(
            [{"role": "user", "content": prompt}],
            temperature=0.9,
            task_type="chat",
            max_tokens=100,
        )
        result = filter_novel_actions(result)
        # 清理：去掉引号、多余换行
        result = result.strip().strip('"').strip("'").strip()
        if len(result) > 50:
            result = result[:50]
        return result if result else None
    except Exception as e:
        logger.error(f"[追问] LLM生成失败: {e}")
        return None
