"""群聊热度状态机 — 基于 WTFLLM 设计。

群聊消息频率动态调节 Bot 活跃度：
- 每条消息 +1.0 热度，@Bot +3.0
- EMA 平滑 (α=0.3)，半衰期 300 秒
- 激活阈值 2.0 → 活跃（可以插话）
- 去激活阈值 0.5 → 回到空闲
- 空闲超时 30s → 休眠

状态转换:
  休眠 --[新消息]--> 空闲 --[热度≥2.0]--> 活跃
  活跃 --[热度<0.5]--> 空闲 --[30s无消息]--> 休眠
"""

import asyncio
import math
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import Optional

from nonebot import logger

# ============================================================
# 状态定义
# ============================================================

class HeatState:
    DORMANT = "dormant"     # 休眠：群聊不活跃，不插话
    IDLE = "idle"           # 空闲：偶尔有消息，仅响应 @/关键词
    ACTIVE = "active"       # 活跃：热烈讨论中，可以主动插话


# ============================================================
# 配置
# ============================================================

# EMA 平滑系数
HEAT_ALPHA = 0.3
# 半衰期（秒）
HEAT_HALF_LIFE = 300
# 激活阈值
HEAT_ACTIVATION = 2.0
# 去激活阈值
HEAT_DEACTIVATION = 0.5
# 空闲超时（秒）
IDLE_TIMEOUT = 30
# 每条消息基础加热量
HEAT_PER_MESSAGE = 1.0
# @Bot 额外加热量
HEAT_AT_BOT = 3.0
# 最大群缓存数
MAX_GROUP_CACHE = 50


@dataclass
class GroupHeat:
    """单个群的热度状态。"""
    group_id: str
    heat: float = 0.0
    state: str = HeatState.DORMANT
    last_message_time: float = 0.0
    last_state_change: float = field(default_factory=time.time)
    message_count: int = 0  # 当前周期消息计数

    def update(self, now: float) -> None:
        """基于时间衰减热度。"""
        elapsed = now - self.last_message_time
        if elapsed > 0:
            # 半衰期衰减
            half_lives = elapsed / HEAT_HALF_LIFE
            self.heat *= (0.5 ** half_lives)
            # 防止极小值
            if self.heat < 0.01:
                self.heat = 0.0

    def add_heat(self, amount: float, now: float) -> None:
        """添加热度（EMA 平滑）。"""
        self.update(now)
        # EMA: new = α × raw + (1-α) × old
        self.heat = HEAT_ALPHA * amount + (1 - HEAT_ALPHA) * self.heat
        self.last_message_time = now
        self.message_count += 1

    def check_state(self, now: float) -> str:
        """检查并更新状态。"""
        old_state = self.state

        if self.state == HeatState.DORMANT:
            if self.heat >= HEAT_ACTIVATION:
                self.state = HeatState.ACTIVE
            elif self.heat > 0:
                self.state = HeatState.IDLE

        elif self.state == HeatState.IDLE:
            if self.heat >= HEAT_ACTIVATION:
                self.state = HeatState.ACTIVE
            elif self.heat < HEAT_DEACTIVATION:
                idle_duration = now - self.last_message_time
                if idle_duration >= IDLE_TIMEOUT:
                    self.state = HeatState.DORMANT

        elif self.state == HeatState.ACTIVE:
            if self.heat < HEAT_DEACTIVATION:
                self.state = HeatState.IDLE
            # 长时间没消息直接回休眠
            idle_duration = now - self.last_message_time
            if idle_duration >= IDLE_TIMEOUT * 2:
                self.state = HeatState.DORMANT

        if old_state != self.state:
            self.last_state_change = now
            self.message_count = 0  # 重置周期计数
            logger.info(
                f"[群热度] group={self.group_id} {old_state} → {self.state} "
                f"(heat={self.heat:.2f})"
            )

        return self.state


class GroupHeatManager:
    """群聊热度管理器（全局单例）。"""

    def __init__(self):
        self._groups: Dict[str, GroupHeat] = {}
        self._lock = asyncio.Lock()

    async def on_message(self, group_id: str, is_at_bot: bool = False) -> str:
        """收到群消息时调用。返回当前状态。"""
        now = time.time()
        async with self._lock:
            if group_id not in self._groups:
                self._groups[group_id] = GroupHeat(group_id=group_id)

            gh = self._groups[group_id]
            amount = HEAT_PER_MESSAGE + (HEAT_AT_BOT if is_at_bot else 0)
            gh.add_heat(amount, now)
            new_state = gh.check_state(now)
            self._cleanup()
            return new_state

    def get_state(self, group_id: str) -> str:
        """获取群当前热度状态。"""
        gh = self._groups.get(group_id)
        if gh:
            gh.check_state(time.time())
            return gh.state
        return HeatState.DORMANT

    def get_heat(self, group_id: str) -> float:
        """获取群当前热度值。"""
        gh = self._groups.get(group_id)
        if gh:
            gh.update(time.time())
            return gh.heat
        return 0.0

    def should_interject(self, group_id: str) -> bool:
        """判断 Bot 是否应该主动插话。

        仅在 ACTIVE 状态且热度足够时返回 True。
        随机概率与热度正相关。
        """
        import random
        state = self.get_state(group_id)
        if state != HeatState.ACTIVE:
            return False
        heat = self.get_heat(group_id)
        # 热度越高，插话概率越大（最高 40%）
        probability = min(0.4, heat / 10.0)
        return random.random() < probability

    def get_activity_description(self, group_id: str) -> str:
        """获取群活跃度描述（供 prompt 注入）。"""
        state = self.get_state(group_id)
        heat = self.get_heat(group_id)
        if state == HeatState.DORMANT:
            return "群里很安静，偶尔有人说一两句话。"
        elif state == HeatState.IDLE:
            return "群里偶尔有人聊天，节奏不紧不慢。"
        else:
            if heat > 5.0:
                return "群里聊得很热烈，大家都在积极发言。"
            else:
                return "群里正在活跃聊天，可以自然地加入话题。"

    def _cleanup(self) -> None:
        """清理不活跃的群的缓存。"""
        if len(self._groups) <= MAX_GROUP_CACHE:
            return
        now = time.time()
        # 按最后消息时间排序，移除最久远的
        sorted_groups = sorted(
            self._groups.items(),
            key=lambda x: x[1].last_message_time
        )
        excess = len(self._groups) - MAX_GROUP_CACHE
        for group_id, gh in sorted_groups[:excess]:
            if now - gh.last_message_time > 3600:  # 1小时以上不活跃
                del self._groups[group_id]


# 全局单例
heat_manager = GroupHeatManager()
