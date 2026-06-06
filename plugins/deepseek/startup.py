"""插件启动/关闭与后台任务模块。

ECC 风格改造：
- LoopManager 统一管理所有后台任务
- 数据库迁移机制
- 会话状态持久化（启停时保存/恢复）
"""
import os
import asyncio
import shutil
from pathlib import Path

from nonebot import get_driver, logger
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .config import VOICE_DIR, SERVER_HOST, SERVER_PORT, REMINDER_CHECK_INTERVAL, VOICE_TOKEN
from .database import (
    init_db, close_db, checkpoint_db, decay_memory_tags, prune_memory_tags,
    save_session_state, get_active_sessions
)
from .migrations import run_migrations
from .loop_manager import loop_manager
from .api import close_http_session
from .proactive import register_proactive_jobs, shutdown_proactive
from .share_parser import global_cleanup_shares
from .reminder import check_and_fire_reminders
from .sticker_search import cleanup_old_downloads
from .plugin_manager import load_plugins_from_dir, startup_all_plugins, shutdown_all_plugins
from .image_gen import cleanup_old_images


driver = get_driver()


@driver.on_startup
async def on_start():
    os.makedirs(VOICE_DIR, exist_ok=True)
    await init_db()

    # 执行数据库迁移
    from .database import get_db
    db = await get_db()
    await run_migrations(db)

    # 功能⑥：加载插件
    load_plugins_from_dir()
    await startup_all_plugins()

    # 挂载语音文件服务（安全路径检查 + token 鉴权）
    try:
        app = driver.server_app
        if app and isinstance(app, FastAPI):
            @app.get("/voice/{filename}")
            async def serve_voice(filename: str, token: str = ""):
                if VOICE_TOKEN and token != VOICE_TOKEN:
                    return {"error": "unauthorized"}
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
    from .config import RANDOM_REPLY_CHANCE, VOICE_ENABLED_PRIVATE, VOICE_ENABLED_GROUP
    logger.info("✅ DeepSeek猫娘插件已启动~ 喵！")
    logger.info(f"ffmpeg 检测: {'已安装 ✅' if has_ff else '未安装 ❌ 语音可能无法发送'}")
    logger.info(f"语音开关: 私聊={VOICE_ENABLED_PRIVATE}, 群聊={VOICE_ENABLED_GROUP}")
    logger.info(f"群聊随机回复概率: {RANDOM_REPLY_CHANCE*100:.1f}%")

    # === 注册所有后台任务到 LoopManager ===

    async def _register_proactive():
        import nonebot
        await asyncio.sleep(15)
        bots = nonebot.get_bots()
        if bots:
            bot = list(bots.values())[0]
            await register_proactive_jobs(bot)
        else:
            logger.warning("[主动消息] Bot未连接，跳过")

    async def _share_cleanup():
        await asyncio.sleep(60)
        await global_cleanup_shares()

    async def _sticker_cleanup():
        await asyncio.sleep(300)
        await cleanup_old_downloads()

    async def _db_checkpoint():
        await checkpoint_db()
        logger.info("[数据库] WAL checkpoint 完成")

    async def _reminder_check():
        import nonebot
        await asyncio.sleep(20)
        bots = nonebot.get_bots()
        if bots:
            bot = list(bots.values())[0]
            await check_and_fire_reminders(bot)

    async def _memory_maintenance():
        await asyncio.sleep(300)
        # 分层衰减：短期记忆衰减快，长期记忆衰减慢
        await decay_memory_tags(decay_rate=0.03, tier="short_term")
        await decay_memory_tags(decay_rate=0.005, tier="long_term")
        # 分层清理
        pruned_short = await prune_memory_tags(min_confidence=0.10, tier="short_term")
        pruned_long = await prune_memory_tags(min_confidence=0.05, tier="long_term")
        pruned = (pruned_short or 0) + (pruned_long or 0)
        if pruned > 0:
            logger.info(f"[记忆] 每日维护：清理了 {pruned} 条低置信度标签 (短期={pruned_short or 0}, 长期={pruned_long or 0})")

    async def _image_cleanup():
        await asyncio.sleep(600)
        await cleanup_old_images(max_age_hours=24)

    # 注册任务
    loop_manager.register("主动消息注册", _register_proactive, 86400)
    loop_manager.register("分享缓存清理", _share_cleanup, 3600)
    loop_manager.register("表情包缓存清理", _sticker_cleanup, 86400)
    loop_manager.register("WAL checkpoint", _db_checkpoint, 7200)
    loop_manager.register("提醒检查", _reminder_check, REMINDER_CHECK_INTERVAL)
    async def _affection_decay():
        from .database import decay_affection
        await decay_affection(inactive_days=7, decay_points=-1.0)

    loop_manager.register("记忆维护", _memory_maintenance, 86400)
    loop_manager.register("好感度衰减", _affection_decay, 86400)
    loop_manager.register("图片缓存清理", _image_cleanup, 3600)

    # 启动所有任务
    await loop_manager.start_all()

    # === 启动 ScreenMCP Worker (手机控制) ===
    try:
        from .config import PHONE_CONTROL_ENABLED, SCREENMCP_API_KEY
        if PHONE_CONTROL_ENABLED and SCREENMCP_API_KEY:
            from .screenmcp_worker import start_worker
            await start_worker(SCREENMCP_API_KEY, 8765)
            logger.info(f"[手机] ScreenMCP Worker 已启动，端口 8765")
        else:
            logger.info("[手机] 手机控制未启用或未配置 API Key")
    except Exception as e:
        logger.error(f"[手机] ScreenMCP Worker 启动失败: {e}")

    # 注册状态端点
    try:
        app = driver.server_app
        if app and isinstance(app, FastAPI):
            @app.get("/loop/status")
            async def loop_status():
                return loop_manager.get_status()

            @app.get("/plugins/status")
            async def plugin_status():
                from .plugin_manager import list_plugins
                return {"plugins": list_plugins()}
    except Exception:
        pass


@driver.on_shutdown
async def _on_shutdown():
    # 保存所有活跃会话的状态（记忆持久化）
    try:
        active = await get_active_sessions(hours=24)
        for sid in active:
            await save_session_state(sid, context_summary="[Bot 关闭前保存]")
        if active:
            logger.info(f"[记忆] 已保存 {len(active)} 个活跃会话状态")
    except Exception as e:
        logger.warning(f"[记忆] 会话状态保存失败: {e}")

    await shutdown_all_plugins()
    await shutdown_proactive()
    await close_http_session()
    await close_db()
    logger.info("✅ 插件资源已释放")
