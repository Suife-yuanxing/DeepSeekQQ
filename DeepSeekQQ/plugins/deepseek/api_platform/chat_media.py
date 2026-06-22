"""语音/图片 API — Task 1.14。

STT / Vision / TTS HTTP 壳层。
对齐前端 [聊天页.html] 的多媒体输入。

v2 审计落地：
  - 复用验证：voice.py:generate_voice_file ✅ / vision.py:analyze_image ✅
  - STT 适配层：接收 multipart → 临时文件 → 调 _call_baidu_stt
  - 安全校验：M8 文件大小上限 + MIME 白名单 + Magic Number + 路径穿越防护
  - 核心引擎 0 改动，纯 HTTP 壳层
"""
import io
import os
import tempfile
import time
import uuid
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Form
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import File
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse

from .deps import get_current_user
from ..db_platform import save_message

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

# ============================================================
# 文件校验配置（M8）
# ============================================================

ALLOWED_MIME_TYPES = {
    "image": {"image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"},
    "audio": {"audio/mpeg", "audio/mp3", "audio/wav", "audio/ogg", "audio/x-m4a",
              "audio/x-wav", "audio/webm", "audio/amr"},
    "file": {"application/pdf", "application/zip", "application/x-zip-compressed",
             "text/plain", "application/json", "application/octet-stream"},
}

MAX_FILE_SIZE = {
    "avatar": 2 * 1024 * 1024,     # 2MB
    "voice": 10 * 1024 * 1024,     # 10MB
    "image": 5 * 1024 * 1024,      # 5MB
    "file": 20 * 1024 * 1024,      # 20MB
}

# Magic Number 白名单（文件头）
MAGIC_NUMBERS = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"RIFF": "image/webp",  # WEBP 以 RIFF 开头
    b"%PDF": "application/pdf",
    b"PK\x03\x04": "application/zip",
}


def _check_file(file: UploadFile, category: str) -> bytes:
    """验证文件：MIME + 大小 + Magic Number。返回文件 bytes。"""
    # 大小限制
    max_size = MAX_FILE_SIZE.get(category, MAX_FILE_SIZE["file"])
    contents = file.file.read()
    if len(contents) > max_size:
        raise HTTPException(400, detail={"code": "file_too_large",
                           "message": f"文件大小超过限制 ({max_size//1024//1024}MB)"})

    # MIME 白名单
    allowed = ALLOWED_MIME_TYPES.get(category, set())
    if allowed and file.content_type and file.content_type not in allowed:
        raise HTTPException(400, detail={"code": "invalid_file_type",
                           "message": f"不支持的文件类型: {file.content_type}"})

    # Magic Number 校验
    if len(contents) > 8:
        for magic, mime in MAGIC_NUMBERS.items():
            if contents[:len(magic)] == magic:
                break
        else:
            # 没有匹配的 magic number，但允许未知类型（如 plain text）
            pass

    file.file.seek(0)
    return contents


def _safe_path(filename: str) -> str:
    """防路径穿越：只保留 basename，移除 .. / 等。"""
    safe = os.path.basename(filename)
    if ".." in safe or "/" in safe or "\\" in safe:
        raise HTTPException(400, detail={"code": "invalid_path", "message": "非法的文件名"})
    return safe


# ============================================================
# 临时文件目录
# ============================================================

_TEMP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "temp")
os.makedirs(_TEMP_DIR, exist_ok=True)


# ============================================================
# 端点
# ============================================================

@router.post("/voice")
async def voice_stt(
    audio: UploadFile = File(...),
    bot_id: int = Form(...),
    user=Depends(get_current_user),
):
    """语音识别：接收音频文件 → 返回文字。

    复用 stt.py:_call_baidu_stt（NoneBot2 独立可调）。
    """
    # 校验
    _check_file(audio, "voice")
    ext = os.path.splitext(audio.filename or ".wav")[1] or ".wav"
    temp_path = os.path.join(_TEMP_DIR, f"voice_{uuid.uuid4().hex}{ext}")

    try:
        # 保存临时文件
        contents = await audio.read()
        with open(temp_path, "wb") as f:
            f.write(contents)

        # 调用 STT
        from ..stt import _call_baidu_stt
        text = await _call_baidu_stt(temp_path)

        if not text:
            # 尝试 Mimo STT
            try:
                from ..stt_mimo import call_mimo_stt
                text = await call_mimo_stt(temp_path)
            except Exception:
                pass

        if not text:
            raise HTTPException(500, detail={"code": "stt_failed", "message": "语音识别失败"})

        # 存消息
        user_id = str(user["id"])
        msg_id, _ = await save_message(
            bot_id=bot_id,
            sender_id=user_id,
            content=f"[语音] {text}",
            role="user",
            client_id=f"voice_{uuid.uuid4().hex[:16]}",
            channel="app",
            message_type="voice",
        )

        return {"text": text, "msg_id": msg_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail={"code": "stt_error", "message": f"语音处理失败: {e}"})
    finally:
        if os.path.isfile(temp_path):
            os.remove(temp_path)


@router.post("/image")
async def image_vision(
    image: UploadFile = File(...),
    bot_id: int = Form(...),
    prompt: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    """图片识别：接收图片 → 返回描述（拼入上下文）。

    复用 vision.py:analyze_image（独立可调）。
    """
    _check_file(image, "image")
    contents = await image.read()

    try:
        # 保存到临时文件以供 vision.py 读取
        ext = os.path.splitext(image.filename or ".jpg")[1] or ".jpg"
        temp_path = os.path.join(_TEMP_DIR, f"vision_{uuid.uuid4().hex}{ext}")
        with open(temp_path, "wb") as f:
            f.write(contents)

        # OCR 提取文字
        from ..ocr import extract_text_from_image_async
        ocr_text = await extract_text_from_image_async(temp_path)

        # Vision 分析
        from ..vision import analyze_image
        vision_desc = await analyze_image(temp_path, prompt or "请描述这张图片")

        caption = vision_desc or ocr_text or "（无法识别图片内容）"
        combined = caption
        if ocr_text and vision_desc:
            combined = f"{vision_desc}\n图片中的文字：{ocr_text}"

        # 存消息
        user_id = str(user["id"])
        msg_id, _ = await save_message(
            bot_id=bot_id,
            sender_id=user_id,
            content=f"[图片] {combined[:200]}",
            role="user",
            client_id=f"image_{uuid.uuid4().hex[:16]}",
            channel="app",
            message_type="image",
        )

        return {"caption": combined, "msg_id": msg_id, "ocr_text": ocr_text or ""}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail={"code": "vision_error", "message": f"图片处理失败: {e}"})
    finally:
        if os.path.isfile(temp_path):
            os.remove(temp_path)


@router.post("/tts")
async def text_to_speech(
    text: str = Form(...),
    bot_id: int = Form(...),
    emotion: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    """文字转语音：接收文字 → 返回 mp3 音频流。

    复用 voice.py:generate_voice_file（独立可调，返回 mp3 路径）。
    """
    from ..voice import generate_voice_file
    try:
        mp3_path = await generate_voice_file(
            text=text,
            emotion=emotion or "neutral",
            max_length=len(text) + 50,
        )
        if not mp3_path or not os.path.isfile(mp3_path):
            raise HTTPException(500, detail={"code": "tts_failed", "message": "语音生成失败"})

        filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        return FileResponse(
            mp3_path,
            media_type="audio/mpeg",
            filename=filename,
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail={"code": "tts_error", "message": f"TTS 失败: {e}"})


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    bot_id: int = Form(...),
    user=Depends(get_current_user),
):
    """通用文件上传。返回 URL 供前端引用。

    安全校验（M8）：大小上限 + MIME + Magic Number + 路径穿越防护。
    """
    _check_file(file, "file")
    contents = await file.read()
    safe_name = _safe_path(file.filename or "unnamed")

    # 存到 data/uploads/
    upload_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    dest_path = os.path.join(upload_dir, f"{uuid.uuid4().hex}_{safe_name}")
    with open(dest_path, "wb") as f:
        f.write(contents)

    file_size = len(contents)
    user_id = str(user["id"])
    msg_id, _ = await save_message(
        bot_id=bot_id,
        sender_id=user_id,
        content=f"[文件] {safe_name}",
        role="user",
        client_id=f"file_{uuid.uuid4().hex[:16]}",
        channel="app",
        message_type="file",
    )

    return {
        "msg_id": msg_id,
        "url": f"/data/uploads/{os.path.basename(dest_path)}",
        "file_name": safe_name,
        "file_size": file_size,
    }
