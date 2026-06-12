"""承诺追踪 — bot 说过"明天告诉你"后会真的兑现。

从 bot 回复中提取承诺，到期后推送提醒。
偶尔（20%）故意忘记，之后道歉。
"""
import asyncio
import random
import re
import time
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ============================================================
# 承诺检测正则
# ============================================================

_PROMISE_PATTERNS: List[tuple] = [
    (re.compile(r'明天(?:再|就|帮)?(.{2,20}?)(?:吧|哦|啦|哈|~|！|。|$|，)'), "明天"),
    (re.compile(r'下次(?:再|就|帮)?(.{2,20}?)(?:吧|哦|啦|哈|~|！|。|$|，)'), "下次"),
    (re.compile(r'回头(?:再|就|帮)?(.{2,20}?)(?:吧|哦|啦|哈|~|！|。|$|，)'), "回头"),
    (re.compile(r'等(?:下|会)(?:再|就|帮)?(.{2,20}?)(?:吧|哦|啦|哈|~|！|。|$|，)'), "等下"),
    (re.compile(r'晚点(?:再|就|帮)?(.{2,20}?)(?:吧|哦|啦|哈|~|！|。|$|，)'), "晚点"),
]

# 排除模式（不是承诺）
_EXCLUDE_PATTERNS = [
    re.compile(r'明天见|明天聊|明天再说|下次见|下次聊'),
    re.compile(r'不知道|不清楚|不记得|忘了|没想好'),
]

# ============================================================
# 常量
# ============================================================

_FORGET_RATE = 0.20    # 故意遗忘概率
_FORGIVEN_WINDOW = 86400 * 3  # 遗忘后3天内道歉

# 道歉模板
_FORGOTTEN_APOLOGIES = [
    "啊！我之前是不是说要{content}来着...完全忘了😭",
    "等等，我好像答应过要{content}...让我想想",
    "突然想起来之前说要{content}，结果忘了...对不起！",
    "唔，之前说的{content}我是不是鸽了...",
    "天哪我忘记要{content}了！现在补上还来得及吗...",
]

# 兑现模板
_PROMISE_FULFILL_TEMPLATES = [
    "对了，之前说要{promise}！",
    "想起来答应你的{promise}~",
    "之前说的{promise}，现在可以告诉你啦！",
    "之前说要{promise}的，我来了！",
]


# ============================================================
# 核心函数
# ============================================================

def extract_promises(reply_text: str, user_id: str, session_id: str) -> List[dict]:
    """从 bot 回复中提取承诺。

    Returns: [{"promise_text": str, "due_hint": str, "created_at": float}, ...]
    """
    promises = []
    for pattern, hint in _PROMISE_PATTERNS:
        for match in pattern.finditer(reply_text):
            matched_text = match.group(0).strip()
            # 排除不是承诺的匹配
            if any(ep.search(matched_text) for ep in _EXCLUDE_PATTERNS):
                continue
            # 太短的不算承诺（<4字）
            if len(matched_text) < 4:
                continue
            promises.append({
                "user_id": user_id,
                "session_id": session_id,
                "promise_text": matched_text,
                "due_hint": hint,
                "created_at": time.time(),
            })
    return promises


def should_forget() -> bool:
    """20%概率故意忘记承诺。"""
    return random.random() < _FORGET_RATE


def estimate_due_time(due_hint: str, created_at: float) -> float:
    """根据 due_hint 估算到期时间戳。"""
    if due_hint == "明天":
        return created_at + 86400 + random.randint(0, 14400)  # 明天+随机0-4小时
    elif due_hint in ("等下", "晚点"):
        return created_at + random.randint(1800, 7200)  # 0.5-2小时后
    elif due_hint == "回头":
        return created_at + random.randint(3600, 21600)  # 1-6小时后
    elif due_hint == "下次":
        return created_at + random.randint(86400, 259200)  # 1-3天后
    return created_at + 86400


def get_forgotten_apology(promise_text: str) -> str:
    """生成遗忘道歉消息。"""
    template = random.choice(_FORGOTTEN_APOLOGIES)
    return template.format(content=promise_text)


def get_fulfill_prefix(promise_text: str) -> str:
    """生成兑现承诺的前缀。"""
    template = random.choice(_PROMISE_FULFILL_TEMPLATES)
    return template.format(promise=promise_text)


# ============================================================
# 数据库操作
# ============================================================

async def save_promise(promise: dict) -> Optional[int]:
    """保存承诺到数据库。"""
    try:
        from .database import get_db
        db = await get_db()
        cursor = await db.execute(
            """INSERT INTO promises (user_id, session_id, promise_text, due_hint,
               created_at, due_at, fulfilled, forgotten)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (promise["user_id"], promise["session_id"], promise["promise_text"],
             promise.get("due_hint", ""), promise["created_at"], promise["due_at"],
             promise.get("fulfilled", 0), promise.get("forgotten", 0))
        )
        await db.commit()
        return cursor.lastrowid
    except Exception as e:
        logger.error(f"[承诺追踪] 保存失败: {e}")
        return None


async def get_due_promises() -> List[dict]:
    """获取所有到期未兑现的承诺（未遗忘的）。"""
    try:
        from .database import get_db
        db = await get_db()
        now = time.time()
        async with db.execute(
            """SELECT * FROM promises
               WHERE due_at <= ? AND fulfilled = 0 AND forgotten = 0
               ORDER BY due_at ASC LIMIT 20""",
            (now,)
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        logger.error(f"[承诺追踪] 查询到期承诺失败: {e}")
        return []


async def get_forgotten_to_apologize() -> List[dict]:
    """获取遗忘且该道歉的承诺（超过 due_at 但还未道歉）。"""
    try:
        from .database import get_db
        db = await get_db()
        now = time.time()
        async with db.execute(
            """SELECT * FROM promises
               WHERE forgotten = 1 AND apologized_at IS NULL
               AND due_at <= ? AND due_at >= ?
               ORDER BY due_at ASC LIMIT 10""",
            (now, now - _FORGIVEN_WINDOW)
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows] if rows else []
    except Exception as e:
        logger.error(f"[承诺追踪] 查询遗忘承诺失败: {e}")
        return []


async def mark_fulfilled(promise_id: int) -> bool:
    """标记承诺为已兑现。"""
    try:
        from .database import get_db
        db = await get_db()
        await db.execute(
            "UPDATE promises SET fulfilled = 1, fulfilled_at = ? WHERE id = ?",
            (time.time(), promise_id)
        )
        await db.commit()
        return True
    except Exception as e:
        logger.error(f"[承诺追踪] 标记兑现失败: {e}")
        return False


async def mark_apologized(promise_id: int) -> bool:
    """标记承诺已道歉。"""
    try:
        from .database import get_db
        db = await get_db()
        await db.execute(
            "UPDATE promises SET apologized_at = ? WHERE id = ?",
            (time.time(), promise_id)
        )
        await db.commit()
        return True
    except Exception as e:
        logger.error(f"[承诺追踪] 标记道歉失败: {e}")
        return False


async def process_bot_reply(reply_text: str, user_id: str, session_id: str):
    """处理 bot 回复：提取承诺并保存（在 post_process 阶段调用）。

    这是主入口函数。
    """
    promises = extract_promises(reply_text, user_id, session_id)
    for p in promises:
        p["due_at"] = estimate_due_time(p["due_hint"], p["created_at"])
        if should_forget():
            p["forgotten"] = 1
        p["fulfilled"] = 0
        p_id = await save_promise(p)
        if p_id:
            logger.info(
                f"[承诺追踪] 新承诺: {p['promise_text'][:30]} "
                f"due={p['due_hint']} forget={p['forgotten']}"
            )
