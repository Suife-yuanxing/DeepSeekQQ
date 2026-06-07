"""作息状态机 — 根据时间决定 bot 的行为模式。

猫娘人设：年轻女生作息，不是严格规律，有随机性。
"""
import random
from datetime import datetime
from dataclasses import dataclass


@dataclass
class ScheduleState:
    period: str           # sleeping/waking/active/meal/lazy/night_owl
    energy: float         # 0.0~1.0 精力值
    reply_speed: float    # 回复速度系数 0.5~1.5
    verbosity: str        # minimal/normal/chatty
    description: str      # 人类可读描述（注入 prompt）


def get_schedule_state(hour: int = None, weekday: int = None) -> ScheduleState:
    """返回当前时段的 bot 行为状态。

    猫娘作息特点：
    - 喜欢熬夜，但不会太晚
    - 早上有点赖床
    - 吃饭时间比较固定
    - 晚上精力最好
    """
    now = datetime.now()
    hour = hour if hour is not None else now.hour
    weekday = weekday if weekday is not None else now.weekday()
    is_weekend = weekday >= 5

    # 凌晨 1:00-7:00: 睡眠态（周末可以晚一点）
    sleep_start = 2 if is_weekend else 1
    if sleep_start <= hour < 7:
        return ScheduleState("sleeping", 0.1, 0.4, "minimal",
            "你在睡觉，被吵醒了，回复极度简短迷糊，可能带哈欠")

    # 早起 7:00-8:00: 刚醒（周末更晚）
    wake_time = 9 if is_weekend else 7
    if wake_time <= hour < wake_time + 1:
        return ScheduleState("waking", 0.3, 0.6, "minimal",
            "你刚醒来，有点迷糊，回复慢且短，偶尔打哈欠")

    # 早餐 8:00-9:00（周末跳过）
    if not is_weekend and 8 <= hour < 9:
        return ScheduleState("meal", 0.5, 0.7, "minimal",
            "你在吃早饭，偶尔看手机回一句，不会长篇大论")

    # 上午活跃 9:00-12:00
    if 9 <= hour < 12:
        return ScheduleState("active", 0.8, 1.0, "normal",
            "上午精力充沛，正常聊天")

    # 午饭 12:00-13:00
    if 12 <= hour < 13:
        return ScheduleState("meal", 0.6, 0.8, "minimal",
            "你在吃午饭，回复偏短，可能会说去吃饭了")

    # 午后 13:00-14:00: 犯困
    if 13 <= hour < 14:
        return ScheduleState("lazy", 0.4, 0.7, "minimal",
            "午后犯困，有点懒洋洋的，回复慢且短")

    # 下午 14:00-18:00
    if 14 <= hour < 18:
        return ScheduleState("active", 0.7, 1.0, "normal",
            "下午正常状态")

    # 晚饭 18:00-19:00
    if 18 <= hour < 19:
        return ScheduleState("meal", 0.5, 0.8, "minimal",
            "你在吃晚饭")

    # 晚间活跃 19:00-22:00: 精力最好
    if 19 <= hour < 22:
        return ScheduleState("active", 0.9, 1.1, "chatty",
            "晚间精力最好，话多一点，可以聊深一点")

    # 深夜 22:00-0:00: 开始犯困
    if 22 <= hour < 24:
        return ScheduleState("night_owl", 0.5, 0.8, "normal",
            "深夜了，有点困但还在撑，回复变慢变短")

    # 凌晨 0:00-1:00: 该睡了
    return ScheduleState("night_owl", 0.2, 0.5, "minimal",
        "该睡了，极度慵懒，催用户也去睡")
