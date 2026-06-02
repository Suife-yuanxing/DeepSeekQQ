"""DeepSeek 猫娘插件入口。
注册消息处理器、启动/关闭钩子、语音文件服务。
"""
import os
import asyncio
import shutil
from pathlib import Path

from nonebot import on_message, get_driver
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .config import VOICE_DIR, SERVER_HOST, SERVER_PORT
from .database import init_db, close_db
from .api import close_http_session
from .proactive import register_proactive_jobs, shutdown_proactive
from .handler import handle_chat
from .share_parser import global_cleanup_shares

chat_handler = on_message(priority=5, block=False)

@chat_handler.handle()
async def _chat_handler(bot: Bot, event: MessageEvent):
    await handle_chat(bot, event)


driver = get_driver()

@driver.on_startup
async def on_start():
    os.makedirs(VOICE_DIR, exist_ok=True)
    await init_db()

    # 挂载语音文件服务（安全路径检查）
    try:
        from fastapi import FastAPI
        from fastapi.responses import FileResponse
        app = driver.server_app
        if app and isinstance(app, FastAPI):
            @app.get("/voice/{filename}")
            async def serve_voice(filename: str):
                voice_path = Path(VOICE_DIR).resolve()
                try:
                    file_path = (voice_path / filename).resolve()
                    if not str(file_path).startswith(str(voice_path)):
                        return {"error": "invalid path"}
                    if file_path.exists():
                        return FileResponse(str(file_path), media_type="audio/mpeg")
                    return {"error": "not found"}
                except Exception:
                    return {"error": "invalid path"}
            print(f"   语音文件服务已挂载: http://{SERVER_HOST}:{SERVER_PORT}/voice/")
    except Exception as e:
        print(f"   语音文件服务挂载失败: {e}")

    has_ff = shutil.which("ffmpeg") is not None
    print(f"✅ DeepSeek猫娘插件已启动~ 喵！")
    print(f"   ffmpeg 检测: {'已安装 ✅' if has_ff else '未安装 ❌ 语音可能无法发送'}")
    print(f"   语音开关: 私聊=True, 群聊=True")
    print(f"   群聊随机回复概率: {5.0}%")

    # 延迟注册主动消息
    async def _wait_and_register():
        await asyncio.sleep(15)
        try:
            import nonebot
            bots = nonebot.get_bots()
            if bots:
                bot = list(bots.values())[0]
                await register_proactive_jobs(bot)
            else:
                print("[主动消息] Bot未连接，跳过")
        except Exception as e:
            print(f"[主动消息] 注册失败: {e}")
    asyncio.create_task(_wait_and_register())

    # 每小时清理一次过期分享缓存
    async def _periodic_share_cleanup():
        while True:
            await asyncio.sleep(3600)
            await global_cleanup_shares()
    asyncio.create_task(_periodic_share_cleanup())


@driver.on_shutdown
async def _on_shutdown():
    await shutdown_proactive()
    await close_http_session()
    await close_db()
    print("✅ 插件资源已释放")
