"""WebSocket 聊天 + 消息历史 — Task 1.6 精简版（方案 B）。

v2 审计修正落地：
  - S1: 新写 call_deepseek_api_stream（SSE 流式），不复用 stream:False 的 call_deepseek_api
  - S2: 方案 B（绕过 Pipeline）—— App 聊天无情绪/记忆/人设演化增强，P0 跑通用，完整版升级方案 A
  - S3: 依赖 db_platform.chat_messages（client_id 幂等去重）
  - S5: JWT 通过 Sec-WebSocket-Protocol 子协议头传递
  - H5: WS 每条 msg 帧校验 bot_id 属于 JWT.user_id
  - 已读回执帧 {type:"read", msg_ids:[]}（v2 遗漏点补齐）

协议帧：
  客户端→服务端: {type:"msg", bot_id, text, client_id} / {type:"read", msg_ids:[]}
  服务端→客户端: {type:"token", text} / {type:"done", server_id, client_id} /
                 {type:"typing"} / {type:"error", message} / {type:"ack", client_id}
"""
import json
import time
import uuid
from typing import AsyncGenerator
from typing import Optional

import aiohttp
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from pydantic import BaseModel

from .deps import ws_current_user
from .deps import require_bot_owner
from .deps import get_current_user
from ..db_platform import get_bot
from ..db_platform import get_bot_for_user
from ..db_platform import get_messages
from ..db_platform import save_message

router = APIRouter(prefix="/api/v1", tags=["chat"])

# ============================================================
# 流式 LLM 调用（S1: 新写，不复用 stream:False 的 call_deepseek_api）
# ============================================================

async def call_deepseek_api_stream(messages: list[dict]) -> AsyncGenerator[str, None]:
    """流式调用 DeepSeek，逐 token yield。

    S1: api.py:79 当前 stream:False 不可复用，这里新写 SSE 流式解析。
    降级：流式失败时 fallback 到 call_deepseek_api（非流式）一次性返回。
    """
    from ..api import get_http_session
    from ..config import API_KEY, BASE_URL, MODEL

    if not API_KEY:
        yield "（未配置 DEEPSEEK_API_KEY，无法回复）"
        return

    url = f"{BASE_URL}/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": messages,
        "stream": True,
        "temperature": 0.9,
        "max_tokens": 1500,
    }

    try:
        session = await get_http_session()
        async with session.post(
            url, headers=headers, json=payload,
            timeout=aiohttp.ClientTimeout(total=60, connect=5, sock_read=30),
        ) as resp:
            if resp.status != 200:
                # 降级到非流式
                from ..api import call_deepseek_api
                yield await call_deepseek_api(messages, task_type="chat")
                return
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
    except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError):
        # 降级到非流式
        from ..api import call_deepseek_api
        yield await call_deepseek_api(messages, task_type="chat")


import asyncio  # noqa: E402 (放在这里避免顶部 import 干扰)


# ============================================================
# 简化版 system prompt（方案 B：基于 persona_json，不走 Pipeline 的 build_system_prompt）
# ============================================================

PERSONALITY_PROMPTS = {
    "tsundere": "你性格傲娇，嘴上不饶人但内心关心对方，常用'哼！'开头，偶尔流露温柔。",
    "gentle": "你性格温柔体贴，说话轻声细语，总是关心对方的感受，用词柔和。",
    "sarcastic": "你性格毒舌，喜欢用反讽和调侃回应，但不含恶意，像朋友间的互怼。",
    "energetic": "你性格元气满满，说话充满活力，常用感叹号，对什么都充满热情。",
    "emotionless": "你性格三无（无口无心无表情），说话简洁平淡，用词客观冷静。",
    "sly": "你性格腹黑，说话带着小聪明和算计，喜欢逗弄对方，看破不说破。",
}


def build_simple_system_prompt(bot: dict) -> str:
    """方案 B 简化版 system prompt，基于 bot_configs.persona_json。"""
    persona = json.loads(bot["persona_json"]) if bot.get("persona_json") else {}
    base = PERSONALITY_PROMPTS.get(bot["personality"], PERSONALITY_PROMPTS["gentle"])
    parts = [
        f"你是 {bot['bot_name']}，一个 AI 聊天伙伴。",
        base,
    ]
    if persona.get("description"):
        parts.append(f"性格补充：{persona['description']}")
    if persona.get("catchphrase"):
        parts.append(f"口头禅：{persona['catchphrase']}")
    if persona.get("age"):
        parts.append(f"年龄：{persona['age']}岁")
    if persona.get("speech_style"):
        parts.append(f"说话风格：{persona['speech_style']}")
    if persona.get("backstory"):
        parts.append(f"背景故事：{persona['backstory']}")
    if persona.get("special_rules"):
        parts.append(f"特殊设定：{persona['special_rules']}")
    # 滑块 6 维
    sliders = []
    if "style_score" in persona:
        sliders.append(f"温柔程度 {persona['style_score']}/10")
    if "talkativeness" in persona:
        sliders.append(f"话量 {persona['talkativeness']}/10")
    if "formality" in persona:
        sliders.append(f"正式程度 {persona['formality']}/10")
    if "initiative" in persona:
        sliders.append(f"主动程度 {persona['initiative']}/10")
    if "emotion_intensity" in persona:
        sliders.append(f"情绪表达强度 {persona['emotion_intensity']}%")
    if "reply_length" in persona:
        length_map = {0: "极简短", 1: "简短", 2: "中等", 3: "详细", 4: "很详细"}
        sliders.append(f"回复长度偏好：{length_map.get(persona['reply_length'], '中等')}")
    if "call_preference" in persona:
        call_map = {"master": "主人", "brother": "哥哥", "sister": "姐姐", "name": "对方名字", "custom": persona.get("custom_call", "你")}
        sliders.append(f"称呼对方为：{call_map.get(persona['call_preference'], '你')}")
    if sliders:
        parts.append("风格设定：" + "，".join(sliders))
    parts.append("请用自然口语回复，不超过 3-4 句话。不要暴露自己是 AI 模型。")
    return "\n".join(parts)


# ============================================================
# REST: 消息历史
# ============================================================

@router.get("/messages")
async def list_messages(
    bot_id: int = Query(...),
    cursor: Optional[float] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
):
    """获取聊天历史（游标分页）。H5: 校验 bot_id 归属。"""
    await require_bot_owner(bot_id, user)
    msgs = await get_messages(bot_id, cursor=cursor, limit=limit)
    return {
        "messages": [_msg_public(m) for m in msgs],
        "has_more": len(msgs) == limit,
        "next_cursor": msgs[-1]["created_at"] if msgs and len(msgs) == limit else None,
    }


@router.get("/messages/search")
async def search_messages(
    q: str = Query(..., min_length=1),
    bot_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
):
    """全文搜索聊天消息。H5: 如果指定 bot_id 则校验归属。"""
    from ..db_core import get_db
    if bot_id is not None:
        await require_bot_owner(bot_id, user)
    db = await get_db()
    parts = ["SELECT * FROM chat_messages WHERE content LIKE ?"]
    params = [f"%{q}%"]
    if bot_id is not None:
        parts.append("AND bot_id = ?")
        params.append(bot_id)
    if date_from:
        from datetime import datetime
        try:
            ts = datetime.fromisoformat(date_from).timestamp()
            parts.append("AND created_at >= ?")
            params.append(ts)
        except ValueError:
            pass
    if date_to:
        from datetime import datetime
        try:
            ts = datetime.fromisoformat(date_to).timestamp()
            parts.append("AND created_at <= ?")
            params.append(ts)
        except ValueError:
            pass
    parts.append("ORDER BY created_at DESC LIMIT ?")
    params.append(limit)
    sql = " ".join(parts)
    async with db.execute(sql, tuple(params)) as cur:
        rows = await cur.fetchall()
    return {
        "messages": [_msg_public(dict(r)) for r in rows],
        "count": len(rows),
        "query": q,
    }


# ============================================================
# WebSocket: 流式聊天
# ============================================================

@router.websocket("/chat/ws")
async def chat_ws(ws: WebSocket):
    """WS 流式聊天。S5: JWT 通过子协议头传递。H5: 每条 msg 校验 bot_id 归属。"""
    # 认证
    try:
        user = await ws_current_user(ws)
    except HTTPException:
        return  # ws_current_user 已 close

    # 握手：选中的 subprotocol 必须回传客户端（Starlette 会自动选第一个）
    subprotocols = ws.scope.get("subprotocols", [])
    selected = next((sp for sp in subprotocols if sp.startswith("bearer.")), subprotocols[0] if subprotocols else None)
    await ws.accept(subprotocol=selected)

    user_id_str = str(user["id"])

    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await _send(ws, {"type": "error", "message": "无效的 JSON 帧"})
                continue

            ftype = frame.get("type")

            if ftype == "msg":
                await _handle_msg(ws, frame, user, user_id_str)
            elif ftype == "read":
                # v2 已读回执帧（P0 精简版只 ack，不持久化已读状态）
                await _send(ws, {"type": "read_ack", "msg_ids": frame.get("msg_ids", [])})
            elif ftype == "ping":
                await _send(ws, {"type": "pong"})
            else:
                await _send(ws, {"type": "error", "message": f"未知帧类型: {ftype}"})
    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await _send(ws, {"type": "error", "message": f"服务端错误: {e}"})
        except Exception:
            pass


async def _handle_msg(ws: WebSocket, frame: dict, user: dict, user_id_str: str) -> None:
    """处理消息帧：校验 ownership → 存用户消息 → 流式 LLM 回复 → 學 Bot 消息。"""
    bot_id = frame.get("bot_id")
    text = (frame.get("text") or "").strip()
    client_id = frame.get("client_id") or uuid.uuid4().hex

    if not bot_id or not text:
        await _send(ws, {"type": "error", "message": "缺少 bot_id 或 text"})
        return

    # H5: ownership 校验
    bot = await get_bot_for_user(int(bot_id), user["id"])
    if not bot:
        await _send(ws, {"type": "error", "message": "无权访问该 Bot", "code": "bot_not_owned"})
        return

    # S3: 存用户消息（client_id 幂等）
    user_msg_id, created = await save_message(
        bot_id=int(bot_id),
        sender_id=user_id_str,
        content=text,
        role="user",
        client_id=client_id,
        channel="app",
        status="replied",
    )
    # ACK（告知客户端用户消息已收到，client_id 幂等时 created=False）
    await _send(ws, {"type": "ack", "client_id": client_id, "msg_id": user_msg_id, "duplicate": not created})

    if not created:
        # 幂等命中：不重复触发 LLM 回复
        return

    # typing 指示
    await _send(ws, {"type": "typing"})

    # 构造 LLM 上下文（方案 B 简化版：system + 最近历史 + 当前消息）
    history = await get_messages(int(bot_id), limit=20)
    history.reverse()  # 时间正序
    messages = [{"role": "system", "content": build_simple_system_prompt(bot)}]
    for m in history:
        if m["role"] in ("user", "assistant") and m["content"]:
            messages.append({"role": m["role"], "content": m["content"]})

    # 流式回复
    bot_client_id = uuid.uuid4().hex
    full_reply = ""
    try:
        async for chunk in call_deepseek_api_stream(messages):
            full_reply += chunk
            await _send(ws, {"type": "token", "text": chunk})
    except Exception as e:
        await _send(ws, {"type": "error", "message": f"LLM 调用失败: {e}"})
        return

    # 存 Bot 回复
    bot_reply_id, _ = await save_message(
        bot_id=int(bot_id),
        sender_id=str(bot["id"]),
        content=full_reply or "（空回复）",
        role="bot",
        client_id=bot_client_id,
        channel="app",
        status="replied",
    )
    await _send(ws, {
        "type": "done",
        "server_id": bot_reply_id,
        "client_id": client_id,
        "reply_msg_id": bot_reply_id,
    })


async def _send(ws: WebSocket, obj: dict) -> None:
    """安全发送 JSON 帧。"""
    try:
        await ws.send_text(json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass


def _msg_public(m: dict) -> dict:
    return {
        "id": m["id"],
        "role": m["role"],
        "content": m["content"],
        "type": m["message_type"],
        "time": m["created_at"],
        "status": m["status"],
        "client_id": m["client_id"],
    }
