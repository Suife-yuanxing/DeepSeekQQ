"""群聊热度状态机 — 智能判断Bot是否应该插话分享feed内容。

核心借鉴: nonebot-plugin-wtfllm 的 Heat State Machine

原理：
- 用半衰期公式计算群聊活跃度
- 状态流转: IDLE → COLD → WARM → ACTIVE → FLOOD
- 不同状态下Bot插话分享feed的概率不同
- 私聊场景也有简化版热度判断（对话节奏）
"""
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Dict
from typing import Optional
from typing import Tuple

from nonebot import logger


# ============================================================
# 状态定义
# ============================================================

class HeatState(Enum):
    IDLE = "idle"         # 空闲（>30s无消息），可主动推送
    COLD = "cold"         # 冷场（热度<0.5），feed破冰
    WARM = "warm"         # 温吞（0.5-2.0），偶尔提feed
    ACTIVE = "active"     # 活跃（2.0-5.0），少提feed
    FLOOD = "flood"       # 刷屏（>5.0），不提feed


# 各状态的feed分享策略
_INTERJECTION_STRATEGIES = {
    HeatState.IDLE:   {"probability": 0.30, "max_feed_refs": 1, "tone": "casual_share"},
    HeatState.COLD:   {"probability": 0.25, "max_feed_refs": 1, "tone": "icebreaker"},
    HeatState.WARM:   {"probability": 0.12, "max_feed_refs": 1, "tone": "casual"},
    HeatState.ACTIVE: {"probability": 0.06, "max_feed_refs": 1, "tone": "minimal"},
    HeatState.FLOOD:  {"probability": 0.0,  "max_feed_refs": 0, "tone": "silent"},
}

# ============================================================
# 热度引擎核心
# ============================================================

# 半衰期（秒）：每条消息的影响力经过此时间减半
HEAT_HALF_LIFE = 300  # 5分钟

# 状态阈值
HEAT_FLOOD_THRESHOLD = 5.0     # 刷屏
HEAT_ACTIVE_THRESHOLD = 2.0    # 活跃
HEAT_WARM_THRESHOLD = 0.5      # 温吞
# 低于0.5为COLD

# 空闲超时（秒）
IDLE_TIMEOUT = 30  # 30秒无消息→IDLE

# 每条消息的基础热度增量
BASE_HEAT_INCREMENT = 1.0

# EMA平滑系数（热度变化速度）
HEAT_VELOCITY_ALPHA = 0.3

# ============================================================
# 状态存储
# ============================================================

@dataclass
class _HeatTracker:
    """单个群聊/私聊的热度追踪器。"""
    heat: float = 0.0
    last_message_time: float = 0.0
    message_count: int = 0
    heat_velocity: float = 0.0  # 热度变化速度（EMA）

    def decay(self, now: float) -> float:
        """计算指数衰减后的热度值。"""
        if self.last_message_time <= 0:
            return 0.0
        elapsed = now - self.last_message_time
        if elapsed <= 0:
            return self.heat
        # 半衰期衰减: heat * 0.5^(elapsed/half_life)
        return self.heat * (0.5 ** (elapsed / HEAT_HALF_LIFE))


# 群聊热度追踪: group_id → _HeatTracker
_group_heat: Dict[str, _HeatTracker] = defaultdict(_HeatTracker)

# 私聊热度追踪: user_id → _HeatTracker
_private_heat: Dict[str, _HeatTracker] = defaultdict(_HeatTracker)


# ============================================================
# 公共API
# ============================================================

def update_heat(
    chat_id: str,
    is_group: bool = False,
) -> HeatState:
    """记录一条新消息，更新并返回当前热度状态。

    每次收到消息时调用。

    Args:
        chat_id: 群ID或用户ID
        is_group: 是否群聊

    Returns:
        当前热度状态
    """
    store = _group_heat if is_group else _private_heat
    tracker = store[chat_id]
    now = time.time()

    # 衰减当前热度
    decayed = tracker.decay(now)

    # 增加新消息热度
    tracker.heat = decayed + BASE_HEAT_INCREMENT

    # 更新热度变化速度（EMA）
    delta = tracker.heat - decayed
    tracker.heat_velocity = (
        HEAT_VELOCITY_ALPHA * delta
        + (1 - HEAT_VELOCITY_ALPHA) * tracker.heat_velocity
    )

    tracker.last_message_time = now
    tracker.message_count += 1

    return _classify_state(tracker, now)


def get_heat_state(chat_id: str, is_group: bool = False) -> HeatState:
    """获取当前热度状态（不更新，只查询）。

    用于在pipeline stage中判断是否该插话。
    """
    store = _group_heat if is_group else _private_heat
    tracker = store.get(chat_id)
    if tracker is None:
        return HeatState.IDLE
    return _classify_state(tracker, time.time())


def should_interject(
    chat_id: str,
    is_group: bool = False,
    has_feed_content: bool = False,
) -> Tuple[bool, Optional[dict]]:
    """判断是否应该插话分享feed内容。

    Args:
        chat_id: 群ID或用户ID
        is_group: 是否群聊
        has_feed_content: feed中是否有新鲜内容

    Returns:
        (should_interject, strategy_dict or None)
    """
    if not has_feed_content:
        return False, None

    state = get_heat_state(chat_id, is_group)
    strategy = _INTERJECTION_STRATEGIES.get(state, {"probability": 0.0, "max_feed_refs": 0, "tone": "silent"})

    import random
    if random.random() < strategy["probability"]:
        logger.info(f"[热度] {chat_id} 状态={state.value} 触发feed插话 (p={strategy['probability']})")
        return True, strategy

    return False, None


def get_interjection_strategy(chat_id: str, is_group: bool = False) -> dict:
    """获取当前插话策略（供behavior_engine参考）。"""
    state = get_heat_state(chat_id, is_group)
    return _INTERJECTION_STRATEGIES.get(
        state,
        {"probability": 0.0, "max_feed_refs": 0, "tone": "silent"},
    )


def get_group_heat_description(chat_id: str) -> str:
    """获取群聊热度的人类可读描述（供prompt注入）。

    用于让LLM感知当前群聊氛围，调整说话策略。
    """
    state = get_heat_state(chat_id, is_group=True)
    descriptions = {
        HeatState.IDLE: "群里很安静，大家都没说话。你可以主动说点什么打破沉默。",
        HeatState.COLD: "群里有点冷清，偶尔有人说一两句。可以适当参与。",
        HeatState.WARM: "群里氛围轻松，大家在闲聊。你可以自然地加入。",
        HeatState.ACTIVE: "群里聊得热火朝天，很多人都在说话。你可以挑感兴趣的插一句。",
        HeatState.FLOOD: "群里刷屏了，消息太快。你尽量简洁，或者等刷屏过去再说。",
    }
    return descriptions.get(state, "")


# ============================================================
# 内部函数
# ============================================================

def _classify_state(tracker: _HeatTracker, now: float) -> HeatState:
    """根据热度值分类状态。"""
    # 先检查空闲
    elapsed = now - tracker.last_message_time
    if tracker.last_message_time > 0 and elapsed > IDLE_TIMEOUT:
        return HeatState.IDLE

    # 衰减后分类
    heat = tracker.decay(now)

    if heat > HEAT_FLOOD_THRESHOLD:
        return HeatState.FLOOD
    elif heat > HEAT_ACTIVE_THRESHOLD:
        return HeatState.ACTIVE
    elif heat > HEAT_WARM_THRESHOLD:
        return HeatState.WARM
    else:
        return HeatState.COLD


# ============================================================
# 清理
# ============================================================

def cleanup_stale_trackers(max_age_seconds: int = 3600):
    """清理超时的追踪器（>1h无消息）。"""
    now = time.time()
    for store in [_group_heat, _private_heat]:
        stale = [
            k for k, v in store.items()
            if now - v.last_message_time > max_age_seconds
        ]
        for k in stale:
            del store[k]
    if stale:
        logger.debug(f"[热度] 清理 {len(stale)} 个过期追踪器")


def reset_heat(chat_id: str, is_group: bool = False):
    """重置指定聊天热度（用于测试）。"""
    store = _group_heat if is_group else _private_heat
    store.pop(chat_id, None)
