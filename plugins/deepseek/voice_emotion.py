"""从语音文件中提取情绪相关特征（音量、语速、时长）。

纯本地处理，不依赖外部 API。
使用 ffmpeg 提取 PCM 数据后分析音频特征。
"""
import os
import asyncio
import struct
from typing import Optional, Dict, Any

from nonebot import logger


async def extract_voice_features(audio_path: str) -> Optional[Dict[str, Any]]:
    """从音频文件提取特征。返回 None 表示提取失败。

    返回字段:
      - rms_volume: float (RMS 音量，越大越激动)
      - duration_ms: float (时长毫秒)
      - silence_ratio: float (静音占比 0~1，越高越犹豫)
      - estimated_emotion: str (推断情绪)
    """
    if not os.path.exists(audio_path):
        return None

    features = {"file_size": os.path.getsize(audio_path)}

    # 用 ffmpeg 提取 PCM 并分析
    pcm_path = audio_path.rsplit(".", 1)[0] + "_analysis.pcm"
    try:
        cmd = [
            "ffmpeg", "-y", "-i", audio_path,
            "-f", "s16le", "-ar", "16000", "-ac", "1",
            pcm_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        if not os.path.exists(pcm_path) or os.path.getsize(pcm_path) < 100:
            return None

        with open(pcm_path, "rb") as f:
            raw_data = f.read()

        # PCM 16-bit signed → 采样点列表
        sample_count = len(raw_data) // 2
        if sample_count == 0:
            return None

        samples = struct.unpack(f"<{sample_count}h", raw_data)

        # RMS 音量
        rms = (sum(s ** 2 for s in samples) / sample_count) ** 0.5
        features["rms_volume"] = rms

        # 时长（16kHz 采样率）
        features["duration_ms"] = sample_count / 16000 * 1000

        # 静音比（低于阈值的采样点占比）
        silence_threshold = 500
        silence_count = sum(1 for s in samples if abs(s) < silence_threshold)
        features["silence_ratio"] = silence_count / sample_count

        # 情绪推断
        features["estimated_emotion"] = _estimate_emotion(features)

    except Exception as e:
        logger.debug(f"[语音特征] 提取失败: {e}")
        return None
    finally:
        if os.path.exists(pcm_path):
            try:
                os.remove(pcm_path)
            except OSError:
                pass

    return features


def _estimate_emotion(features: dict) -> str:
    """从音频特征推断情绪。"""
    rms = features.get("rms_volume", 0)
    silence_ratio = features.get("silence_ratio", 0.5)
    duration = features.get("duration_ms", 0)

    # 高音量 + 低静音比 = 激动/生气
    if rms > 8000 and silence_ratio < 0.3:
        return "激动"
    # 低音量 + 高静音比 = 犹豫/难过
    if rms < 2000 and silence_ratio > 0.6:
        return "犹豫"
    # 极短语音 = 简短回应
    if duration < 1000:
        return "简短"
    # 中等音量 + 正常静音比 = 正常
    return "正常"
