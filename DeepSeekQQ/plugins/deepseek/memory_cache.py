"""记忆缓存与会话管理：跨会话上下文恢复、工作记忆、会话状态、情绪回忆。

包含公开 API：recover_session_context。
"""
import json as _json
import random
import re
import time
from typing import Any
from typing import Dict
from typing import Optional

from nonebot import logger

from .database import get_recent_memories
from .database import get_session_state
from .database import save_session_state


def _format_time_ago(hours_ago: float) -> str:
    """格式化时间为自然描述。"""
    if hours_ago < 1:
        return "刚才"
    elif hours_ago < 8:
        return f"{int(hours_ago)}小时前"
    elif hours_ago < 24:
        return "昨天"
    elif hours_ago < 48:
        return "前天"
    else:
        return f"{int(hours_ago / 24)}天前"


def _build_bot_emotion_memory_hint(state: dict, hours_ago: float) -> Optional[str]:
    """从 session_state 的 bot_mood_snapshot 构建 bot 情绪回忆提示。"""
    snapshot_str = state.get("bot_mood_snapshot", "{}")
    if not snapshot_str or snapshot_str == "{}":
        return None

    try:
        snapshot_data = _json.loads(snapshot_str)
    except (_json.JSONDecodeError, TypeError):
        return None

    mood_data = snapshot_data.get("mood")
    if not mood_data:
        return None

    dominant = mood_data.get("dominant", "平静")
    if dominant == "平静":
        return None

    reason = mood_data.get("reason", "")
    valence = mood_data.get("valence", 0)

    # 时间衰减：越久越不容易触发
    if hours_ago < 2:
        chance = 0.8
    elif hours_ago < 8:
        chance = 0.6
    elif hours_ago < 24:
        chance = 0.4
    elif hours_ago < 72:
        chance = 0.25
    elif hours_ago < 168:  # 7天
        chance = 0.1
    else:
        return None  # 超过7天不回忆

    if random.random() > chance:
        return None

    # 根据情绪类型生成不同的回忆提示
    time_desc = _format_time_ago(hours_ago)

    negative_hints = {
        "生气": [
            f"你{time_desc}和他聊天时有点生气" + (f"（因为{reason}）" if reason else "") + "。如果他态度好了，可以傲娇地消气，不要太冷淡。",
            f"你{time_desc}有点小脾气" + (f"，因为{reason}" if reason else "") + "。现在可以稍微傲娇一点，但不要一直生气。",
        ],
        "难过": [
            f"你{time_desc}和他聊天时有点难过" + (f"（{reason}）" if reason else "") + "。如果他关心你了，可以稍微透露一点，但不要太明显。",
            f"你{time_desc}心情不太好" + (f"，因为{reason}" if reason else "") + "。现在可以稍微提一下，但不要刻意。",
        ],
        "吃醋": [
            f"你{time_desc}吃醋了" + (f"（因为{reason}）" if reason else "") + "。可以傲娇地提一下，但不要太认真。",
            f"你{time_desc}有点醋意" + (f"，因为{reason}" if reason else "") + "。现在可以酸酸地暗示一下。",
        ],
        "委屈": [
            f"你{time_desc}有点委屈" + (f"（{reason}）" if reason else "") + "。如果他哄你了，可以顺势消气。",
        ],
        "担心": [
            f"你{time_desc}有点担心" + (f"（{reason}）" if reason else "") + "。可以关心地问一下后续。",
        ],
    }

    positive_hints = {
        "开心": [
            f"你{time_desc}和他聊天很开心。可以延续好心情，语气轻快一些。",
            f"你{time_desc}心情不错。继续保持愉快的氛围。",
        ],
        "害羞": [
            f"你{time_desc}有点害羞。可以稍微提一下，逗逗他。",
            f"你{time_desc}被逗害羞了。现在可以傲娇地回忆一下。",
        ],
        "得意": [
            f"你{time_desc}有点得意。可以延续自信的语气。",
        ],
        "兴奋": [
            f"你{time_desc}很兴奋。可以延续热情的氛围。",
        ],
    }

    # 负面情绪：valence < -0.2
    if valence < -0.2 and dominant in negative_hints:
        return random.choice(negative_hints[dominant])
    # 正面情绪：valence > 0.2
    elif valence > 0.2 and dominant in positive_hints:
        return random.choice(positive_hints[dominant])
    # 中性但非平静
    elif dominant in negative_hints:
        return random.choice(negative_hints[dominant])
    elif dominant in positive_hints:
        return random.choice(positive_hints[dominant])

    return None


async def recover_session_context(session_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """在会话首条消息时，从 session_state 恢复上次对话的上下文。

    只在会话「新鲜」时触发（当前会话历史为空或仅 1 条）。
    返回一个包含 recall_prompt 的字典，用于注入 system prompt。
    """
    try:
        state = await get_session_state(session_id)
        if not state:
            return None

        # 检查当前会话是否「新鲜」（刚启动，还没有历史）
        recent = await get_recent_memories(session_id, 3)
        if len(recent) > 1:
            return None  # 会话已有活跃对话，不需要恢复

        last_interaction = state.get("last_interaction", 0)
        if last_interaction == 0:
            return None

        hours_ago = (time.time() - last_interaction) / 3600
        topic = state.get("last_topic", "")
        emotion = state.get("last_emotion", "")

        # 超过 30 天不恢复（太久远了）
        if hours_ago > 720:
            return None

        # 构建自然的时间描述
        time_hint = _format_time_ago(hours_ago)

        recall_prompt = ""
        if topic:
            recall_prompt = (
                f"你{time_hint}和他在聊「{topic}」，"
                f"当时他{emotion}。" if emotion else
                f"你{time_hint}和他在聊「{topic}」。"
            )
            recall_prompt += (
                "如果他现在说的话和之前有关，自然地接上话题——"
                "不用说「上次聊到」「之前说过」之类的废话，就像一直在聊一样自然接话。"
            )

        # 检查情绪快照（情绪记忆功能）
        mood_care_hint = None
        try:
            from .db_mood import get_last_mood_snapshot
            from .db_mood import get_mood_care_hint
            snapshot = await get_last_mood_snapshot(user_id)
            if snapshot:
                mood_care_hint = get_mood_care_hint(snapshot)
        except Exception:
            pass

        # 检查 bot 情绪回忆（跨会话情绪记忆）
        bot_emotion_memory_hint = None
        try:
            bot_emotion_memory_hint = _build_bot_emotion_memory_hint(state, hours_ago)
        except Exception:
            pass

        logger.info(f"[会话恢复] {session_id[:20]}... 上次: {topic[:30] if topic else '无'} ({time_hint})")
        result = {
            "last_topic": topic,
            "last_emotion": emotion,
            "time_hint": time_hint,
            "recall_prompt": recall_prompt,
        }
        if mood_care_hint:
            result["mood_care_hint"] = mood_care_hint
        if bot_emotion_memory_hint:
            result["bot_emotion_memory_hint"] = bot_emotion_memory_hint
        return result
    except Exception as e:
        logger.info(f"[会话恢复] 失败（非关键）: {e}")
        return None


async def _update_scratchpad_task(session_id: str, user_id: str, raw_msg: str, reply_text: str, bot_mood: dict = None):
    """P0-3: 异步更新跨轮工作记忆。"""
    try:
        from .db_session import get_session_state as _get_state
        from .db_session import save_session_state as _save_state
        from .prompt import update_scratchpad

        state = await _get_state(session_id) or {}
        current = state.get("scratchpad", "")
        emotion = bot_mood.get("dominant", "") if bot_mood else ""

        new_scratchpad = await update_scratchpad(
            session_id, raw_msg, reply_text, current, emotion
        )
        if new_scratchpad:
            # 直接 UPDATE scratchpad 列
            await _save_state(session_id, scratchpad=new_scratchpad)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[记忆] scratchpad 更新跳过: {e}")


async def _update_session_state(session_id: str, raw_msg: str, reply_text: str, bot_mood: dict = None):
    """每次回复后异步更新 session_state，记录当前对话状态。"""
    try:
        import json
        # 提取简要话题（用户消息中的前几个中文词）
        topic_keywords = re.findall(r'[一-鿿]{2,8}', raw_msg)
        topic = "、".join(topic_keywords[:3]) if topic_keywords else "闲聊"

        # 提取用户可能的情绪
        emotion = ""
        if any(kw in raw_msg for kw in ["哈哈", "笑", "开心", "好", "棒", "喜欢"]):
            emotion = "心情不错"
        elif any(kw in raw_msg for kw in ["累", "烦", "难过", "哭", "气"]):
            emotion = "情绪不太好"
        elif any(kw in raw_msg for kw in ["？", "吗", "怎么", "为什么"]):
            emotion = "在问问题"

        # 构建 bot_mood_snapshot JSON（保留 farewell_time 兼容性）
        # 先读取现有数据，保留 farewell_time
        from .db_session import get_session_state as _get_state
        existing = await _get_state(session_id)
        snapshot_data = {}
        if existing and existing.get("bot_mood_snapshot"):
            try:
                snapshot_data = json.loads(existing["bot_mood_snapshot"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 写入 bot 情绪快照
        if bot_mood and bot_mood.get("dominant", "平静") != "平静":
            snapshot_data["mood"] = {
                "valence": round(bot_mood.get("valence", 0), 3),
                "arousal": round(bot_mood.get("arousal", 0.2), 3),
                "dominant": bot_mood.get("dominant", "平静"),
                "reason": bot_mood.get("trigger_reason", "")[:50],
                "time": time.time(),
            }

        bot_mood_json = json.dumps(snapshot_data, ensure_ascii=False) if snapshot_data else "{}"

        await save_session_state(
            session_id,
            topic=topic[:30],
            emotion=emotion,
            context_summary=f"用户: {raw_msg[:100]} | 回复: {reply_text[:100]}",
            bot_mood=bot_mood_json,
        )
        logger.debug(f"[会话状态] {session_id[:20]}... 已更新: {topic[:20]}")
    except Exception as e:
        logger.debug(f"[会话状态] 更新失败（非关键）: {e}")
