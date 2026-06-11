"""作息状态机 — 根据时间决定 bot 的行为模式。

林念念：21岁女大学生作息，不是严格规律，有随机性。
"""
import random
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ScheduleState:
    period: str           # sleeping/waking/active/meal/lazy/night_owl
    energy: float         # 0.0~1.0 精力值
    reply_speed: float    # 回复速度系数 0.5~1.5
    verbosity: str        # minimal/normal/chatty
    description: str      # 人类可读描述（注入 prompt）


def get_schedule_state(hour: int = None, weekday: int = None) -> ScheduleState:
    """返回当前时段的 bot 行为状态。

    林念念作息特点（大三学生）：
    - 喜欢熬夜刷手机/追番，周末更放肆
    - 早上靠闹钟挣扎起床
    - 吃饭时间不固定，看课表
    - 晚上没课的时候精力最好
    - 下午有课或在图书馆赶作业
    """
    now = datetime.now()
    hour = hour if hour is not None else now.hour
    weekday = weekday if weekday is not None else now.weekday()
    is_weekend = weekday >= 5

    # 凌晨 1:00-8:00: 睡眠态（周末可以更晚）
    sleep_start = 2 if is_weekend else 1
    if sleep_start <= hour < 8:
        return ScheduleState("sleeping", 0.1, 0.4, "minimal",
            "你在睡觉，被吵醒了，回复极度简短迷糊，可能带哈欠")

    # 早起 8:00-9:00: 挣扎起床赶课（周末更晚）
    wake_time = 10 if is_weekend else 8
    if wake_time <= hour < wake_time + 1:
        return ScheduleState("waking", 0.3, 0.6, "minimal",
            "你刚醒，可能在赶去上课的路上，回复慢且短，偶尔吐槽早起")

    # 上午 9:00-12:00: 上课/摸鱼
    if 9 <= hour < 12:
        return ScheduleState("active", 0.7, 0.9, "normal",
            "上午，可能在教室摸鱼，能回消息但不会太长")

    # 午饭 12:00-13:00
    if 12 <= hour < 13:
        return ScheduleState("meal", 0.6, 0.8, "minimal",
            "你在吃午饭，可能顺便刷手机回两句")

    # 午后 13:00-14:00: 午休/摸鱼
    if 13 <= hour < 14:
        return ScheduleState("lazy", 0.4, 0.7, "minimal",
            "午休时间，有点困，懒洋洋地刷手机")

    # 下午 14:00-18:00: 上课/图书馆/小组作业
    if 14 <= hour < 18:
        return ScheduleState("active", 0.7, 1.0, "normal",
            "下午，可能在图书馆或上课，偶尔分心回消息")

    # 晚饭 18:00-19:00
    if 18 <= hour < 19:
        return ScheduleState("meal", 0.5, 0.8, "minimal",
            "你在吃晚饭")

    # 晚间自由时间 19:00-23:00: 精力最好（自由时间！）
    if 19 <= hour < 23:
        return ScheduleState("active", 0.9, 1.1, "chatty",
            "晚间自由时间，精力充沛，话多，可以深聊")

    # 深夜 23:00-0:00: 床上刷手机
    if 23 <= hour < 24:
        return ScheduleState("night_owl", 0.5, 0.8, "normal",
            "深夜，在床上刷手机，回复变慢变慵懒")

    # 凌晨 0:00-1:00: 该睡了但还在撑
    return ScheduleState("night_owl", 0.2, 0.5, "minimal",
        "该睡了但还在刷手机，极度慵懒，可能会催对方也去睡")
