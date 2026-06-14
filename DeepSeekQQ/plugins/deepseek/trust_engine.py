"""TrustEngine：多维信任分计算，归一化后加权。

P0-2: 权限连续体 — 替换二元 owner/non-owner 为 5 级信任体系。

修复量纲问题：各维度先归一化到 [0, 1]，再加权求和。
原方案直接加权导致好感度主导（好感度 0~1000 vs 消息量 log 0~7）。

信任分级：
- STRANGER (0-20):      基础聊天
- ACQUAINTANCE (20-40): 搜索、图片识别
- FRIEND (40-70):       主动消息、音乐、图片生成
- CLOSE (70-90):        查看手机屏幕、服务器状态
- OWNER (90+):          手机操控、管理命令
"""
from __future__ import annotations
import math
from enum import IntEnum
from typing import Optional


class TrustTier(IntEnum):
    STRANGER = 0
    ACQUAINTANCE = 20
    FRIEND = 40
    CLOSE = 70
    OWNER = 90


# 各维度归一化上限（可在 config.py 中覆盖）
NORM_CONFIG = {
    "affection_max": 500,    # 好感度 500 分视为满分（超出截断）
    "messages_max": 5000,    # 消息量 5000 条视为满分
    "days_max": 365,         # 账龄 365 天视为满分
    "behavior_max": 100,     # 行为分百分制
}

# 权重（各项归一化后，权重之和 = 1）
WEIGHTS = {
    "affection": 0.35,
    "messages": 0.20,
    "days": 0.10,
    "behavior": 0.25,
    "explicit": 0.10,
}


def _norm(value: float, max_val: float) -> float:
    """归一化到 [0, 1]，超出上限截断。"""
    if max_val <= 0:
        return 0.0
    return max(0.0, min(1.0, value / max_val))


def calculate_trust_score(
    affection_score: float,
    message_count: int,
    account_days: int,
    behavior_score: float,     # 0~100，需在 DB 中以百分制维护
    explicit_grant: bool = False,
    is_owner: bool = False,
) -> float:
    """计算信任分，返回 0~100 的浮点数。

    Args:
        affection_score: 好感度原始分（0~1000+）
        message_count: 累计消息数
        account_days: 账号创建天数
        behavior_score: 行为分（0~100 百分制，含正面行为和负面行为扣分）
        explicit_grant: 用户是否显式授权特定能力
        is_owner: 是否为 bot 主人

    Returns:
        0~100 的信任分
    """
    if is_owner:
        return 100.0

    cfg = NORM_CONFIG
    n_affection = _norm(affection_score, cfg["affection_max"])
    n_messages = _norm(math.log1p(message_count), math.log1p(cfg["messages_max"]))
    n_days = _norm(account_days, cfg["days_max"])
    n_behavior = _norm(behavior_score, cfg["behavior_max"])
    n_explicit = 1.0 if explicit_grant else 0.0

    w = WEIGHTS
    raw = (
        n_affection * w["affection"] +
        n_messages * w["messages"] +
        n_days * w["days"] +
        n_behavior * w["behavior"] +
        n_explicit * w["explicit"]
    )

    return round(raw * 100, 1)


def get_trust_tier(score: float) -> TrustTier:
    """根据分数返回信任层级。"""
    for tier in reversed(list(TrustTier)):
        if score >= tier.value:
            return tier
    return TrustTier.STRANGER


def check_permission(score: float, required_tier: TrustTier) -> bool:
    """检查用户是否拥有所需信任层级的权限。

    Usage:
        if check_permission(user_score, TrustTier.FRIEND):
            await send_proactive_message(...)
    """
    return get_trust_tier(score).value >= required_tier.value


def get_unlocked_abilities(score: float) -> list[str]:
    """返回当前信任分解锁的能力列表。"""
    tier = get_trust_tier(score)
    abilities = ["基础聊天"]

    if tier >= TrustTier.ACQUAINTANCE:
        abilities.extend(["搜索", "图片识别"])
    if tier >= TrustTier.FRIEND:
        abilities.extend(["主动消息", "音乐点播", "图片生成"])
    if tier >= TrustTier.CLOSE:
        abilities.extend(["查看手机屏幕", "服务器状态"])
    if tier >= TrustTier.OWNER:
        abilities.extend(["手机操控", "管理命令"])

    return abilities
