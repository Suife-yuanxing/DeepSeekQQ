"""从语音文件中提取情绪相关特征（音量、语速、时长、频谱特征）。

纯本地处理，不依赖外部 API。
使用 ffmpeg 提取 PCM 数据后分析音频特征。
增强版：增加频谱特征和语速分析，提升情绪识别准确率。
"""
import os
import asyncio
import struct
import math
from typing import Optional, Dict, Any, List

from nonebot import logger


def _analyze_pcm_sync(pcm_path: str, features: dict) -> dict:
    """同步分析 PCM 文件（供 asyncio.to_thread 调用）。"""
    with open(pcm_path, "rb") as f:
        raw_data = f.read()

    sample_count = len(raw_data) // 2
    if sample_count == 0:
        return features

    samples = struct.unpack(f"<{sample_count}h", raw_data)

    # RMS 音量（分帧计算）
    frame_size = 1600  # 100ms @ 16kHz
    rms_values = []
    for i in range(0, sample_count - frame_size, frame_size):
        frame = samples[i:i + frame_size]
        rms = (sum(s ** 2 for s in frame) / frame_size) ** 0.5
        rms_values.append(rms)

    features["rms_volume"] = sum(rms_values) / len(rms_values) if rms_values else 0
    features["rms_std"] = _std(rms_values) if len(rms_values) > 1 else 0

    # 时长（16kHz 采样率）
    features["duration_ms"] = sample_count / 16000 * 1000

    # 静音比
    silence_threshold = 500
    silence_count = sum(1 for s in samples if abs(s) < silence_threshold)
    features["silence_ratio"] = silence_count / sample_count

    # 语速估算
    onset_count = _detect_onsets(rms_values, threshold=1000)
    duration_sec = features["duration_ms"] / 1000
    features["speech_rate"] = onset_count / duration_sec if duration_sec > 0 else 0

    # 音高变化
    zero_crossings = _count_zero_crossings(samples)
    features["pitch_std"] = zero_crossings / duration_sec if duration_sec > 0 else 0

    # 频谱质心
    features["spectral_centroid_mean"] = _estimate_spectral_centroid(samples, sample_count)

    # 情绪推断
    emotion, confidence = _estimate_emotion_enhanced(features)
    features["estimated_emotion"] = emotion
    features["confidence"] = confidence
    return features


async def extract_voice_features(audio_path: str) -> Optional[Dict[str, Any]]:
    """从音频文件提取特征。返回 None 表示提取失败。

    返回字段:
      - rms_volume: float (RMS 音量，越大越激动)
      - rms_std: float (RMS 标准差，越大越不稳定)
      - duration_ms: float (时长毫秒)
      - silence_ratio: float (静音占比 0~1，越高越犹豫)
      - speech_rate: float (语速估算，音节数/秒)
      - pitch_std: float (音高变化，越大越激动)
      - spectral_centroid_mean: float (频谱质心均值)
      - estimated_emotion: str (推断情绪)
      - confidence: float (置信度)
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

        # CPU 密集型计算放到线程池，避免阻塞事件循环
        features = await asyncio.to_thread(_analyze_pcm_sync, pcm_path, features)

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


def _std(values: List[float]) -> float:
    """计算标准差"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return math.sqrt(variance)


def _detect_onsets(rms_values: List[float], threshold: float) -> int:
    """基于能量变化检测音节起始点"""
    if len(rms_values) < 2:
        return 0

    onset_count = 0
    prev_rms = rms_values[0]

    for rms in rms_values[1:]:
        # 能量突然增加超过阈值 = 新音节
        if rms > prev_rms * 1.5 and rms > threshold:
            onset_count += 1
        prev_rms = rms

    return onset_count


def _count_zero_crossings(samples: tuple) -> int:
    """计算过零率（音高估算的简化方法）"""
    if len(samples) < 2:
        return 0

    crossings = 0
    prev_sign = 1 if samples[0] >= 0 else -1

    for s in samples[1:]:
        curr_sign = 1 if s >= 0 else -1
        if curr_sign != prev_sign:
            crossings += 1
        prev_sign = curr_sign

    return crossings


def _estimate_spectral_centroid(samples: tuple, sample_count: int) -> float:
    """估算频谱质心（简化版：基于高频能量占比）"""
    if sample_count < 100:
        return 0.0

    # 将信号分为低频和高频部分
    mid_point = sample_count // 2
    low_energy = sum(s ** 2 for s in samples[:mid_point])
    high_energy = sum(s ** 2 for s in samples[mid_point:])

    total_energy = low_energy + high_energy
    if total_energy == 0:
        return 0.0

    # 返回高频能量占比作为频谱质心的近似
    return high_energy / total_energy


def _estimate_emotion_enhanced(features: dict) -> tuple:
    """增强版情绪推断，返回 (情绪, 置信度)"""
    rms = features.get("rms_volume", 0)
    rms_std = features.get("rms_std", 0)
    silence_ratio = features.get("silence_ratio", 0.5)
    duration = features.get("duration_ms", 0)
    speech_rate = features.get("speech_rate", 0)
    pitch_std = features.get("pitch_std", 0)
    spectral_centroid = features.get("spectral_centroid_mean", 0)

    # 规则引擎（多特征综合判断）

    # 激动：音量大 + 语速快 + 音高变化大
    if (rms > 8000 and
        speech_rate > 4 and
        pitch_std > 100):
        return "激动", 0.85

    # 生气：音量大 + 音量波动大 + 频谱偏高
    if (rms > 7000 and
        rms_std > 2000 and
        spectral_centroid > 0.4):
        return "生气", 0.80

    # 低落：音量小 + 语速慢 + 长沉默
    if (rms < 2000 and
        speech_rate < 2 and
        silence_ratio > 0.4):
        return "低落", 0.75

    # 犹豫：语速不均匀 + 频繁停顿
    if (rms_std > 1500 and
        silence_ratio > 0.2 and
        silence_ratio < 0.4):
        return "犹豫", 0.70

    # 简短：极短语音
    if duration < 1000:
        return "简短", 0.65

    # 兴奋：音量中等偏高 + 语速快 + 频谱高
    if (rms > 5000 and
        speech_rate > 3 and
        spectral_centroid > 0.3):
        return "兴奋", 0.70

    # 平静：其他情况
    return "平静", 0.50


