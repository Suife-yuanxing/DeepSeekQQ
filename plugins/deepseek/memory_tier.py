"""记忆层级模块 — 借鉴 nmem (6-tier) + Undefined (3-layer cognitive memory)。

定义四层记忆体系，按重要性/持久性组织：
- Pinned Memory: 用户明确标记"记住这个"的永久记忆
- Long-Term Memory: 压缩/提炼后的事实和共享回忆
- Short-Term Memory: 最近对话的记忆标签和偏好
- Working Memory: 当前会话的活动状态（scratchpad）

使用: get_tiered_memory() 返回各层记忆，prompt.py 中按层级注入。
"""
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger


# ============================================================
# 记忆条目数据类
# ============================================================

class MemoryEntry:
    """统一记忆条目。"""
    __slots__ = ("content", "source", "confidence", "created_at", "last_accessed", "tier")

    def __init__(self, content: str, source: str = "", confidence: float = 0.5,
                 created_at: float = None, tier: str = "short_term"):
        self.content = content
        self.source = source
        self.confidence = confidence
        self.created_at = created_at or time.time()
        self.last_accessed = time.time()
        self.tier = tier

    def to_prompt(self) -> str:
        """转为 prompt 可用的字符串。"""
        return f"[{self.content}]"


# ============================================================
# 层级定义
# ============================================================

TIER_CONFIG = {
    "pinned": {
        "label": "固定记忆",
        "inject_prefix": "【你必须记住】",
        "max_items": 5,
        "priority": 100,
    },
    "long_term": {
        "label": "长期记忆",
        "inject_prefix": "【你们之间的回忆】",
        "max_items": 5,
        "priority": 80,
    },
    "short_term": {
        "label": "短期记忆",
        "inject_prefix": "【你记得的】",
        "max_items": 3,
        "priority": 60,
    },
    "working": {
        "label": "工作记忆",
        "inject_prefix": "【当前对话状态】",
        "max_items": 1,
        "priority": 50,
    },
}

# Prompt 注入顺序（从高优先级到低）
_INJECTION_ORDER = ("working", "short_term", "long_term", "pinned")


# ============================================================
# 核心 API
# ============================================================

async def get_tiered_memory(
    user_id: str,
    session_id: str,
    current_msg: str,
    scratchpad: str = "",
) -> Dict[str, Any]:
    """获取分层记忆上下文。

    Returns:
        {
            "pinned": List[MemoryEntry],       # 永久记忆
            "long_term": List[MemoryEntry],     # 长期事实/回忆
            "short_term": List[MemoryEntry],    # 近期记忆标签
            "working": str,                     # 工作记忆文本
            "prompt_parts": List[str],          # 已排序的可直接注入的 prompt 片段
        }
    """
    result: Dict[str, Any] = {
        "pinned": [],
        "long_term": [],
        "short_term": [],
        "working": "",
        "prompt_parts": [],
    }

    # ---- Working Memory ----
    if scratchpad:
        result["working"] = scratchpad
        cfg = TIER_CONFIG["working"]
        result["prompt_parts"].append(
            f"{cfg['inject_prefix']}\n{scratchpad}"
        )

    # ---- Short-Term Memory: 从 memory_tags 获取 ----
    try:
        from .db_tags import get_relevant_memory_tags
        rows = await get_relevant_memory_tags(user_id, limit=6)
        entries = []
        for r in rows:
            entry = MemoryEntry(
                content=r["content"],
                confidence=r.get("confidence", 0.5),
                created_at=r.get("created_at", time.time()),
                tier="short_term",
            )
            entries.append(entry)
        result["short_term"] = entries

        if entries:
            cfg = TIER_CONFIG["short_term"]
            tags_text = "\n".join(f"- {e.content}" for e in entries[:cfg["max_items"]])
            result["prompt_parts"].append(f"{cfg['inject_prefix']}\n{tags_text}")
    except Exception as e:
        logger.debug(f"[记忆层级] 短期记忆获取失败: {e}")

    # ---- Long-Term Memory: 共享回忆 + 私人梗 + 重要日期 ----
    long_term_items = []
    try:
        from .db_memories_deep import get_shared_memories
        from .db_memories_deep import get_private_memes
        from .db_memories_deep import get_important_dates

        shared = await get_shared_memories(user_id, limit=2)
        for s in shared:
            long_term_items.append(
                MemoryEntry(content=s, confidence=0.7, tier="long_term")
            )

        memes = await get_private_memes(user_id, limit=2)
        for m in memes:
            long_term_items.append(
                MemoryEntry(content=m, confidence=0.6, tier="long_term")
            )

        dates = await get_important_dates(user_id, limit=2)
        for d in dates:
            long_term_items.append(
                MemoryEntry(content=d, confidence=0.8, tier="long_term")
            )
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[记忆层级] 长期记忆获取失败: {e}")

    result["long_term"] = long_term_items
    if long_term_items:
        cfg = TIER_CONFIG["long_term"]
        items_text = "\n".join(f"- {e.content}" for e in long_term_items[:cfg["max_items"]])
        result["prompt_parts"].append(f"{cfg['inject_prefix']}\n{items_text}")

    # ---- Pinned Memory: 待实现（需要用户标记机制） ----
    # 预留接口：当用户明确说"记住xxx"时，存入 pinned_memories 表

    logger.debug(
        f"[记忆层级] {user_id[:8]}: "
        f"pinned={len(result['pinned'])} long={len(result['long_term'])} "
        f"short={len(result['short_term'])} working={len(result['working'])}"
    )

    return result


def format_tiered_prompt_injection(tiered: Dict[str, Any]) -> str:
    """将分层记忆格式化为 prompt 注入文本。

    按优先级顺序排列：working → short_term → long_term → pinned
    """
    parts = tiered.get("prompt_parts", [])
    if not parts:
        return ""
    return "\n".join(parts)


# ============================================================
# Pinned Memory 管理（预留）
# ============================================================

async def pin_memory(user_id: str, content: str):
    """标记一条记忆为永久（用户说"记住这个"）。

    注意：此功能需要 pinned_memories 表，目前使用 memory_tags 的 tier 字段。
    """
    try:
        from .db_tags import ensure_tag
        await ensure_tag(user_id, "pinned", content, confidence=0.9)
        logger.info(f"[记忆层级] Pinned: {user_id[:8]} -> {content[:30]}")
    except Exception as e:
        logger.warning(f"[记忆层级] pin_memory 失败: {e}")


async def get_pinned_memories(user_id: str) -> List[str]:
    """获取用户的固定记忆。"""
    try:
        from .db_tags import get_relevant_memory_tags
        rows = await get_relevant_memory_tags(user_id, limit=5)
        return [r["content"] for r in rows if r.get("tag_type") == "pinned"]
    except Exception:
        return []
