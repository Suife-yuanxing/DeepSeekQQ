"""音频/语音共享工具函数。
voice/voice_mimo/stt/stt_mimo 共同依赖的底层操作。
"""
import os
import asyncio
from typing import Optional, List

import aiofiles
from nonebot import logger


# ============================================================
# 文件系统工具
# ============================================================

def ensure_dir(path: str):
    """确保目录存在，不存在则创建。"""
    os.makedirs(path, exist_ok=True)


def safe_remove(path: Optional[str]):
    """安全删除文件，忽略文件不存在等错误。"""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def validate_file(path: str, min_size: int = 100) -> bool:
    """验证文件存在且不小于 min_size 字节。"""
    return os.path.exists(path) and os.path.getsize(path) > min_size


# ============================================================
# 异步文件写入
# ============================================================

async def write_audio_file(path: str, data: bytes) -> bool:
    """异步写入音频数据到文件，返回是否成功。"""
    try:
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)
        return True
    except OSError as e:
        logger.error(f"[音频] 写入文件失败 {path}: {e}")
        return False


# ============================================================
# 异步清理
# ============================================================

async def _delayed_cleanup(path: str, delay: int = 300):
    """延迟后删除单个文件。"""
    await asyncio.sleep(delay)
    safe_remove(path)


async def _delayed_cleanup_multi(paths: List[str], delay: int = 60):
    """延迟后删除多个文件。"""
    await asyncio.sleep(delay)
    for p in paths:
        safe_remove(p)


def schedule_cleanup(path: str, delay: int = 300):
    """调度一个延迟清理任务（fire-and-forget）。"""
    from .utils import safe_task
    safe_task(_delayed_cleanup(path, delay))


def schedule_cleanup_multi(paths: List[str], delay: int = 60):
    """调度多个文件的延迟清理任务（fire-and-forget）。"""
    from .utils import safe_task
    safe_task(_delayed_cleanup_multi(paths, delay))


# ============================================================
# ffmpeg 转换
# ============================================================

async def convert_audio_with_ffmpeg(
    input_path: str,
    output_path: str,
    sample_rate: int = 16000,
    channels: int = 1,
    extra_args: Optional[List[str]] = None,
) -> bool:
    """使用 ffmpeg 转换音频格式。

    Args:
        input_path: 输入文件路径
        output_path: 输出文件路径
        sample_rate: 采样率 (Hz)
        channels: 声道数
        extra_args: 额外的 ffmpeg 参数

    Returns:
        转换是否成功
    """
    try:
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels),
        ]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(output_path)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning(f"[音频] ffmpeg 转换失败: {stderr.decode()[:200]}")
            return False

        if validate_file(output_path, 100):
            logger.info(f"[音频] 转换成功: {output_path}")
            return True
        else:
            logger.warning(f"[音频] 转换输出文件过小或不存在: {output_path}")
            return False
    except Exception as e:
        logger.error(f"[音频] ffmpeg 转换异常: {e}")
        return False


# ============================================================
# 时间戳路径生成
# ============================================================

def make_audio_path(prefix: str, dir_path: str, ext: str = ".mp3") -> str:
    """生成带时间戳的音频文件路径。"""
    import time
    ensure_dir(dir_path)
    return f"{dir_path}/{prefix}_{int(time.time() * 1000)}{ext}"
