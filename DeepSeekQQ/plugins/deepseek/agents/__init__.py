"""Agent 注册模块 — A3: AgentRouter 作为 Pipeline 前置过滤器。

从 22 个 stage 中提取 3 个能独立短路的为 agent，其余 19 个保持 Pipeline 不变。

Agent 优先级顺序与当前 Pipeline 执行顺序一致：
  security(p=10) → music(p=35) → phone_direct(p=40)

设计原则：
- trigger 做粗筛（同步），execute 做精筛+执行（异步）
- 每个 agent 短路时自己调用 ctx.bot.send() 发送回复
- 不匹配时返回 None，消息流入 Pipeline
"""
import re

from ..agent_base import AgentMeta
from ..agent_base import AgentOutput
from ..agent_base import AgentRouter
from ..pipeline import _SKIP

# 全局 Router 实例
router = AgentRouter()


# ============================================================
# agent_security (priority=10): 安全过滤 — 所有消息必经
# ============================================================

async def _agent_security_execute(ctx, output: AgentOutput):
    """扫描输入中的敏感/注入内容，不安全则拦截并短路。"""
    from nonebot.adapters.onebot.v11 import Message

    from ..handler_helpers import make_reply
    from ..security import get_blocked_reply
    from ..security import scan_input

    is_safe, reason = scan_input(ctx.raw_msg, ctx.user_id)
    if not is_safe:
        reply = get_blocked_reply(reason)
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(reply)))
        output.set("sec_blocked", True)
        return _SKIP
    output.set("sec_blocked", False)
    return None


router.register(AgentMeta(
    name="security",
    priority=10,
    trigger=lambda ctx: bool(ctx.raw_msg),
    execute=_agent_security_execute,
    parallel_ok=False,
))


# ============================================================
# agent_music (priority=35): 音乐点歌/推荐/歌词
#   — 与 pipeline 顺序一致：music(#15) 在 phone_direct(#16) 之前
# ============================================================


def _music_trigger(ctx) -> bool:
    """音乐意图粗筛 — 使用 detect_music_intent 精确判断。"""
    if not ctx.raw_msg:
        return False
    from ..music import detect_music_intent
    intent, _ = detect_music_intent(ctx.raw_msg)
    return intent != "none"


async def _agent_music_execute(ctx, output: AgentOutput):
    """检测音乐意图 → 调 API → 发 XML 卡片/歌词 → 短路。"""
    from ..music import handle_music_stage

    result = await handle_music_stage(ctx)
    if result == "SKIP":
        return _SKIP
    return None


router.register(AgentMeta(
    name="music",
    priority=35,
    trigger=_music_trigger,
    execute=_agent_music_execute,
    parallel_ok=False,
))


# ============================================================
# agent_phone_direct (priority=40): 手机命令直连
#   — 在 LLM 之前拦截明确手机指令，避免幻觉编造屏幕内容
#   — 与原 stage_phone_direct 行为一致（复用相同正则），
#     但 agent 版本直接发送回复并短路，不再依赖下游 stage
# ============================================================

_PHONE_TRIGGER_KW = [
    "截图", "截屏", "屏幕", "打开", "返回", "桌面",
    "上滑", "下滑", "往上", "往下", "输入", "打字",
    "启动", "进入", "后退", "主屏幕",
]


async def _agent_phone_direct_execute(ctx, output: AgentOutput):
    """正则匹配手机命令 → 调 phone_bridge → 直接发送回复 → 短路。"""
    from nonebot import logger

    from ..mcp_client import check_phone_permission
    from ..mcp_client import ensure_phone_bridge

    # 权限和在线检查
    if not check_phone_permission(ctx.user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return None

    msg = ctx.raw_msg.strip()

    try:
        # ── 截图 / 截屏 ──
        if re.search(r'(截[图屏]|截个图|截一下|屏幕截图|看看.?屏幕|给.*截图|把.*截图)', msg):
            logger.info(f"[agent:phone_direct] 截图: {msg[:30]}")
            img_b64 = await bridge.screenshot()
            reply = (
                f"[CQ:image,file=base64://{img_b64}]\n喏，这是当前手机屏幕~"
                if img_b64 else "截图失败了，检查一下手机连接？"
            )
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 打开应用 ──
        m = re.search(
            r'(?:打开|启动|进入)(?:\S{0,6})'
            r'(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)',
            msg,
        )
        if m:
            app = m.group(1)
            logger.info(f"[agent:phone_direct] 打开: {app}")
            resp = await bridge.open_app(app)
            reply = f"✅ 已打开{app}~" if resp.get("success") else f"打开{app}失败: {resp.get('error', '未知错误')}"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 返回键 ──
        if re.search(
            r'(返回|后退|\bback\b|退回去|按.*返回|按.*\bback\b|'
            r'退出(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)?|'
            r'关闭(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)?)',
            msg, re.IGNORECASE,
        ):
            logger.info(f"[agent:phone_direct] 返回: {msg[:30]}")
            resp = await bridge.back()
            reply = "✅ 已返回" if resp.get("success") else "返回失败"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 回到桌面 ──
        if re.search(
            r'((回|返回|到).{0,3}(桌面|主屏幕|主页)|主屏幕|主页|\bhome\b)',
            msg, re.IGNORECASE,
        ):
            logger.info(f"[agent:phone_direct] 回桌面: {msg[:30]}")
            resp = await bridge.home()
            reply = "✅ 已回到桌面" if resp.get("success") else "返回桌面失败"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 上滑 ──
        if re.search(r'(往上滑|上滑|往上翻|向上滑|向上滚动|(帮|给).*上.*(滑|翻|滚))', msg):
            logger.info(f"[agent:phone_direct] 上滑: {msg[:30]}")
            resp = await bridge.scroll_up()
            reply = "✅ 已上滑" if resp.get("success") else "滑动失败"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 下滑 ──
        if re.search(r'(往下滑|下滑|往下翻|向下滑|向下滚动|(帮|给).*下.*(滑|翻|滚))', msg):
            logger.info(f"[agent:phone_direct] 下滑: {msg[:30]}")
            resp = await bridge.scroll_down()
            reply = "✅ 已下滑" if resp.get("success") else "滑动失败"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 输入文字 ──
        m = re.search(r'(?:输入(?!法|框|模式|入)|打字|键入|帮我打|帮我写)\s*[：:]*\s*(.{1,200})', msg)
        if m:
            text = m.group(1).strip()
            logger.info(f"[agent:phone_direct] 输入: {text[:30]}")
            resp = await bridge.type_text(text)
            reply = f"✅ 已输入「{text[:30]}」" if resp.get("success") else f"输入失败: {resp.get('error', '未知错误')}"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

        # ── 屏幕文字识别 ──
        if re.search(
            r'(屏幕.*有什么|屏幕.*显示|识别屏幕|屏幕.*字|看看.*屏幕|屏幕.*内容|看.*屏幕.*有)',
            msg,
        ):
            logger.info(f"[agent:phone_direct] 屏幕文字: {msg[:30]}")
            text = await bridge.get_screen_text()
            reply = f"📱 屏幕上的文字：\n{text}" if text else "读取屏幕失败"
            await ctx.bot.send(ctx.event, reply)
            return _SKIP

    except Exception as e:
        logger.error(f"[agent:phone_direct] 手机操作异常，回退到 Pipeline: {e}")
        return None

    return None


router.register(AgentMeta(
    name="phone_direct",
    priority=40,
    trigger=lambda ctx: bool(ctx.raw_msg and any(kw in ctx.raw_msg for kw in _PHONE_TRIGGER_KW)),
    execute=_agent_phone_direct_execute,
    parallel_ok=False,
))
