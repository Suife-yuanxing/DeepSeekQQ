"""MCP 客户端模块 — 为 Bot 提供统一的工具调用接口。

支持的工具后端：
- glm-mcp: 智谱AI API（对话/视觉/OCR/翻译/嵌入）
- lighthouse: 腾讯云轻量服务器管理
- anysearch: 联网搜索

设计原则：
- 所有工具通过统一的 call_tool() 接口调用
- 工具发现：get_available_tools() 返回可用工具列表
- Prompt 注入：build_tools_prompt() 生成供 LLM 使用的工具描述
"""
import json
import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

logger = logging.getLogger("deepseek.mcp")

# ============================================================
# 工具注册表
# ============================================================

# 每个工具定义：{name, description, parameters, handler}
_registered_tools: List[Dict[str, Any]] = []


def register_tool(
    name: str,
    description: str,
    parameters: Dict[str, Any],
    handler,
    enabled: bool = True,
):
    """注册一个 MCP 工具。"""
    _registered_tools.append({
        "name": name,
        "description": description,
        "parameters": parameters,
        "handler": handler,
        "enabled": enabled,
    })
    logger.info(f"[MCP] 注册工具: {name}")


def get_available_tools() -> List[Dict[str, Any]]:
    """获取所有已启用工具的元数据列表。"""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["parameters"],
        }
        for t in _registered_tools
        if t["enabled"]
    ]


def build_tools_prompt() -> str:
    """生成供 LLM system prompt 使用的工具描述文本。"""
    tools = get_available_tools()
    if not tools:
        return ""

    lines = ["\n【可用工具】你可以使用以下工具来回答用户问题："]
    for t in tools:
        params_desc = ", ".join(
            f"{k}({v.get('description', '')})"
            for k, v in t["parameters"].get("properties", {}).items()
        )
        lines.append(f"- {t['name']}: {t['description']}")
        if params_desc:
            lines.append(f"  参数: {params_desc}")
    lines.append(
        "使用格式：在回复中需要调用工具时，用 [tool:工具名] {\"参数\": \"值\"} [/tool] 包裹。\n"
        "例如：[tool:search] {\"query\": \"今天天气\"} [/tool]"
    )
    return "\n".join(lines)


async def call_tool(
    name: str,
    arguments: Dict[str, Any],
    user_id: str = "",
) -> Optional[str]:
    """调用指定的MCP工具。

    Args:
        name: 工具名称
        arguments: 工具参数
        user_id: 调用者 QQ 号（自动注入到手机工具的参数中）

    Returns:
        工具执行结果字符串，或 None（工具不存在/执行失败）
    """
    # 手机工具自动注入 user_id（LLM 无需感知权限参数）
    if name.startswith("phone_") and user_id:
        arguments = {**arguments, "user_id": user_id}

    for t in _registered_tools:
        if t["name"] == name and t["enabled"]:
            try:
                result = await t["handler"](**arguments)
                return result
            except Exception as e:
                logger.error(f"[MCP] 工具 '{name}' 执行失败: {e}")
                return None
    logger.warning(f"[MCP] 工具 '{name}' 未找到或未启用")
    return None


# ============================================================
# 工具实现
# ============================================================


# ── glm_chat: 智谱AI对话 ──

async def _glm_chat_handler(
    prompt: str,
    system_prompt: str = "",
    max_tokens: int = 500,
) -> Optional[str]:
    """调用智谱AI GLM 模型进行对话。"""
    from .config import GLM_API_KEY
    from .config import GLM_MODEL

    if not GLM_API_KEY:
        return None

    import aiohttp
    from .api import get_http_session

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": GLM_MODEL,
        "messages": messages,
        "max_tokens": min(max_tokens, 1000),
    }

    try:
        session = await get_http_session()
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return content or None
    except Exception as e:
        logger.warning(f"[GLM Chat] 失败: {e}")
        return None


# ── glm_vision_tool: 智谱AI视觉 ──

async def _glm_vision_handler(image_url: str, prompt: str = "描述这张图片") -> Optional[str]:
    """调用智谱AI视觉模型分析图片。"""
    from .config import GLM_API_KEY
    from .config import GLM_VISION_MODEL

    if not GLM_API_KEY:
        return None

    import base64
    import aiohttp

    # 下载图片并编码
    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.get(image_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            img_b64 = base64.b64encode(await resp.read()).decode("utf-8")
    except Exception:
        return None

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GLM_VISION_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 500,
    }

    try:
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "").strip() or None
    except Exception as e:
        logger.warning(f"[GLM Vision] 失败: {e}")
        return None


# ── search: 联网搜索（Tavily） ──

async def _search_handler(query: str, limit: int = 3) -> Optional[str]:
    """使用 Tavily 进行联网搜索。"""
    from .search import search
    from .search import extract_search_query

    try:
        result = await search(query)
        if result and result.results:
            items = []
            for r in result.results[:limit]:
                title = r.get("title", "")
                url = r.get("url", "")
                snippet = r.get("snippet", "")[:200]
                items.append(f"- {title}\n  {snippet}\n  {url}")
            return "\n\n".join(items) if items else None
        return None
    except Exception as e:
        logger.warning(f"[Search] 失败: {e}")
        return None


# ── server_status: 服务器状态查询 ──

async def _server_status_handler() -> Optional[str]:
    """查询腾讯云轻量服务器状态。"""
    try:
        from .config import MY_QQ  # 服务器信息可以从配置获取
        # 使用 lighthouse MCP 工具查询实例
        # 这里返回一个占位 - 实际调用需要通过 MCP bridge
        return "服务器状态查询功能需要 lighthouse MCP 连接。请确保 MCP server 已配置。"
    except Exception as e:
        return f"查询失败: {e}"


# ============================================================
# 手机工具 — PhoneBridge 封装
# ============================================================

_PHONE_USER_ID: Optional[str] = None  # 启动时设置


def set_phone_user(user_id: str):
    """设置允许使用手机工具的用户 ID（仅主人）。"""
    global _PHONE_USER_ID
    _PHONE_USER_ID = user_id


def check_phone_permission(user_id: str) -> bool:
    """检查是否有手机控制权限。"""
    from .config import PHONE_WS_KEY
    if not PHONE_WS_KEY:
        return False
    if _PHONE_USER_ID and user_id != _PHONE_USER_ID:
        return False
    return True


async def ensure_phone_bridge():
    """检查手机是否在线（MobileRun Portal 直连模式，无需 controller）。"""
    from .phone_bridge import get_relay
    relay = get_relay()
    if not relay.phone_online:
        return None
    return relay


async def _phone_screenshot_handler(user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接，请检查 MobileRun Portal 是否在线"
    img_b64 = await bridge.screenshot()
    if img_b64:
        return f"[CQ:image,file=base64://{img_b64}]"
    return "截图失败"


async def _phone_ui_tree_handler(user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    return await bridge.get_screen_text()


async def _phone_tap_handler(x: int, y: int, user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.tap(x, y)
    return "✅ 已点击" if resp.get("success") else f"点击失败: {resp.get('error')}"


async def _phone_tap_text_handler(text: str, user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.tap_text(text)
    return f"✅ 已点击「{text}」" if resp.get("success") else f"点击失败: {resp.get('error')}"


async def _phone_swipe_handler(
    x1: int, y1: int, x2: int, y2: int, user_id: str = "",
) -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.swipe(x1, y1, x2, y2)
    return "✅ 已滑动" if resp.get("success") else f"滑动失败: {resp.get('error')}"


async def _phone_scroll_up_handler(user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.scroll_up()
    return "✅ 已上滑" if resp.get("success") else f"失败: {resp.get('error')}"


async def _phone_scroll_down_handler(user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.scroll_down()
    return "✅ 已下滑" if resp.get("success") else f"失败: {resp.get('error')}"


async def _phone_type_handler(text: str, user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.type_text(text)
    return f"✅ 已输入「{text[:30]}」" if resp.get("success") else f"输入失败: {resp.get('error')}"


async def _phone_open_app_handler(app_name: str, user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.open_app(app_name)
    return f"✅ 已打开{app_name}" if resp.get("success") else f"打开失败: {resp.get('error')}"


async def _phone_back_handler(user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.back()
    return "✅ 已返回" if resp.get("success") else "失败"


async def _phone_home_handler(user_id: str = "") -> Optional[str]:
    if not check_phone_permission(user_id):
        return None
    bridge = await ensure_phone_bridge()
    if not bridge:
        return "手机未连接"
    resp = await bridge.home()
    return "✅ 已回到桌面" if resp.get("success") else "失败"


# ============================================================
# 注册工具
# ============================================================

def _register_default_tools():
    """注册默认工具集。"""
    # 只注册一次
    if _registered_tools:
        return

    # glm_chat — 当 DeepSeek 不可用或需要特定能力时使用
    register_tool(
        name="glm_chat",
        description="调用智谱AI GLM模型进行文本对话，适合需要中文理解、翻译、总结等任务",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "要发送给GLM的提示词"},
                "system_prompt": {"type": "string", "description": "系统提示词（可选）"},
            },
            "required": ["prompt"],
        },
        handler=_glm_chat_handler,
    )

    # glm_vision — 增强图片识别
    register_tool(
        name="glm_vision",
        description="分析图片内容，支持中英文混合场景识别。适合需要理解图片内容的场景",
        parameters={
            "type": "object",
            "properties": {
                "image_url": {"type": "string", "description": "图片URL"},
                "prompt": {"type": "string", "description": "图片分析提示词（可选）"},
            },
            "required": ["image_url"],
        },
        handler=_glm_vision_handler,
    )

    # search — 联网搜索
    register_tool(
        name="web_search",
        description="联网搜索最新信息，适合查询实时数据、新闻、百科等",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "limit": {"type": "integer", "description": "返回结果数量（默认3）"},
            },
            "required": ["query"],
        },
        handler=_search_handler,
    )

    # server_status — 服务器运维
    register_tool(
        name="server_status",
        description="查询腾讯云轻量服务器状态、CPU、内存等信息",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_server_status_handler,
    )

    # ── 手机控制工具（仅主人可用）──

    register_tool(
        name="phone_screenshot",
        description="截取手机屏幕，返回截图。用于了解手机当前显示的内容",
        parameters={
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        handler=_phone_screenshot_handler,
    )

    register_tool(
        name="phone_ui_tree",
        description="读取手机屏幕上的所有文字内容。用于了解当前界面有什么按钮、文本、选项",
        parameters={
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        handler=_phone_ui_tree_handler,
    )

    register_tool(
        name="phone_tap",
        description="点击手机屏幕指定坐标。需要先通过 phone_ui_tree 或 phone_screenshot 获取目标位置",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X坐标（横向位置）"},
                "y": {"type": "integer", "description": "Y坐标（纵向位置）"},
            },
            "required": ["x", "y"],
        },
        handler=_phone_tap_handler,
    )

    register_tool(
        name="phone_tap_text",
        description="在手机屏幕上查找指定文字并点击。如点击「微信」「确定」「发送」等按钮",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要点击的文字/按钮名称"},
            },
            "required": ["text"],
        },
        handler=_phone_tap_text_handler,
    )

    register_tool(
        name="phone_swipe",
        description="在手机屏幕上滑动，从(x1,y1)滑到(x2,y2)",
        parameters={
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "起始X坐标"},
                "y1": {"type": "integer", "description": "起始Y坐标"},
                "x2": {"type": "integer", "description": "终点X坐标"},
                "y2": {"type": "integer", "description": "终点Y坐标"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        handler=_phone_swipe_handler,
    )

    register_tool(
        name="phone_scroll_up",
        description="在手机屏幕上向上滑动（浏览下方内容）",
        parameters={
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        handler=_phone_scroll_up_handler,
    )

    register_tool(
        name="phone_scroll_down",
        description="在手机屏幕上向下滑动（浏览上方内容）",
        parameters={
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        handler=_phone_scroll_down_handler,
    )

    register_tool(
        name="phone_type",
        description="在手机当前输入框中输入文字",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要输入的文字内容"},
            },
            "required": ["text"],
        },
        handler=_phone_type_handler,
    )

    register_tool(
        name="phone_open_app",
        description="打开手机上的指定应用，如微信、抖音、B站、淘宝、设置等",
        parameters={
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "应用名称，如: 微信, QQ, 抖音, 淘宝, B站, 小红书, 支付宝, 设置"},
            },
            "required": ["app_name"],
        },
        handler=_phone_open_app_handler,
    )

    register_tool(
        name="phone_back",
        description="按手机返回键",
        parameters={
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        handler=_phone_back_handler,
    )

    register_tool(
        name="phone_home",
        description="回到手机桌面",
        parameters={
            "type": "object",
            "properties": {
            },
            "required": [],
        },
        handler=_phone_home_handler,
    )

    # ==================== 时钟控制 ====================

    async def _phone_set_alarm_handler(hour: int, minute: int, label: str = "", user_id: str = "") -> Optional[str]:
        if not check_phone_permission(user_id):
            return None
        bridge = await ensure_phone_bridge()
        if not bridge:
            return "手机未连接"
        resp = await bridge.set_alarm(hour, minute, label)
        return resp.get("message", "闹钟设置请求已发送") if resp.get("success") else f"设置失败: {resp.get('error', '')}"

    async def _phone_set_timer_handler(minutes: int, label: str = "", user_id: str = "") -> Optional[str]:
        if not check_phone_permission(user_id):
            return None
        bridge = await ensure_phone_bridge()
        if not bridge:
            return "手机未连接"
        resp = await bridge.set_timer(minutes, label)
        return resp.get("message", "计时器设置请求已发送") if resp.get("success") else f"设置失败: {resp.get('error', '')}"

    register_tool(
        name="phone_set_alarm",
        description="在手机上设置闹钟（时、分、标签）",
        parameters={
            "type": "object",
            "properties": {
                "hour": {"type": "integer", "description": "小时（0-23）"},
                "minute": {"type": "integer", "description": "分钟（0-59）"},
                "label": {"type": "string", "description": "闹钟标签（可选）"},
            },
            "required": ["hour", "minute"],
        },
        handler=_phone_set_alarm_handler,
    )

    register_tool(
        name="phone_set_timer",
        description="在手机上设置倒计时（分钟数）",
        parameters={
            "type": "object",
            "properties": {
                "minutes": {"type": "integer", "description": "倒计时分钟数"},
                "label": {"type": "string", "description": "计时器标签（可选）"},
            },
            "required": ["minutes"],
        },
        handler=_phone_set_timer_handler,
    )

    logger.info(f"[MCP] 已注册 {len(_registered_tools)} 个工具")


# 自动注册
_register_default_tools()


# ============================================================
# Pipeline 集成辅助
# ============================================================

def parse_tool_call(reply_text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 回复中解析工具调用。

    格式: [tool:工具名] {"参数": "值"} [/tool]
    兼容: [tool:工具名] {{"参数": "值"}} [/tool] (LLM 偶尔输出双花括号)

    Returns:
        {"tool": "工具名", "args": {...}} 或 None
    """
    import re
    # 兼容单花括号 { } 和双花括号 {{ }} 两种格式
    match = re.search(
        r'\[tool:(\w+)\]\s*(\{+.*?\}+)\s*\[/tool\]',
        reply_text, re.DOTALL
    )
    if match:
        tool_name = match.group(1)
        json_str = match.group(2)
        # 如果是双花括号 {{...}}，剥掉一层
        if json_str.startswith("{{") and json_str.endswith("}}"):
            json_str = json_str[1:-1]
        try:
            args = json.loads(json_str)
            return {"tool": tool_name, "args": args}
        except json.JSONDecodeError:
            logger.warning(f"[MCP] 工具调用参数JSON解析失败: {match.group(2)[:100]}")
            return None
    return None


def remove_tool_call(reply_text: str) -> str:
    """从回复文本中移除工具调用标记（兼容单/双花括号）。"""
    import re
    return re.sub(r'\[tool:\w+\]\s*\{+.*?\}+\s*\[/tool\]', '', reply_text, flags=re.DOTALL).strip()
