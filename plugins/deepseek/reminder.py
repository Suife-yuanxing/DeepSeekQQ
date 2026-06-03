"""备忘录/提醒模块（Phase 4）。

功能：
- 自然语言解析提醒请求
- 创建/查询/取消提醒
- 定时触发提醒
- 上下文感知（提醒未到期时的自然回复）
"""
import re
import json
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

import pytz
from nonebot import logger

from . import api
from .database import (
    save_reminder, get_due_reminders, mark_reminder_done,
    reschedule_reminder, get_user_reminders, cancel_reminder,
    find_reminder_by_content
)

TZ = pytz.timezone('Asia/Shanghai')


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ReminderParseResult:
    success: bool
    trigger_time: Optional[float] = None  # unix timestamp
    content: str = ""                      # 提醒内容
    repeat_type: str = "none"              # none/daily/weekly/monthly
    error: str = ""                        # 解析失败原因


# ============================================================
# 自然语言时间解析
# ============================================================

# 兜底：正则匹配常见格式
_TIME_PATTERNS = [
    # "3小时后" / "3个小时后"
    (r"(\d+)\s*(?:个)?小时后", lambda m: time.time() + int(m.group(1)) * 3600),
    # "30分钟后" / "30分钟以后"
    (r"(\d+)\s*分钟后", lambda m: time.time() + int(m.group(1)) * 60),
    # "明天早上8点"
    (r"明天(?:早上?|上午)?(\d{1,2})点", lambda m: _tomorrow_at(int(m.group(1)), 0)),
    # "明天下午3点"
    (r"明天下午(\d{1,2})点(?:半)?", lambda m: _tomorrow_at(int(m.group(1)) + 12, 30 if "半" in m.group(0) else 0)),
    # "后天早上8点"
    (r"后天(?:早上?|上午)?(\d{1,2})点", lambda m: _day_after_tomorrow_at(int(m.group(1)), 0)),
    # "晚上10点" / "10点"
    (r"(?:晚上|今晚)?(\d{1,2})点(?:半)?", lambda m: _today_at(int(m.group(1)) + (12 if "晚" in m.group(0) or int(m.group(1)) < 6 else 0), 30 if "半" in m.group(0) else 0)),
]


def _tomorrow_at(hour: int, minute: int = 0) -> float:
    now = datetime.now(TZ)
    target = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0) + timedelta(days=1)
    return target.timestamp()


def _day_after_tomorrow_at(hour: int, minute: int = 0) -> float:
    now = datetime.now(TZ)
    target = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0) + timedelta(days=2)
    return target.timestamp()


def _today_at(hour: int, minute: int = 0) -> float:
    now = datetime.now(TZ)
    target = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
    if target.timestamp() < now.timestamp():
        target += timedelta(days=1)
    return target.timestamp()


def _regex_parse_time(user_msg: str) -> Optional[float]:
    """正则兜底解析时间。"""
    for pattern, handler in _TIME_PATTERNS:
        match = re.search(pattern, user_msg)
        if match:
            try:
                return handler(match)
            except Exception:
                continue
    return None


# ============================================================
# LLM 时间解析
# ============================================================

_PARSE_PROMPT = """你是一个时间解析助手。从用户消息中提取提醒时间和内容。

当前时间：{now}

用户消息：{user_msg}

严格按以下JSON格式返回，不要有任何其他文字：
```json
{{
  "has_reminder": true/false,
  "datetime": "YYYY-MM-DDTHH:MM:SS",
  "content": "提醒内容（简短，10字内）",
  "repeat": "none/daily/weekly/monthly"
}}
```

规则：
1. 如果用户没有设置提醒的意图，has_reminder 为 false
2. datetime 必须是未来时间
3. content 提取用户想要被提醒做的事
4. 如果用户说"每天"/"每天早上"等，repeat 为 daily
5. 如果没有明确时间，默认为最近的整点或半点"""


async def parse_reminder(user_msg: str) -> ReminderParseResult:
    """用 LLM + 正则解析用户的提醒请求。"""
    now_str = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %A")

    # 先用 LLM 解析
    try:
        messages = [
            {"role": "system", "content": "你是一个时间解析助手，只输出JSON。"},
            {"role": "user", "content": _PARSE_PROMPT.format(now=now_str, user_msg=user_msg)}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.1)
        clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
        match = re.search(r'\{[\s\S]*\}', clean)
        if match:
            data = json.loads(match.group())
            if not data.get("has_reminder"):
                return ReminderParseResult(success=False, error="用户没有设置提醒的意图")

            # 解析时间
            dt_str = data.get("datetime", "")
            try:
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    dt = TZ.localize(dt)
                trigger_time = dt.timestamp()
            except ValueError:
                # 回退到正则
                trigger_time = _regex_parse_time(user_msg)

            if not trigger_time:
                return ReminderParseResult(success=False, error="无法解析时间")

            # 确保是未来时间
            if trigger_time < time.time():
                return ReminderParseResult(success=False, error="提醒时间已过去")

            content = data.get("content", "该做这件事啦~")
            repeat = data.get("repeat", "none")

            return ReminderParseResult(
                success=True,
                trigger_time=trigger_time,
                content=content,
                repeat_type=repeat,
            )
    except Exception as e:
        logger.warning(f"[提醒] LLM解析失败，回退正则: {e}")

    # 回退到正则解析
    trigger_time = _regex_parse_time(user_msg)
    if trigger_time and trigger_time > time.time():
        return ReminderParseResult(
            success=True,
            trigger_time=trigger_time,
            content="你设置的提醒到了~",
            repeat_type="none",
        )

    return ReminderParseResult(success=False, error="无法理解提醒时间")


# ============================================================
# 提醒管理
# ============================================================

async def create_reminder(user_id: str, session_id: str, user_msg: str) -> str:
    """创建提醒，返回给用户的确认消息。"""
    parsed = await parse_reminder(user_msg)

    if not parsed.success:
        return f"嗯？我没太听懂你想提醒什么...{parsed.error}，再说清楚一点嘛~"

    reminder_id = await save_reminder(
        user_id=user_id,
        session_id=session_id,
        content=parsed.content,
        trigger_time=parsed.trigger_time,
        repeat_type=parsed.repeat_type,
        original_msg=user_msg,
    )

    # 格式化时间
    dt = datetime.fromtimestamp(parsed.trigger_time, tz=TZ)
    time_str = dt.strftime("%m月%d日 %H:%M")
    repeat_str = ""
    if parsed.repeat_type == "daily":
        repeat_str = "（每天重复）"
    elif parsed.repeat_type == "weekly":
        repeat_str = "（每周重复）"

    return f"好哒~我会在 {time_str} 提醒你「{parsed.content}」{repeat_str}，记下了喵~"


async def check_and_fire_reminders(bot) -> None:
    """检查并触发到期的提醒。由 startup.py 的定时任务调用。"""
    due = await get_due_reminders()
    if not due:
        return

    for reminder in due:
        rid = reminder["id"]
        user_id = reminder["user_id"]
        session_id = reminder["session_id"]
        content = reminder["content"]
        repeat_type = reminder["repeat_type"]

        # 发送提醒消息
        dt = datetime.now(TZ)
        hour = dt.hour
        if 5 <= hour < 11:
            time_greeting = "早安~"
        elif 22 <= hour or hour < 5:
            time_greeting = "夜深了~"
        else:
            time_greeting = ""

        msg = f"{time_greeting}提醒你：「{content}」"

        try:
            if session_id.startswith("group_"):
                group_id = int(session_id.replace("group_", ""))
                await bot.send_group_msg(group_id=group_id, message=msg)
            else:
                await bot.send_private_msg(user_id=int(user_id), message=msg)
            logger.info(f"[提醒] 已发送: user={user_id} content={content[:30]}")
        except Exception as e:
            logger.error(f"[提醒] 发送失败: {e}")

        # 处理重复提醒
        if repeat_type == "daily":
            next_time = _tomorrow_at(dt.hour, dt.minute)
            await reschedule_reminder(rid, next_time)
        elif repeat_type == "weekly":
            next_time = time.time() + 7 * 86400
            await reschedule_reminder(rid, next_time)
        else:
            await mark_reminder_done(rid)


async def list_reminders(user_id: str) -> str:
    """列出用户的所有待提醒。"""
    reminders = await get_user_reminders(user_id)
    if not reminders:
        return "你目前没有待提醒的事项哦~"

    lines = ["你目前的提醒列表："]
    for i, r in enumerate(reminders, 1):
        dt = datetime.fromtimestamp(r["trigger_time"], tz=TZ)
        time_str = dt.strftime("%m月%d日 %H:%M")
        repeat = ""
        if r["repeat_type"] == "daily":
            repeat = " [每天]"
        elif r["repeat_type"] == "weekly":
            repeat = " [每周]"
        lines.append(f"{i}. {time_str} - {r['content']}{repeat} (ID:{r['id']})")

    lines.append("\n要取消哪个？告诉我ID就行~")
    return "\n".join(lines)


async def cancel_reminder_by_id(user_id: str, reminder_id: int) -> str:
    """取消提醒。"""
    success = await cancel_reminder(user_id, reminder_id)
    if success:
        return f"好哒，提醒 #{reminder_id} 已经取消了~"
    return f"嗯？找不到这个提醒呢，是不是ID不对？"


async def get_pending_reminders_context(user_id: str) -> str:
    """获取用户待提醒的上下文信息（用于注入 prompt）。"""
    reminders = await get_user_reminders(user_id)
    if not reminders:
        return ""

    now = time.time()
    lines = []
    for r in reminders[:3]:  # 最多3条
        dt = datetime.fromtimestamp(r["trigger_time"], tz=TZ)
        time_str = dt.strftime("%m月%d日 %H:%M")
        remaining = r["trigger_time"] - now
        if remaining > 3600:
            remain_str = f"还有{int(remaining/3600)}小时"
        elif remaining > 60:
            remain_str = f"还有{int(remaining/60)}分钟"
        else:
            remain_str = "马上就到了"
        lines.append(f"- {r['content']}（{time_str}，{remain_str}）")

    return "【用户提醒】\n" + "\n".join(lines)


# ============================================================
# 意图识别
# ============================================================

_REMINDER_KEYWORDS = [
    "提醒我", "提醒", "叫我", "闹钟", "别忘了", "记住",
    "到时候", "几点叫我", "定时", "备忘",
]

_LIST_KEYWORDS = ["我有哪些提醒", "提醒列表", "查看提醒", "我的提醒"]

_CANCEL_KEYWORDS = ["取消提醒", "删除提醒", "不用提醒了", "去掉提醒"]


def is_reminder_request(msg: str) -> str:
    """判断消息是否是提醒相关请求。

    Returns:
        "create" / "list" / "cancel" / "" (不是提醒请求)
    """
    if any(kw in msg for kw in _CANCEL_KEYWORDS):
        return "cancel"
    if any(kw in msg for kw in _LIST_KEYWORDS):
        return "list"
    if any(kw in msg for kw in _REMINDER_KEYWORDS):
        return "create"
    return ""
