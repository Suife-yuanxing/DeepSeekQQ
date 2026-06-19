"""作息状态机 — 根据时间决定 bot 的行为模式。

林念念：21岁女大学生作息，不是严格规律，有随机性。
每天生成随机偏移量，模拟真人作息波动。

真人化 P1-1：get_schedule_state() 接受可选 session_id，将状态同步到 CausalContext。
"""
import random
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta


# ============================================================
# 每日随机偏移量（每天0点刷新）
# ============================================================

_SLEEP_OFFSET_RANGE = (-45, 45)      # 分钟，睡觉时间偏移
_WAKE_OFFSET_RANGE = (-30, 30)       # 分钟，起床时间偏移
_SKIP_CLASS_CHANCE = 0.05            # 工作日9-11点逃课概率
_LATE_NIGHT_CHANCE = 0.10            # 凌晨1点后还不睡概率
_WEEKEND_LATE_SLEEP_CHANCE = 0.50    # 周末晚睡1-2小时概率

_daily_offset_cache: dict = {}
_offset_date: str = ""  # "YYYY-MM-DD"

# 真人化 P1-3：注册初始状态到全局状态表
try:
    from .global_state import register as _gs_register
    from .global_state import register_snapshot as _gs_snapshot
    _gs_register("schedule._daily_offset_cache", {}, namespace="schedule")
    _gs_register("schedule._offset_date", "", namespace="schedule")
except ImportError:
    _gs_register = None
    _gs_snapshot = None


def _ensure_daily_offsets():
    """确保当天偏移量已生成（每天0点刷新）。"""
    global _daily_offset_cache, _offset_date
    today = datetime.now().strftime("%Y-%m-%d")
    if _offset_date == today:
        return
    _daily_offset_cache = {
        "sleep": random.randint(*_SLEEP_OFFSET_RANGE),
        "wake": random.randint(*_WAKE_OFFSET_RANGE),
        "weekend_late": random.random() < _WEEKEND_LATE_SLEEP_CHANCE,
        "weekend_late_minutes": random.randint(60, 120),
        "skip_class": random.random() < _SKIP_CLASS_CHANCE,
        "late_night": random.random() < _LATE_NIGHT_CHANCE,
    }
    _offset_date = today
    # 真人化 P1-3：更新注册表中的引用
    if _gs_snapshot:
        _gs_snapshot("schedule._daily_offset_cache", _daily_offset_cache, {}, namespace="schedule")
        _gs_snapshot("schedule._offset_date", _offset_date, "", namespace="schedule")


def _get_virtual_now() -> datetime:
    """获取带随机偏移的虚拟时间，用于作息判断。

    用虚拟时间做所有时间段判断，保证同一天内一致性。
    """
    _ensure_daily_offsets()
    now = datetime.now()
    return now


# ============================================================
# 数据结构
# ============================================================

@dataclass
class ScheduleState:
    period: str           # sleeping/waking/active/meal/lazy/night_owl/skip_class
    energy: float         # 0.0~1.0 精力值
    reply_speed: float    # 回复速度系数 0.5~1.5
    verbosity: str        # minimal/normal/chatty
    description: str      # 人类可读描述（注入 prompt）


def get_schedule_state(
    hour: int = None,
    weekday: int = None,
    session_id: str = "",
) -> ScheduleState:
    """返回当前时段的 bot 行为状态。

    林念念作息特点（大三学生）：
    - 喜欢熬夜刷手机/追番，周末更放肆
    - 早上靠闹钟挣扎起床
    - 吃饭时间不固定，看课表
    - 晚上没课的时候精力最好
    - 下午有课或在图书馆赶作业
    - 每天有随机偏移量（±45分钟睡觉、±30分钟起床）
    - 偶尔逃课、偶尔深夜不睡

    真人化 P1-1：当提供 session_id 时，将状态写入 CausalContext。
    """
    _ensure_daily_offsets()
    now = datetime.now()
    hour = hour if hour is not None else now.hour
    weekday = weekday if weekday is not None else now.weekday()
    is_weekend = weekday >= 5
    offsets = _daily_offset_cache

    # 计算睡眠偏移后的虚拟小时（用于睡眠判断）
    sleep_offset_hours = offsets["sleep"] / 60.0  # ±0.75小时
    wake_offset_hours = offsets["wake"] / 60.0    # ±0.5小时

    # 判断是否处于偏移后的睡眠时间
    sleep_start = 1 + sleep_offset_hours  # 凌晨1点 ±0.75h
    sleep_end = 7

    # 周末睡眠可以更晚
    if is_weekend:
        sleep_start = 2 + sleep_offset_hours
        if offsets["weekend_late"]:
            late_hours = offsets["weekend_late_minutes"] / 60.0
            sleep_start += late_hours

    state = None

    # 阶段1: 深夜不睡（仅当 late_night=True 且在0-2点之间，覆盖正常睡眠）
    if offsets["late_night"] and 0 <= hour < 2:
        state = ScheduleState("night_owl", 0.3, 0.6, "normal",
            "深夜了还不困，在刷手机追番，偶尔回消息")

    # 阶段2: 睡眠态（在偏移后的睡眠时间内）
    if state is None:
        in_sleep = False
        if is_weekend:
            # 周末：睡觉时间基于偏移后的 sleep_start
            sleep_start_int = int(sleep_start)
            if sleep_start_int <= hour < 8:
                in_sleep = True
            elif hour >= 0 and hour < min(sleep_start_int, 8):
                in_sleep = True
        else:
            # Bug 6 修复：工作日也使用偏移后的 sleep_start（原为硬编码 1<=hour<8）
            if sleep_start <= hour < sleep_end:
                in_sleep = True
        if in_sleep:
            state = ScheduleState("sleeping", 0.1, 0.4, "minimal",
                "你在睡觉，被吵醒了，回复极度简短迷糊，可能带哈欠")

    # 阶段3: 起床时间（偏移后）
    if state is None:
        if is_weekend:
            base_wake = 10 + wake_offset_hours
            if offsets["weekend_late"]:
                base_wake += offsets["weekend_late_minutes"] / 60.0
        else:
            base_wake = 8 + wake_offset_hours
        wake_int = int(base_wake)
        if wake_int <= hour < wake_int + 1:
            state = ScheduleState("waking", 0.3, 0.6, "minimal",
                "你刚醒，可能在赶去上课的路上，回复慢且短，偶尔吐槽早起")

    # 起床后的间隙（wake_int+1 到 9点）
    if state is None:
        if is_weekend:
            base_wake = 10 + wake_offset_hours
            if offsets["weekend_late"]:
                base_wake += offsets["weekend_late_minutes"] / 60.0
        else:
            base_wake = 8 + wake_offset_hours
        wake_int = int(base_wake)
        if wake_int + 1 <= hour < 9:
            if is_weekend and offsets["weekend_late"]:
                state = ScheduleState("waking", 0.4, 0.65, "minimal",
                    "周末赖床中，半梦半醒地刷手机")
            else:
                state = ScheduleState("waking", 0.4, 0.65, "minimal",
                    "刚起床没多久，还有点迷糊")

    # 阶段4: 逃课！（工作日9-11点有5%概率）
    if state is None:
        if not is_weekend and offsets["skip_class"] and 9 <= hour < 12:
            state = ScheduleState("lazy", 0.5, 0.7, "normal",
                "今天不想上课，躲在宿舍里刷手机打发时间")

    # 阶段5: 上午 9:00-12:00: 上课/摸鱼
    if state is None and 9 <= hour < 12:
        state = ScheduleState("active", 0.7, 0.9, "normal",
            "上午，可能在教室摸鱼，能回消息但不会太长")

    # 阶段6: 午饭 12:00-13:00
    if state is None and 12 <= hour < 13:
        state = ScheduleState("meal", 0.6, 0.8, "minimal",
            "你在吃午饭，可能顺便刷手机回两句")

    # 阶段7: 午后 13:00-14:00: 午休/摸鱼
    if state is None and 13 <= hour < 14:
        state = ScheduleState("lazy", 0.4, 0.7, "minimal",
            "午休时间，有点困，懒洋洋地刷手机")

    # 阶段8: 下午 14:00-18:00: 上课/图书馆/小组作业
    if state is None and 14 <= hour < 18:
        state = ScheduleState("active", 0.7, 1.0, "normal",
            "下午，可能在图书馆或上课，偶尔分心回消息")

    # 阶段9: 晚饭 18:00-19:00
    if state is None and 18 <= hour < 19:
        state = ScheduleState("meal", 0.5, 0.8, "minimal",
            "你在吃晚饭")

    # 阶段10: 晚间自由时间 19:00-23:00: 精力最好
    if state is None and 19 <= hour < 23:
        state = ScheduleState("active", 0.9, 1.1, "chatty",
            "晚间自由时间，精力充沛，话多，可以深聊")

    # 阶段11: 深夜 23:00-0:00: 床上刷手机
    if state is None and 23 <= hour < 24:
        state = ScheduleState("night_owl", 0.5, 0.8, "normal",
            "深夜，在床上刷手机，回复变慢变慵懒")

    # 阶段12: 凌晨 0:00-1:00: 该睡了但还在撑
    if state is None:
        state = ScheduleState("night_owl", 0.2, 0.5, "minimal",
            "该睡了但还在刷手机，极度慵懒，可能会催对方也去睡")

    # 真人化 P1-1：同步到 CausalContext
    if session_id:
        _sync_to_causal_context(state, session_id)

    return state


def _sync_to_causal_context(state: ScheduleState, session_id: str) -> None:
    """真人化 P1-1：将 schedule 状态同步到 CausalContext。"""
    try:
        from .causal_context import get_cc
        cc = get_cc(session_id)
        cc.update_body_state(
            energy=state.energy,
            tiredness=1.0 - state.energy,
            schedule_period=state.period,
        )
    except Exception:
        pass  # CausalContext 不可用时静默降级
