"""备忘录/提醒模块（Phase 4）。

功能：
- 自然语言解析提醒请求
- 创建/查询/取消提醒
- 定时触发提醒
- 上下文感知（提醒未到期时的自然回复）
"""
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import pytz
from nonebot import logger

from . import api
from .database import cancel_reminder
from .database import find_reminder_by_content
from .database import get_due_reminders
from .database import get_user_reminders
from .database import mark_reminder_done
from .database import reschedule_reminder
from .database import save_reminder

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
    # "凌晨3点"
    (r"凌晨(\d{1,2})点(?:半)?", lambda m: _today_at(int(m.group(1)), 30 if "半" in m.group(0) else 0)),
    # "早上8点" / "上午10点"
    (r"(?:早上?|上午)(\d{1,2})点(?:半)?", lambda m: _today_at(int(m.group(1)), 30 if "半" in m.group(0) else 0)),
    # "下午3点"
    (r"下午(\d{1,2})点(?:半)?", lambda m: _today_at(int(m.group(1)) + 12, 30 if "半" in m.group(0) else 0)),
    # "晚上10点" / "今晚10点"
    (r"(?:晚上|今晚)(\d{1,2})点(?:半)?", lambda m: _today_at(int(m.group(1)) + 12, 30 if "半" in m.group(0) else 0)),
    # 兜底："10点"（无修饰词时，1-5点视为下午，6-11点视为上午，12点不变）
    (r"(\d{1,2})点(?:半)?", lambda m: _today_at(int(m.group(1)) + (12 if 1 <= int(m.group(1)) <= 5 else 0), 30 if "半" in m.group(0) else 0)),
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
        from .utils import clean_json_text
        clean = clean_json_text(raw)
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

async def generate_reminder_reply(scene: str, **kwargs) -> str:
    """用 LLM 基于念念人设生成个性化的提醒相关回复。"""
    try:
        if scene == "create_success":
            prompt = (
                f"用户设置了提醒。时间：{kwargs['time_str']}，内容：{kwargs['content']}"
                f"{'，每天重复' if kwargs.get('repeat') == 'daily' else '，每周重复' if kwargs.get('repeat') == 'weekly' else ''}。"
                "用你的性格回复确认，1句话，口语化，不要括号动作。"
            )
        elif scene == "create_fail":
            prompt = f"用户想设提醒但你没听懂。原因：{kwargs.get('error', '时间解析失败')}。用你的性格回复，让他再说清楚一点，1句话，口语化。"
        elif scene == "list":
            items = kwargs.get("items", [])
            lines = []
            for i, r in enumerate(items, 1):
                dt = datetime.fromtimestamp(r["trigger_time"], tz=TZ)
                t = dt.strftime("%m月%d日 %H:%M")
                repeat = "每天" if r["repeat_type"] == "daily" else "每周" if r["repeat_type"] == "weekly" else ""
                lines.append(f"{i}. {t} {r['content']}{' (' + repeat + ')' if repeat else ''} ID:{r['id']}")
            prompt = (
                f"用户的提醒列表：\n{'chr(10)'.join(lines)}\n"
                "用你的性格开头介绍一下，然后列出清单，最后告诉他可以取消。口语化，不要括号动作。"
            )
        elif scene == "cancel_success":
            prompt = f"用户取消了提醒 #{kwargs['rid']}。用你的性格回复确认，1句话，口语化，不要括号动作。"
        elif scene == "cancel_fail":
            prompt = "用户想取消提醒但ID不对。用你的性格回复，让他再确认一下ID，1句话，口语化，不要括号动作。"
        elif scene == "no_reminder":
            prompt = "用户想取消提醒但没有提供ID。用你的性格回复，让他告诉你ID，1句话，口语化，不要括号动作。"
        elif scene == "fire":
            prompt = (
                f"提醒时间到了！内容：{kwargs['content']}。"
                f"当前时间：{kwargs.get('time_str', '')}。"
                "用你的性格提醒用户，1-2句话，口语化，不要括号动作。可以加一点关心的语气。"
            )
        else:
            return ""

        sys = (
            "你是林念念，一个21岁的普通女孩，正在QQ上和朋友聊天。你的性格：活泼开朗、会调侃、嘴硬、偶尔撒娇、隐藏好色属性。"
            "口语化、短句、像发QQ消息。不要加括号动作。只输出回复内容，不要任何其他文字。"
        )
        messages = [
            {"role": "system", "content": sys},
            {"role": "user", "content": prompt}
        ]
        reply = await api.call_deepseek_api(messages, temperature=0.9)
        reply = re.sub(r'[（(][^）)]*[）)]', '', reply).strip()
        if len(reply) > 5:
            return reply
    except Exception as e:
        logger.warning(f"[提醒] LLM回复生成失败: {e}")

    # fallback
    fallbacks = {
        "create_success": f"好哒~我会在 {kwargs.get('time_str', '')} 提醒你「{kwargs.get('content', '')}」，记下了喵~",
        "create_fail": f"嗯？我没太听懂你想提醒什么...{kwargs.get('error', '')}，再说清楚一点嘛~",
        "cancel_success": f"好哒，提醒 #{kwargs.get('rid', '')} 已经取消了~",
        "cancel_fail": "嗯？找不到这个提醒呢，是不是ID不对？",
        "no_reminder": "告诉我你要取消的提醒ID嘛~",
        "fire": f"呐呐~ 到时间了哦！提醒你：「{kwargs.get('content', '')}」",
    }
    return fallbacks.get(scene, "")


async def create_reminder(user_id: str, session_id: str, user_msg: str) -> str:
    """创建提醒，返回给用户的确认消息。"""
    parsed = await parse_reminder(user_msg)

    if not parsed.success:
        return await generate_reminder_reply("create_fail", error=parsed.error)

    reminder_id = await save_reminder(
        user_id=user_id,
        session_id=session_id,
        content=parsed.content,
        trigger_time=parsed.trigger_time,
        repeat_type=parsed.repeat_type,
        original_msg=user_msg,
    )

    dt = datetime.fromtimestamp(parsed.trigger_time, tz=TZ)
    time_str = dt.strftime("%m月%d日 %H:%M")

    return await generate_reminder_reply(
        "create_success",
        time_str=time_str,
        content=parsed.content,
        repeat=parsed.repeat_type,
    )


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

        # 用 LLM 生成个性化的提醒消息
        dt = datetime.now(TZ)
        time_str = dt.strftime("%H:%M")
        msg = await generate_reminder_reply("fire", content=content, time_str=time_str)

        try:
            if session_id.startswith("group_"):
                group_id = int(session_id.replace("group_", ""))
                await bot.send_group_msg(group_id=group_id, message=msg)
            else:
                await bot.send_private_msg(user_id=int(user_id), message=msg)
            logger.info(f"[提醒] 已发送: user={user_id} content={content[:30]}")
        except Exception as e:
            logger.error(f"[提醒] 发送失败: {e}")

        # 处理重复提醒：使用原始设定时间计算下次触发，避免延迟漂移
        if repeat_type == "daily":
            original_dt = datetime.fromtimestamp(reminder["trigger_time"], tz=TZ)
            next_time = _tomorrow_at(original_dt.hour, original_dt.minute)
            await reschedule_reminder(rid, next_time)
        elif repeat_type == "weekly":
            next_time = reminder["trigger_time"] + 7 * 86400
            await reschedule_reminder(rid, next_time)
        else:
            await mark_reminder_done(rid)


async def list_reminders(user_id: str) -> str:
    """列出用户的所有待提醒。"""
    reminders = await get_user_reminders(user_id)
    if not reminders:
        return await generate_reminder_reply("list", items=[])

    return await generate_reminder_reply("list", items=reminders)


async def cancel_reminder_by_id(user_id: str, reminder_id: int) -> str:
    """取消提醒。"""
    success = await cancel_reminder(user_id, reminder_id)
    if success:
        return await generate_reminder_reply("cancel_success", rid=reminder_id)
    return await generate_reminder_reply("cancel_fail", rid=reminder_id)


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
