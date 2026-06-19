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
    # 真人化 P2-3：改进正则——更广泛的动词匹配，捕获跨词承诺
    (re.compile(r'明天(?:我)?(?:再|就|帮|去|给|查|看|问|找|做|告诉|发|整理|处理|弄|搞|弄|准备|安排)?(.{2,30}?)(?:吧|哦|啦|哈|~|！|。|$|，|呢|啊)'), "明天"),
    (re.compile(r'下次(?:我)?(?:再|就|帮|去|给|查|看|问|找|做|告诉|发|整理|处理|弄|搞|弄|准备|安排)?(.{2,30}?)(?:吧|哦|啦|哈|~|！|。|$|，|呢|啊)'), "下次"),
    (re.compile(r'回头(?:我)?(?:再|就|帮|去|给|查|看|问|找|做|告诉|发|整理|处理|弄|搞|弄|准备|安排)?(.{2,30}?)(?:吧|哦|啦|哈|~|！|。|$|，|呢|啊)'), "回头"),
    (re.compile(r'等(?:下|会)(?:我)?(?:再|就|帮|去|给|查|看|问|找|做|告诉|发|整理|处理|弄|搞|弄|准备|安排)?(.{2,30}?)(?:吧|哦|啦|哈|~|！|。|$|，|呢|啊)'), "等下"),
    (re.compile(r'晚点(?:我)?(?:再|就|帮|去|给|查|看|问|找|做|告诉|发|整理|处理|弄|搞|弄|准备|安排)?(.{2,30}?)(?:吧|哦|啦|哈|~|！|。|$|，|呢|啊)'), "晚点"),
]

# 排除模式（不是承诺）
_EXCLUDE_PATTERNS = [
    re.compile(r'明天见|明天聊|明天再说|下次见|下次聊'),
    re.compile(r'不知道|不清楚|不记得|忘了|没想好'),
]

# ============================================================
# 常量
# ============================================================

_FORGIVEN_WINDOW = 86400 * 7  # 遗忘后7天内道歉（真人化 P2-5：原3天→7天，可配置）

# 真人化 P2-5：遗忘从二元改为渐进式
# 到期后不同时段的遗忘概率不同——越久越容易忘，但小概率还记得
_FORGET_STAGES = [
    # (到期后秒数上限, 遗忘概率)
    (7200,   0.10),   # 0-2h:   10% 忘
    (21600,  0.30),   # 2-6h:   30% 忘
    (86400,  0.60),   # 6-24h:  60% 忘
    (float("inf"), 0.80),  # >24h: 80% 忘（仍有20%记得）
]

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
                "source": "regex",  # 真人化 P2-3：标记提取来源
            })
    return promises


async def detect_implicit_promise(reply_text: str) -> Optional[str]:
    """使用 LLM 检测隐式承诺（真人化 P2-3）。

    当正则未能捕获明确承诺词（明天/下次/回头等），但 bot 的回复中
    包含了隐式承诺时（如「我去看看」「查到告诉你」「这个我研究一下」），
    由 LLM 判断是否构成承诺。

    Args:
        reply_text: bot 的回复文本

    Returns:
        提取到的承诺文本，或 None
    """
    # 先用正则快速预判——文本中是否有承诺关键词
    has_hint = any(kw in reply_text for kw in [
        "告诉你", "帮你看", "查一下", "找找", "问问",
        "去查", "去看", "去问", "去找", "去了解",
        "研究一下", "了解一下", "打探一下",
    ])
    if not has_hint:
        return None

    prompt = (
        "判断以下回复中是否包含一个「承诺」（bot 说之后会做某事）。\n\n"
        f"回复：{reply_text[:200]}\n\n"
        "如果包含承诺，只回复承诺内容（如「帮你查一下XX」）；"
        "如果不包含承诺，只回复「无」。\n"
        "注意：'明天见'、'下次聊'这类不是承诺，是道别。"
    )

    try:
        from .local_llm import query_local_llm
        result = await query_local_llm(
            prompt=prompt,
            max_tokens=80,
            temperature=0.3,  # 低温度，精确判断
        )
        if result and result.strip() not in ("", "无", "无。", "不包含", "没有"):
            cleaned = result.strip().rstrip("。！，.!, ")
            if len(cleaned) >= 4:
                return cleaned
    except Exception:
        pass

    return None


async def extract_promises_with_llm(
    reply_text: str, user_id: str, session_id: str
) -> List[dict]:
    """从 bot 回复中提取承诺（正则 + LLM 辅助，真人化 P2-3）。

    策略：
    1. 先用改进正则快速提取（第一层）
    2. 如果正则命中≥1 条且有足够的置信度 → 直接用正则结果
    3. 如果正则 0 命中但文本含动作提示词 → LLM 辅助提取
    4. LLM 仅在低置信度（regex_confidence < 0.7）时触发

    Returns: [{"promise_text": str, "due_hint": str, ...}, ...]
    """
    # 第一层：正则提取
    regex_promises = extract_promises(reply_text, user_id, session_id)

    # 计算正则置信度
    # 有明确的"明天/下次/回头/等下/晚点"关键词 → 高置信度
    has_explicit_due = any(
        kw in reply_text for kw in ["明天", "下次", "回头", "等下", "晚点"]
    )

    if regex_promises and has_explicit_due:
        # 正则高置信度，不触发 LLM
        return regex_promises

    # 正则低置信度 → LLM 辅助
    implicit = await detect_implicit_promise(reply_text)

    if implicit:
        # 估算 due_hint
        due_hint = "明天"
        if any(kw in reply_text for kw in ["下次", "改天"]):
            due_hint = "下次"
        elif any(kw in reply_text for kw in ["等下", "马上", "一会"]):
            due_hint = "等下"
        elif any(kw in reply_text for kw in ["晚点", "回头"]):
            due_hint = "回头"

        llm_promise = {
            "user_id": user_id,
            "session_id": session_id,
            "promise_text": implicit,
            "due_hint": due_hint,
            "created_at": time.time(),
            "source": "llm",
        }

        # 去重：如果 regex 已有相似承诺，不重复添加
        if regex_promises:
            existing_texts = [p["promise_text"] for p in regex_promises]
            if not any(implicit[:10] in et for et in existing_texts):
                return regex_promises + [llm_promise]
            return regex_promises
        return [llm_promise]

    return regex_promises


def should_forget(due_at: float = None) -> bool:
    """判断是否「忘记」承诺（真人化 P2-5：渐进式遗忘）。

    若提供 due_at（到期时间戳），根据当前时间与到期时间的差值，
    查 _FORGET_STAGES 确定遗忘概率。越久越容易忘。

    若未提供 due_at，使用创建时默认 20% 概率（向后兼容）。
    """
    if due_at is None:
        # 向后兼容：承诺创建时尚未有 due_at，默认 20%
        return random.random() < 0.20

    now = time.time()
    elapsed = now - due_at

    # elapsed < 0 表示还没到期 → 不会忘
    if elapsed < 0:
        return False

    for max_elapsed, prob in _FORGET_STAGES:
        if elapsed <= max_elapsed:
            return random.random() < prob

    # fallback（不应到达，但安全起见）
    return random.random() < 0.80


def get_forget_probability(due_at: float) -> float:
    """查询当前遗忘概率（不实际掷骰子，用于调试/日志）。

    Args:
        due_at: 承诺到期时间戳

    Returns:
        当前阶段的遗忘概率 (0.0~0.80)
    """
    now = time.time()
    elapsed = now - due_at
    if elapsed < 0:
        return 0.0
    for max_elapsed, prob in _FORGET_STAGES:
        if elapsed <= max_elapsed:
            return prob
    return 0.80


def estimate_due_time(due_hint: str, created_at: float, due_offset: float = None) -> tuple[float, float]:
    """根据 due_hint 估算到期时间戳。返回 (due_at, due_offset)。

    M12: 若提供 persisted_offset，则复用该偏移（重启后一致性）。
         否则生成随机偏移并返回，由调用方持久化。
    """
    if due_offset is not None:
        return created_at + due_offset, due_offset

    if due_hint == "明天":
        offset = random.randint(0, 14400)  # 明天+随机0-4小时
        return created_at + 86400 + offset, offset
    elif due_hint in ("等下", "晚点"):
        offset = random.randint(1800, 7200)  # 0.5-2小时后
        return created_at + offset, offset
    elif due_hint == "回头":
        offset = random.randint(3600, 21600)  # 1-6小时后
        return created_at + offset, offset
    elif due_hint == "下次":
        offset = random.randint(86400, 259200)  # 1-3天后
        return created_at + offset, offset
    offset = 86400  # 默认明天
    return created_at + offset, offset


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
    """保存承诺到数据库。M12: 同时持久化 due_offset。"""
    try:
        from .database import get_db
        db = await get_db()
        cursor = await db.execute(
            """INSERT INTO promises (user_id, session_id, promise_text, due_hint,
               created_at, due_at, fulfilled, forgotten, due_offset)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (promise["user_id"], promise["session_id"], promise["promise_text"],
             promise.get("due_hint", ""), promise["created_at"], promise["due_at"],
             promise.get("fulfilled", 0), promise.get("forgotten", 0),
             promise.get("due_offset", 0))
        )
        await db.commit()
        return cursor.lastrowid
    except Exception as e:
        await db.rollback()
        logger.error(f"[承诺追踪] 保存失败: {e}")
        return None


async def get_due_promises() -> List[dict]:
    """获取所有到期未兑现的承诺（含渐进式遗忘判定）。

    真人化 P2-5：查询时动态计算遗忘概率。越久未兑现越容易忘。
    返回的承诺中，已「遗忘」的会被过滤掉（但仍然可道歉）。
    """
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
        promises = [dict(r) for r in rows] if rows else []

        # 真人化 P2-5：渐进式遗忘——对每个到期承诺动态判断是否「忘了」
        result = []
        for p in promises:
            due_at = p.get("due_at", 0)
            forget_prob = get_forget_probability(due_at)
            if random.random() < forget_prob:
                # 标记为遗忘（下次不再提醒，但可道歉）
                await _mark_forgotten(p["id"])
                logger.debug(
                    f"[承诺追踪] 渐进遗忘: {p['promise_text'][:30]} "
                    f"过期{((now - due_at) / 3600):.1f}h → 遗忘概率{forget_prob:.0%} → 命中"
                )
                continue
            result.append(p)

        return result
    except Exception as e:
        logger.error(f"[承诺追踪] 查询到期承诺失败: {e}")
        return []


async def _mark_forgotten(promise_id: int) -> bool:
    """标记承诺为已遗忘（内部使用）。"""
    try:
        from .database import get_db
        db = await get_db()
        await db.execute(
            "UPDATE promises SET forgotten = 1, forgotten_at = ? WHERE id = ?",
            (time.time(), promise_id)
        )
        await db.commit()
        return True
    except Exception as e:
        await db.rollback()
        logger.error(f"[承诺追踪] 标记遗忘失败: {e}")
        return False


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
        await db.rollback()
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
        await db.rollback()
        logger.error(f"[承诺追踪] 标记道歉失败: {e}")
        return False


async def process_bot_reply(reply_text: str, user_id: str, session_id: str):
    """处理 bot 回复：提取承诺并保存（在 post_process 阶段调用）。

    这是主入口函数。
    M12: due_offset 持久化确保随机偏移在重启后保持一致。
    真人化 P2-5：遗忘从二元改为渐进式——创建时不做遗忘标记，
    而是在检查到期承诺时按 _FORGET_STAGES 动态计算遗忘概率。
    真人化 P2-3：使用正则+LLM 混合提取策略。
    """
    # 真人化 P2-3：正则 + LLM 混合提取
    promises = await extract_promises_with_llm(reply_text, user_id, session_id)
    for p in promises:
        due_at, offset = estimate_due_time(p["due_hint"], p["created_at"])
        p["due_at"] = due_at
        p["due_offset"] = offset
        # 真人化 P2-5：创建时不做遗忘决策，标记为 0
        # 遗忘概率在 get_due_promises 检查时按 _FORGET_STAGES 动态计算
        p["forgotten"] = 0
        p["fulfilled"] = 0
        source_tag = f" source={p.get('source', 'regex')}" if p.get('source') else ""
        p_id = await save_promise(p)
        if p_id:
            logger.info(
                f"[承诺追踪] 新承诺: {p['promise_text'][:30]} "
                f"due={p['due_hint']} offset={offset}s{source_tag}"
            )
