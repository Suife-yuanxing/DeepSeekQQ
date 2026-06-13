"""视频文件处理模块 — 提取关键帧并通过视觉模型理解视频内容。

功能：
1. 下载视频文件（来自 QQ 消息的视频附件 URL）
2. 用 ffmpeg 提取关键帧（首帧 + 中间帧 + 末帧，最多3帧）
3. 将关键帧发送给 analyze_image() 进行视觉识别
4. 汇总帧描述，生成视频内容摘要

全局可调用：from .video_processor import process_video
"""

import asyncio
import base64
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Optional

import aiohttp

logger = logging.getLogger("deepseek.video_processor")

# 帧提取配置
MAX_FRAMES = 3          # 最多提取3帧
FRAME_QUALITY = 60      # JPEG 质量（1-100），平衡质量和文件大小
MAX_VIDEO_SIZE_MB = 50  # 最大视频大小，超过不处理
DOWNLOAD_TIMEOUT = 60   # 视频下载超时（秒）
FFMPEG_TIMEOUT = 30     # ffmpeg 处理超时（秒）


class VideoProcessResult:
    """视频处理结果。"""

    def __init__(
        self,
        summary: str = "",
        frame_count: int = 0,
        duration: float = 0.0,
        source: str = "",
        error: str = "",
    ):
        self.summary = summary
        self.frame_count = frame_count
        self.duration = duration
        self.source = source  # "vision" | "ocr" | "placeholder" | "error"
        self.error = error

    @property
    def success(self) -> bool:
        return bool(self.summary) and self.source != "error"


async def process_video(
    url: str,
    prompt: str = "请详细描述这张视频截图中显示的内容，用中文回答",
) -> Optional[str]:
    """处理视频文件：下载 → 提取关键帧 → 视觉分析 → 汇总描述。

    Args:
        url: 视频文件 URL（来自 QQ 消息的视频附件）
        prompt: 给视觉模型的提示词

    Returns:
        视频内容描述的包装字符串（与 analyze_image 格式兼容），
        失败时返回 None。
    """
    from .vision import analyze_image

    logger.info(f"[视频处理] 开始处理: {url[:80]}...")

    # ===== 第1步：下载视频 =====
    video_path = None
    try:
        video_path = await _download_video(url)
        if not video_path:
            logger.warning("[视频处理] 下载失败")
            return None

        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if file_size_mb > MAX_VIDEO_SIZE_MB:
            logger.warning(f"[视频处理] 视频过大 ({file_size_mb:.1f}MB > {MAX_VIDEO_SIZE_MB}MB)，跳过")
            return None

        logger.info(f"[视频处理] 下载完成: {file_size_mb:.1f}MB -> {video_path}")

    except Exception as e:
        logger.warning(f"[视频处理] 下载异常: {type(e).__name__}: {e}")
        return None

    # ===== 第2步：提取关键帧 =====
    frames = []
    duration = 0.0
    try:
        frames, duration = await _extract_key_frames(video_path)
        if not frames:
            logger.warning("[视频处理] 帧提取失败，使用首帧重试")
            frames = await _extract_first_frame(video_path)
            if frames:
                duration = await _get_video_duration(video_path)

        logger.info(f"[视频处理] 提取了 {len(frames)} 帧 (时长 {duration:.1f}s)")
    except Exception as e:
        logger.warning(f"[视频处理] 帧提取异常: {type(e).__name__}: {e}")

    # ===== 第3步：逐帧分析 =====
    frame_descriptions = []
    for i, frame_path in enumerate(frames):
        try:
            frame_prompt = _build_frame_prompt(prompt, i, len(frames), duration)
            result = await analyze_image(frame_path, frame_prompt)
            if result:
                from .vision import extract_vision_text
                desc = extract_vision_text(result)
                if desc:
                    frame_descriptions.append(f"[帧{i+1}] {desc}")
        except Exception as e:
            logger.warning(f"[视频处理] 帧{i+1} 分析失败: {e}")

    # ===== 第4步：清理临时文件 =====
    _cleanup(video_path, frames)

    if not frame_descriptions:
        logger.warning("[视频处理] 所有帧分析失败")
        return None

    # ===== 第5步：汇总视频描述 =====
    duration_text = f"{int(duration // 60)}分{int(duration % 60)}秒" if duration >= 60 else f"{int(duration)}秒"
    combined = f"[视频内容: 时长约{duration_text}]\n" + "\n".join(frame_descriptions)

    logger.info(f"[视频处理] ✅ 完成: {len(frame_descriptions)}帧, {len(combined)}字")
    return combined


def _build_frame_prompt(base_prompt: str, idx: int, total: int, duration: float) -> str:
    """为每帧构造专门的 prompt。"""
    if total == 1:
        return f"{base_prompt}\n这是视频的唯一截帧，请详细描述画面内容。"
    if idx == 0:
        return f"{base_prompt}\n这是视频的开头画面（第1帧，共{total}帧），请描述你看到的内容。"
    if idx == total - 1:
        return f"{base_prompt}\n这是视频的结尾画面（第{total}帧），请描述你看到的内容。"
    return f"{base_prompt}\n这是视频中间的画面（第{idx+1}帧，共{total}帧），请描述你看到的内容。"


# ============================================================
# 视频下载
# ============================================================


async def _download_video(url: str) -> Optional[str]:
    """下载视频到临时文件。"""
    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"[视频处理] 下载失败 status={resp.status}: {url[:80]}")
                return None

            # 从 Content-Type 或 URL 推测扩展名
            content_type = resp.headers.get("Content-Type", "")
            ext = _guess_ext(content_type, url)
            suffix = f".{ext}" if ext else ".mp4"

            # 写入临时文件
            fd, path = tempfile.mkstemp(suffix=suffix, prefix="qq_video_")
            os.close(fd)

            with open(path, "wb") as f:
                total = 0
                async for chunk in resp.content.iter_chunked(8192):
                    f.write(chunk)
                    total += len(chunk)
                    if total > MAX_VIDEO_SIZE_MB * 1024 * 1024:
                        f.close()
                        os.unlink(path)
                        logger.warning(f"[视频处理] 下载超过最大大小 {MAX_VIDEO_SIZE_MB}MB")
                        return None

            return path

    except asyncio.TimeoutError:
        logger.warning(f"[视频处理] 视频下载超时 ({DOWNLOAD_TIMEOUT}s): {url[:80]}")
        return None
    except Exception as e:
        logger.warning(f"[视频处理] 视频下载异常: {type(e).__name__}: {e}")
        return None


def _guess_ext(content_type: str, url: str) -> str:
    """推测视频文件扩展名。"""
    ct_map = {
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
        "video/x-msvideo": "avi",
        "video/mpeg": "mpg",
        "video/ogg": "ogv",
    }
    for ct, ext in ct_map.items():
        if ct in content_type:
            return ext

    # 从 URL 推测
    for ext in ("mp4", "webm", "mov", "avi", "mkv", "flv", "wmv"):
        if f".{ext}" in url.lower():
            return ext

    return "mp4"  # 默认


# ============================================================
# ffmpeg 帧提取
# ============================================================


async def _extract_key_frames(video_path: str) -> tuple:
    """从视频中提取关键帧（首帧 + 中间帧 + 末帧）。

    Returns:
        (frame_paths: List[str], duration: float)
    """
    duration = await _get_video_duration(video_path)
    if duration <= 0:
        logger.warning(f"[视频处理] 无法获取视频时长: {video_path}")
        return [], 0.0

    # 提取首帧（0秒） + 中间帧 + 末帧
    positions = [0.0]
    if duration > 3:
        positions.append(duration / 2)
    if duration > 1:
        positions.append(max(duration - 0.5, duration * 0.95))

    frames = []
    for i, pos in enumerate(positions):
        frame_path = await _extract_frame_at(video_path, pos, i)
        if frame_path:
            frames.append(frame_path)

    return frames, duration


async def _extract_first_frame(video_path: str) -> List[str]:
    """回退：只提取首帧。"""
    frame = await _extract_frame_at(video_path, 0.0, 0)
    return [frame] if frame else []


async def _extract_frame_at(video_path: str, time_sec: float, idx: int) -> Optional[str]:
    """在指定时间位置提取一帧 JPEG。

    Args:
        video_path: 视频文件路径
        time_sec: 提取位置（秒）
        idx: 帧编号（用于临时文件命名）

    Returns:
        帧文件路径，失败返回 None
    """
    fd, out_path = tempfile.mkstemp(suffix=f"_f{idx}.jpg", prefix="qq_frame_")
    os.close(fd)

    cmd = [
        "ffmpeg",
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", str(max(1, min(31, (100 - FRAME_QUALITY) * 31 // 100 + 1))),
        "-y",  # 覆盖输出
        out_path,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=FFMPEG_TIMEOUT,
        )
        await proc.wait()

        if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
            return out_path
        else:
            if os.path.exists(out_path):
                os.unlink(out_path)
            logger.debug(f"[视频处理] ffmpeg 帧{idx} 输出为空 (t={time_sec:.1f}s)")
            return None

    except asyncio.TimeoutError:
        logger.warning(f"[视频处理] ffmpeg 帧{idx} 超时 (t={time_sec:.1f}s)")
        if os.path.exists(out_path):
            os.unlink(out_path)
        return None
    except FileNotFoundError:
        logger.error("[视频处理] ffmpeg 未安装或不在 PATH 中")
        if os.path.exists(out_path):
            os.unlink(out_path)
        return None
    except Exception as e:
        logger.warning(f"[视频处理] ffmpeg 帧{idx} 异常: {type(e).__name__}: {e}")
        if os.path.exists(out_path):
            os.unlink(out_path)
        return None


async def _get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）。"""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        await proc.wait()
        duration_str = stdout.decode().strip()
        return float(duration_str) if duration_str else 0.0
    except Exception as e:
        logger.debug(f"[视频处理] 获取时长失败: {e}")
        return 0.0


# ============================================================
# 清理
# ============================================================


def _cleanup(video_path: Optional[str], frame_paths: List[str]) -> None:
    """清理临时文件。"""
    if video_path and os.path.exists(video_path):
        try:
            os.unlink(video_path)
        except OSError:
            pass
    for f in frame_paths:
        if os.path.exists(f):
            try:
                os.unlink(f)
            except OSError:
                pass
