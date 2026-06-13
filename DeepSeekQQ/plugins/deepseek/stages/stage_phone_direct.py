"""Stage: 手机命令直接处理 — LLM之前拦截明确的手机操作指令，避免幻觉。"""
import re
from typing import Optional

from nonebot import logger

from ..pipeline import ChatContext
from ..pipeline import stage


@stage("phone_direct")
async def _stage_phone_direct(ctx: ChatContext) -> Optional[str]:
    """手机命令直接处理：在 LLM 之前拦截明确的手机操作指令。

    避免 LLM 编造屏幕内容（幻觉），直接调用 MobileRun Portal 工具。
    复杂指令（如"看看微信谁给我发了消息"）仍走 LLM + 工具调用。
    """
    from ..mcp_client import check_phone_permission, ensure_phone_bridge

    # 权限和在线检查
    if not check_phone_permission(ctx.user_id):
        logger.info(f"[phone_direct] 权限不足 user={ctx.user_id}")
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        logger.info("[phone_direct] 手机不在线，跳过")
        return None

    try:
        msg = ctx.raw_msg.strip()

        # ── 截图 / 截屏 ──
        # 使用包含匹配（不用 ^），适配中文口语多变语序
        if re.search(r'(截[图屏]|截个图|截一下|屏幕截图|看看.?屏幕|.*截图.*|给.*截图|把.*截图|.*截.*图.*)', msg):
            logger.info(f"[phone_direct] 截图命令: {msg}")
            img_b64 = await bridge.screenshot()
            if img_b64:
                ctx.reply_text = f"[CQ:image,file=base64://{img_b64}]\n喏，这是当前手机屏幕~"
            else:
                ctx.reply_text = "截图失败了，检查一下手机连接？"
            ctx.skip_llm = True
            return None

        # ── 打开应用 ──
        m = re.search(r'(?:打开|启动|进入)(?:\S{0,6})(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)', msg)
        if m:
            app = m.group(1)
            logger.info(f"[phone_direct] 打开应用: {app}")
            resp = await bridge.open_app(app)
            if resp.get("success"):
                ctx.reply_text = f"✅ 已打开{app}~"
            else:
                ctx.reply_text = f"打开{app}失败: {resp.get('error', '未知错误')}"
            ctx.skip_llm = True
            return None

        # ── 返回键 ──
        if re.search(r'(返回|后退|\bback\b|退回去|按.*返回|按.*\bback\b|退出(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)?|关闭(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)?)', msg, re.IGNORECASE):
            logger.info(f"[phone_direct] 返回: {msg}")
            resp = await bridge.back()
            ctx.reply_text = "✅ 已返回" if resp.get("success") else "返回失败"
            ctx.skip_llm = True
            return None

        # ── 回到桌面 ──
        if re.search(r'((回|返回|到).{0,3}(桌面|主屏幕|主页)|主屏幕|主页|\bhome\b)', msg, re.IGNORECASE):
            logger.info(f"[phone_direct] 回桌面: {msg}")
            resp = await bridge.home()
            ctx.reply_text = "✅ 已回到桌面" if resp.get("success") else "返回桌面失败"
            ctx.skip_llm = True
            return None

        # ── 滑动操作 ──
        if re.search(r'(往上滑|上滑|往上翻|向上滑|向上滚动|(帮|给).*上.*(滑|翻|滚))', msg):
            logger.info(f"[phone_direct] 上滑: {msg}")
            resp = await bridge.scroll_up()
            ctx.reply_text = "✅ 已上滑" if resp.get("success") else "滑动失败"
            ctx.skip_llm = True
            return None
        if re.search(r'(往下滑|下滑|往下翻|向下滑|向下滚动|(帮|给).*下.*(滑|翻|滚))', msg):
            logger.info(f"[phone_direct] 下滑: {msg}")
            resp = await bridge.scroll_down()
            ctx.reply_text = "✅ 已下滑" if resp.get("success") else "滑动失败"
            ctx.skip_llm = True
            return None

        # ── 输入文字 ──
        m = re.search(r'(?:输入(?!法|框|模式|入)|打字|键入|帮我打|帮我写)\s*[：:]*\s*(.{1,200})', msg)
        if m:
            text = m.group(1).strip()
            logger.info(f"[phone_direct] 输入: {text[:30]}")
            resp = await bridge.type_text(text)
            if resp.get("success"):
                ctx.reply_text = f"✅ 已输入「{text[:30]}」"
            else:
                ctx.reply_text = f"输入失败: {resp.get('error', '未知错误')}"
            ctx.skip_llm = True
            return None

        # ── 屏幕文字识别 ──
        if re.search(r'(屏幕.*有什么|屏幕.*显示|识别屏幕|屏幕.*字|看看.*屏幕|屏幕.*内容|看.*屏幕.*有)', msg):
            logger.info(f"[phone_direct] 屏幕文字: {msg}")
            text = await bridge.get_screen_text()
            if text:
                ctx.reply_text = f"📱 屏幕上的文字：\n{text}"
            else:
                ctx.reply_text = "读取屏幕失败"
            ctx.skip_llm = True
            return None

    except Exception as e:
        logger.error(f"[phone_direct] 手机操作异常，回退到 LLM 处理: {e}")
        return None

    return None
