"""数据库连接池管理。全局单连接复用，aiosqlite 线程安全。"""
import aiosqlite
from typing import Optional
from nonebot import logger

from .config import DB_PATH

_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    """获取全局数据库连接（延迟初始化，自动复用）。"""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA synchronous=NORMAL")
    return _db


async def checkpoint_db():
    """手动触发 WAL checkpoint，防止日志无限膨胀。"""
    global _db
    if _db:
        try:
            await _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.error(f"[数据库] checkpoint 失败: {e}")


async def close_db():
    """关闭全局数据库连接。先 checkpoint 再关闭，确保 WAL 落盘。"""
    global _db
    if _db:
        await checkpoint_db()
        await _db.close()
        _db = None
