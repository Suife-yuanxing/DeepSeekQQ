"""数据库连接池管理。全局单连接复用，aiosqlite 线程安全。"""
import asyncio
import os
import stat
from typing import Optional

import aiosqlite
from nonebot import logger

from .config import DB_PATH

_db: Optional[aiosqlite.Connection] = None
_db_lock = asyncio.Lock()


async def get_db() -> aiosqlite.Connection:
    """获取全局数据库连接（延迟初始化，自动复用，锁保护，健康检查）。"""
    global _db
    if _db is not None:
        # 健康检查：执行简单查询验证连接可用
        try:
            await _db.execute("SELECT 1")
            return _db
        except Exception:
            logger.warning("[数据库] 连接已失效，重新创建")
            try:
                await _db.close()
            except Exception:
                pass
            _db = None
    async with _db_lock:
        if _db is not None:
            return _db
        _db = await aiosqlite.connect(DB_PATH, timeout=10.0)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA synchronous=NORMAL")
        await _db.execute("PRAGMA busy_timeout=5000")
        await _db.execute("PRAGMA foreign_keys=ON")
        # M-15: 限制数据库文件权限为 600（仅 owner 可读写）
        try:
            if os.name != "nt":  # Windows 不支持 os.chmod 权限位
                os.chmod(DB_PATH, stat.S_IRUSR | stat.S_IWUSR)
        except Exception as e:
            logger.debug(f"[数据库] 文件权限设置失败（非关键）: {e}")
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
