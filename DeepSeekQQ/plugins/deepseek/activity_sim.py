"""活动模拟器 — bot 有持续的"当前活动"状态，可被用户询问。

根据时段自动切换活动，维持到下一个时段。
用户问"在干嘛"时能真实回答。
"""
import random
import time
from dataclasses import dataclass
from typing import Optional


# ============================================================
# 活动数据结构
# ============================================================

@dataclass
class Activity:
    name: str               # "在图书馆自习"
    action: str             # "翻书/写笔记"
    emoji: str              # "📚"
    can_interrupt: bool = True


# ============================================================
# 按时段组织的活动池（权重百分比）
# ============================================================

ACTIVITY_POOLS: dict = {
    "morning": [   # 9:00-11:50
        (Activity("上课摸鱼", "偷偷看手机", "📱", True), 30),
        (Activity("在图书馆自习", "翻书写笔记", "📚", True), 30),
        (Activity("在宿舍躺着", "刷手机赖床", "🛏️", True), 20),
        (Activity("在食堂吃早饭", "啃包子喝豆浆", "🥟", True), 10),
        (Activity("操场晨跑", "喘着气跑步", "🏃", False), 5),
        (Activity("赶作业", "狂写作业中", "✍️", True), 5),
    ],
    "noon": [      # 12:00-13:00
        (Activity("在食堂吃饭", "排队打饭", "🍚", True), 60),
        (Activity("拿外卖", "等外卖小哥", "🛵", True), 20),
        (Activity("在宿舍吃泡面", "懒得去食堂", "🍜", True), 20),
    ],
    "afternoon": [ # 14:00-17:00
        (Activity("在图书馆自习", "备战期末", "📚", True), 40),
        (Activity("上课中", "听老师讲课", "📝", True), 20),
        (Activity("在宿舍打游戏", "开着电脑打游戏", "🎮", True), 20),
        (Activity("小组讨论", "和组员争论方案", "👥", False), 10),
        (Activity("在咖啡店", "喝着奶茶发呆", "🧋", True), 10),
    ],
    "dinner": [    # 17:00-18:00
        (Activity("在食堂吃晚饭", "纠结吃什么", "🍲", True), 40),
        (Activity("操场散步", "戴着耳机溜达", "🚶", True), 20),
        (Activity("在宿舍休息", "躺着刷手机", "📱", True), 20),
        (Activity("出去吃", "和室友出去觅食", "🍕", True), 20),
    ],
    "evening": [   # 19:00-22:00
        (Activity("在追番", "窝在宿舍看番剧", "📺", True), 30),
        (Activity("打游戏", "打着LOL/原神", "🎮", True), 30),
        (Activity("刷手机", "躺着刷视频", "📱", True), 20),
        (Activity("和室友聊天", "宿舍夜聊中", "💬", True), 20),
    ],
    "night": [     # 23:00+
        (Activity("躺在床上刷手机", "睡前刷会手机", "📱", True), 60),
        (Activity("准备睡觉", "洗漱完准备睡", "😴", True), 30),
        (Activity("失眠了", "翻来覆去睡不着", "😣", True), 10),
    ],
}

# ============================================================
# 时间段映射
# ============================================================

_HOUR_TO_SLOT: dict = {}
_HOUR_TO_SLOT.update({h: "morning" for h in range(9, 12)})
_HOUR_TO_SLOT.update({h: "noon" for h in range(12, 14)})
_HOUR_TO_SLOT.update({h: "afternoon" for h in range(14, 17)})
_HOUR_TO_SLOT.update({17: "dinner", 18: "dinner"})
_HOUR_TO_SLOT.update({h: "evening" for h in range(19, 23)})
_HOUR_TO_SLOT.update({h: "night" for h in range(23, 24)})
_HOUR_TO_SLOT.update({h: "night" for h in range(0, 9)})


# ============================================================
# 当前活动缓存
# ============================================================

_current_activity: Optional[Activity] = None
_current_slot: str = ""
_activity_updated: float = 0  # 预留：活动最后更新时间（当前仅赋值未读取，后续可做稳定性判断）


def get_current_activity(session_id: str = "") -> Activity:
    """获取当前时段的活动，时段切换时重新随机选择。

    真人化 P1-1：当提供 session_id 时，将活动状态同步到 CausalContext。
    """
    global _current_activity, _current_slot, _activity_updated
    from datetime import datetime
    now = datetime.now()
    slot = _HOUR_TO_SLOT.get(now.hour, "evening")

    if slot != _current_slot or _current_activity is None:
        pool = ACTIVITY_POOLS.get(slot, ACTIVITY_POOLS["evening"])
        activities, weights = zip(*pool)
        _current_activity = random.choices(activities, weights=weights, k=1)[0]
        _current_slot = slot
        _activity_updated = time.time()

    # 真人化 P1-1：同步到 CausalContext
    if session_id and _current_activity:
        _sync_activity_to_cc(session_id, _current_activity)

    return _current_activity


def _sync_activity_to_cc(session_id: str, activity: Activity) -> None:
    """将当前活动同步到 CausalContext。"""
    try:
        from .causal_context import get_cc
        cc = get_cc(session_id)
        # 计算活动强度
        intensity_map = {
            "上课摸鱼": 0.4, "在图书馆自习": 0.7, "在宿舍躺着": 0.2,
            "在食堂吃早饭": 0.3, "操场晨跑": 0.9, "赶作业": 0.8,
            "在食堂吃饭": 0.3, "拿外卖": 0.3, "在宿舍吃泡面": 0.2,
            "上课中": 0.8, "在宿舍打游戏": 0.7, "小组讨论": 0.9,
            "在咖啡店": 0.5, "在食堂吃晚饭": 0.3, "操场散步": 0.4,
            "在宿舍休息": 0.2, "出去吃": 0.4, "在追番": 0.6,
            "打游戏": 0.8, "刷手机": 0.2, "和室友聊天": 0.5,
            "躺在床上刷手机": 0.1, "准备睡觉": 0.1, "失眠了": 0.1,
        }
        intensity = intensity_map.get(activity.name, 0.5)
        cc.update_activity(
            activity=activity.name,
            intensity=intensity,
            can_interrupt=activity.can_interrupt,
        )
    except Exception:
        pass  # CausalContext 不可用时静默降级


def get_activity_hint(session_id: str = "") -> str:
    """生成活动提示词，注入系统 prompt。

    真人化 P1-1：当提供 session_id 时，从 CausalContext 读取活动。
    """
    act = get_current_activity(session_id)
    return f"你现在正在{act.name}，如果有人问你在干嘛就说{act.name}"


def get_natural_activity_mention(session_id: str = "") -> str:
    """偶尔在回复中自然带出当前活动（5%概率）。

    Returns: 空字符串或活动相关短句（不含emoji，因为可能被过滤）。
    """
    if random.random() > 0.05:
        return ""
    act = get_current_activity(session_id)
    mentions = [
        f"刚{act.action}，",
    ]
    return random.choice(mentions)


def get_doing_reply(session_id: str = "") -> str:
    """生成"在干嘛"的自然回答模板。

    Returns: 给LLM的上下文提示，用于自然回答。
    """
    act = get_current_activity(session_id)
    return f"在{act.name}（{act.action}）{act.emoji}"
