"""article_cache 表操作 — 文章缓存。"""
from datetime import datetime
from typing import Any
from typing import Dict
from typing import Optional

from .db_core import get_db


async def get_article_cache(url_hash: str) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT title, author, summary, fetched_at FROM article_cache WHERE url_hash = ?",
        (url_hash,)
    ) as cursor:
        row = await cursor.fetchone()
        if row and datetime.now().timestamp() - row["fetched_at"] < 86400:
            return {"title": row["title"] or "无标题", "author": row["author"] or "未知", "summary": row["summary"] or "", "cached": True}
        return None


async def save_article_cache(url_hash: str, url: str, title: str, author: str, summary: str):
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO article_cache
        (url_hash, url, title, author, summary, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (url_hash, url, title, author, summary[:2000], datetime.now().timestamp())
    )
    await db.commit()
