"""Stage: MCP 工具执行 — 检测 LLM 回复中的工具调用并执行。"""
from typing import Optional

from nonebot import logger

from ..mcp_client import call_tool as mcp_call_tool
from ..mcp_client import parse_tool_call
from ..mcp_client import remove_tool_call
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("mcp_execute")
async def _stage_mcp_execute(ctx: ChatContext) -> Optional[str]:
    """检测 LLM 回复中的 MCP 工具调用并执行。"""
    if not ctx.reply_text:
        return None

    tool_call = parse_tool_call(ctx.reply_text)
    if not tool_call:
        return None

    tool_name = tool_call["tool"]
    tool_args = tool_call.get("args", {})
    logger.info(f"[MCP] LLM 请求调用工具: {tool_name} args={tool_args}")

    try:
        result = await mcp_call_tool(tool_name, tool_args, user_id=ctx.user_id)
        if result:
            # 将工具结果注入到回复中（替换工具调用标记，不加前缀保持人设）
            ctx.reply_text = remove_tool_call(ctx.reply_text)
            if ctx.reply_text.strip():
                ctx.reply_text += f"\n{result[:800]}"
            else:
                ctx.reply_text = result[:800]
            logger.info(f"[MCP] 工具 '{tool_name}' 执行成功 ({len(result)}字)")
        else:
            # 工具调用失败，移除标记但不添加错误信息
            ctx.reply_text = remove_tool_call(ctx.reply_text)
            logger.warning(f"[MCP] 工具 '{tool_name}' 执行失败或无结果")
    except Exception as e:
        logger.error(f"[MCP] 工具执行异常: {e}")
        ctx.reply_text = remove_tool_call(ctx.reply_text)

    return None
