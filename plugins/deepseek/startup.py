"""插件启动/关闭与后台任务模块。"""
import os
import asyncio
import shutil
from pathlib import Path

from nonebot import get_driver, logger
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .config import VOICE_DIR, SERVER_HOST, SERVER_PORT, REMINDER_CHECK_INTERVAL
from .database import init_db, close_db, checkpoint_db
from .api import close_http_session
from .proactive import register_proactive_jobs, shutdown_proactive
from .share_parser import global_cleanup_shares
from .reminder import check_and_fire_reminders
from .hot_topics import check_and_push_topics
from .sticker_search import cleanup_old_downloads


driver = get_driver()


@driver.on_startup
async def on_start():
    os.makedirs(VOICE_DIR, exist_ok=True)
    await init_db()

    # 挂载语音文件服务（安全路径检查）
    try:
        app = driver.server_app
        if app and isinstance(app, FastAPI):
            @app.get("/voice/{filename}")
            async def serve_voice(filename: str):
                voice_path = Path(VOICE_DIR).resolve()
                try:
                    file_path = (voice_path / filename).resolve()
                    if not file_path.is_relative_to(voice_path):
                        return {"error": "invalid path"}
                    if file_path.exists():
                        return FileResponse(str(file_path), media_type="audio/mpeg")
                    return {"error": "not found"}
                except Exception:
                    return {"error": "invalid path"}
            logger.info(f"语音文件服务已挂载: http://{SERVER_HOST}:{SERVER_PORT}/voice/")
    except Exception as e:
        logger.warning(f"语音文件服务挂载失败: {e}")

    has_ff = shutil.which("ffmpeg") is not None
    logger.info("✅ DeepSeek猫娘插件已启动~ 喵！")
    logger.info(f"ffmpeg 检测: {'已安装 ✅' if has_ff else '未安装 ❌ 语音可能无法发送'}")
    logger.info(f"语音开关: 私聊=True, 群聊=True")
    logger.info(f"群聊随机回复概率: {5.0}%")

    async def _wait_and_register():
        await asyncio.sleep(15)
        try:
            import nonebot
            bots = nonebot.get_bots()
            if bots:
                bot = list(bots.values())[0]
                await register_proactive_jobs(bot)
            else:
                logger.warning("[主动消息] Bot未连接，跳过")
        except Exception as e:
            logger.error(f"[主动消息] 注册失败: {e}")

    asyncio.create_task(_protected_task("主动消息注册", _wait_and_register))

    async def _periodic_share_cleanup():
        while True:
            try:
                await asyncio.sleep(3600)
                await global_cleanup_shares()
            except Exception as e:
                logger.error(f"[清理任务] 分享缓存清理异常: {e}")

    asyncio.create_task(_protected_task("分享缓存清理", _periodic_share_cleanup))

    # 表情包下载缓存清理（每天清理一次）
    async def _periodic_sticker_cleanup():
        while True:
            try:
                await asyncio.sleep(86400)
                await cleanup_old_downloads()
            except Exception as e:
                logger.error(f"[清理任务] 表情包缓存清理异常: {e}")

    asyncio.create_task(_protected_task("表情包缓存清理", _periodic_sticker_cleanup))

    async def _periodic_checkpoint():
        while True:
            try:
                await asyncio.sleep(7200)
                await checkpoint_db()
                logger.info("[数据库] WAL checkpoint 完成")
            except Exception as e:
                logger.error(f"[数据库] checkpoint 异常: {e}")

    asyncio.create_task(_protected_task("WAL checkpoint", _periodic_checkpoint))

    # Phase 4: 提醒检查定时任务
    async def _periodic_reminder_check():
        import nonebot
        await asyncio.sleep(20)  # 等待 bot 连接
        while True:
            try:
                bots = nonebot.get_bots()
                if bots:
                    bot = list(bots.values())[0]
                    await check_and_fire_reminders(bot)
                await asyncio.sleep(REMINDER_CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"[提醒] 检查异常: {e}")
                await asyncio.sleep(REMINDER_CHECK_INTERVAL)

    asyncio.create_task(_protected_task("提醒检查", _periodic_reminder_check))

    # 热搜话题主动推送（每4小时检查一次）
    async def _periodic_hot_topics():
        import nonebot
        await asyncio.sleep(60)  # 等待 bot 连接 + 冷启动
        while True:
            try:
                bots = nonebot.get_bots()
                if bots:
                    bot = list(bots.values())[0]
                    await check_and_push_topics(bot)
                await asyncio.sleep(14400)  # 4小时
            except Exception as e:
                logger.error(f"[热搜] 检查异常: {e}")
                await asyncio.sleep(14400)

    asyncio.create_task(_protected_task("热搜推送", _periodic_hot_topics))


async def _protected_task(name: str, coro_func):
    """包装后台任务，异常后自动重启，防止静默死亡。"""
    while True:
        try:
            await coro_func()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[{name}] 任务异常，5秒后重启: {e}")
            await asyncio.sleep(5)


@driver.on_shutdown
async def _on_shutdown():
    await shutdown_proactive()
    await close_http_session()
    await close_db()
    logger.info("✅ 插件资源已释放")
