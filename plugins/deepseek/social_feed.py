"""社交信息流引擎 — 模拟真人"刷手机"的记忆。

功能：
- 模拟刷手机行为：从热搜/社交媒体获取内容，存入短期"feed记忆"
- Feed记忆衰减：随时间衰退（6h后 relevance<0.3，24h清除）
- 自然语言摘要：供prompt注入使用
- 引用去重：同一条内容不重复提（借鉴LocoAgent操作日志）
- 兴趣加权：匹配人设兴趣的内容概率提升

设计原则：
- 纯内存存储（OrderedDict），模拟真人短期记忆，不需要数据库
- 与 hot_topics.py 解耦：hot_topics 负责"获取"，social_feed 负责"记忆和消费"
- 由 behavior_engine 和 handler 调用，不影响已有流程
"""
import hashlib
import random
import time
from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional
from typing import Set

from nonebot import logger


# ============================================================
# 数据结构
# ============================================================

@dataclass
class FeedItem:
    """刷到的一条内容。"""
    content: str              # 内容摘要/标题
    source: str               # "抖音"/"B站"/"微博"/"小红书"/"小黑盒"
    url: str = ""             # 原始链接
    category: str = ""        # "搞笑"/"游戏"/"动漫"/"美食"/"八卦"/"科技"/"其他"
    seen_at: float = 0.0      # 刷到的时间戳
    relevance: float = 1.0    # 衰退后的相关性 (1.0→0)
    item_id: str = ""         # 去重ID (content+source的hash)

    def __post_init__(self):
        if not self.item_id:
            self.item_id = hashlib.md5(
                f"{self.content[:50]}|{self.source}".encode()
            ).hexdigest()[:12]
        if not self.seen_at:
            self.seen_at = time.time()


# ============================================================
# Feed 记忆存储（纯内存 LRU + TTL）
# ============================================================

# 有序字典：最近访问的在末尾
_feed_store: OrderedDict = OrderedDict()
_feed_max_items: int = 100

# 已提及/已忽略的去重集合（借鉴 LocoAgent operation log）
_mentioned_ids: Set[str] = set()
_mentioned_max_size: int = 200
_ignored_categories: Dict[str, float] = {}  # category -> ignored_until timestamp


def _cleanup_expired():
    """清理过期feed条目和去重记录。"""
    global _feed_store, _mentioned_ids
    now = time.time()

    # 清理过期feed（24小时）
    expired = [
        k for k, v in _feed_store.items()
        if now - v.seen_at > 86400  # 24h
    ]
    for k in expired:
        del _feed_store[k]
    if expired:
        logger.debug(f"[Feed] 清理过期条目: {len(expired)} 条")

    # 清理过期忽略记录（3天）
    global _ignored_categories
    _ignored_categories = {
        k: v for k, v in _ignored_categories.items()
        if now < v
    }

    # 限制去重集合大小
    if len(_mentioned_ids) > _mentioned_max_size:
        _mentioned_ids = set(list(_mentioned_ids)[-_mentioned_max_size // 2:])


def store_feed_items(items: List[FeedItem]) -> int:
    """存储feed条目到记忆。返回新增数量。"""
    global _feed_store
    _cleanup_expired()

    added = 0
    for item in items:
        if not item.content or len(item.content) < 3:
            continue
        # 已存在则更新 relevance
        if item.item_id in _feed_store:
            existing = _feed_store[item.item_id]
            existing.relevance = min(1.0, existing.relevance + 0.2)
            _feed_store.move_to_end(item.item_id)
            continue
        # 新增
        if len(_feed_store) >= _feed_max_items:
            _feed_store.popitem(last=False)  # 移除最旧的
        _feed_store[item.item_id] = item
        added += 1

    if added:
        logger.info(f"[Feed] 新增 {added} 条，当前共 {len(_feed_store)} 条")
    return added


def decay_feed_memory():
    """衰减feed相关性。由定时任务周期调用。"""
    now = time.time()
    for item in _feed_store.values():
        age_hours = (now - item.seen_at) / 3600
        if age_hours <= 2:
            item.relevance = 1.0  # 2h内保持新鲜
        elif age_hours <= 6:
            item.relevance = 1.0 - (age_hours - 2) * 0.175  # 线性衰减到0.3
        elif age_hours <= 24:
            item.relevance = max(0.0, 0.3 - (age_hours - 6) * 0.0167)  # 缓慢衰减到0
        else:
            item.relevance = 0.0

    _cleanup_expired()


def get_recent_feed(limit: int = 5, max_age_minutes: int = 120) -> List[FeedItem]:
    """获取最近看到的内容（按relevance排序）。"""
    _cleanup_expired()
    now = time.time()
    max_age_sec = max_age_minutes * 60

    candidates = [
        item for item in _feed_store.values()
        if now - item.seen_at < max_age_sec and item.relevance > 0.2
    ]
    # 按relevance降序
    candidates.sort(key=lambda x: x.relevance, reverse=True)
    return candidates[:limit]


def get_feed_count() -> int:
    """当前有效feed条目数。"""
    _cleanup_expired()
    now = time.time()
    return sum(
        1 for item in _feed_store.values()
        if now - item.seen_at < 86400 and item.relevance > 0.1
    )


# ============================================================
# 引用去重（借鉴 LocoAgent Operation Log）
# ============================================================

def mark_as_mentioned(item_id: str) -> None:
    """标记某条feed已被Bot在对话中提过。"""
    _mentioned_ids.add(item_id)
    if len(_mentioned_ids) > _mentioned_max_size:
        # 保留最近的一半
        keep = list(_mentioned_ids)[-_mentioned_max_size // 2:]
        _mentioned_ids.clear()
        _mentioned_ids.update(keep)


def was_mentioned(item_id: str) -> bool:
    """检查某条feed是否已被提过。"""
    return item_id in _mentioned_ids


def mark_category_ignored(category: str, hours: int = 72):
    """标记某类话题被用户忽略，一段时间内不提。"""
    _ignored_categories[category] = time.time() + hours * 3600
    logger.debug(f"[Feed] 类别 {category} 被忽略 {hours}h")


def is_category_ignored(category: str) -> bool:
    """检查某类话题是否在忽略期内。"""
    if category not in _ignored_categories:
        return False
    return time.time() < _ignored_categories[category]


# ============================================================
# 自然语言摘要（供 prompt 注入）
# ============================================================

# 多样化的自然表述模板（避免机械重复）
_NATURAL_FRAMINGS = {
    "抖音": [
        "刚刷到一条抖音「{content}」",
        "抖音上都在发「{content}」",
        "刷抖音看到「{content}」，大家都在讨论",
        "抖音刷到「{content}」了",
    ],
    "B站": [
        "B站上看到「{content}」",
        "刚才在B站刷到「{content}」",
        "B站热搜有个「{content}」挺火的",
        "B站好多人在聊「{content}」",
    ],
    "微博": [
        "微博上在说「{content}」",
        "刷微博看到「{content}」上热搜了",
        "微博好多人转发「{content}」",
        "看到一条微博「{content}」",
    ],
    "小红书": [
        "小红书上看到「{content}」",
        "刷小红书刷到「{content}」",
        "小红书都在推「{content}」",
    ],
    "小黑盒": [
        "小黑盒上看到「{content}」",
        "游戏圈在聊「{content}」",
    ],
}

# 记录最近使用的模板索引（防重复）
_framing_history: List[str] = []  # 最近10条使用的framing


def get_scroll_memory_summary(limit: int = 3) -> Optional[str]:
    """生成自然语言摘要："你刚刷手机看到了XXX"。

    返回格式化的prompt文本，或None（无有效内容时）。
    """
    items = get_recent_feed(limit=limit, max_age_minutes=240)  # 4h内

    # 过滤已提过和被忽略的
    items = [
        item for item in items
        if not was_mentioned(item.item_id)
        and not is_category_ignored(item.category)
    ]
    if not items:
        return None

    lines = []
    for item in items[:3]:
        source_framings = _NATURAL_FRAMINGS.get(item.source, ["看到「{content}」"])
        # 选择最近没用过的framing
        available = [f for f in source_framings if f not in _framing_history[-5:]]
        if not available:
            available = source_framings
        framing = random.choice(available)
        lines.append(f"- {framing.format(content=item.content[:30])}")

        # 记录使用历史
        _framing_history.append(framing)
        if len(_framing_history) > 20:
            _framing_history.pop(0)

    if not lines:
        return None

    # 构建多样化引导语
    intros = [
        "你刚才刷手机看到了以下内容：",
        "你最近刷到的（可以随口提一下）：",
        "你刷手机时注意到这些：",
    ]
    intro = random.choice(intros)

    outro = random.choice([
        "\n如果话题合适可以自然提一句，不要生硬播报。1-2个就够了，不用全提。",
        "\n聊天中如果碰到相关话题，可以随口说一下。不用刻意。",
        "\n有合适的切入点就提一嘴，像刚刷到随手分享给朋友。不用每个都提。",
    ])

    return intro + "\n" + "\n".join(lines) + outro


def get_scroll_trigger_hint() -> Optional[str]:
    """生成单条feed的触发提示，用于behavior_engine注入。

    比 get_scroll_memory_summary 更轻量，只返回一条最合适的。
    """
    items = get_recent_feed(limit=5, max_age_minutes=240)
    items = [
        item for item in items
        if not was_mentioned(item.item_id)
        and not is_category_ignored(item.category)
    ]
    if not items:
        return None

    # 随机选一条（relevance加权）
    weights = [item.relevance for item in items]
    total = sum(weights)
    item = random.choices(items, weights=[w/total for w in weights], k=1)[0]

    source_framings = _NATURAL_FRAMINGS.get(item.source, ["看到「{content}」"])
    framing = random.choice(source_framings)
    text = framing.format(content=item.content[:30])

    # 标记为已提及
    mark_as_mentioned(item.item_id)

    logger.info(f"[Feed] 触发引用: {text[:50]}")
    return f"你刚看到：{text}。可以随口提一下。"


# ============================================================
# 刷手机决策
# ============================================================

# 人设兴趣关键词（匹配的内容概率×2）
_INTEREST_KEYWORDS = [
    "原神", "星穹铁道", "崩坏", "米哈游", "游戏", "动漫", "番剧",
    "芙莉莲", "周杰伦", "YOASOBI", "音乐", "猫", "宠物", "萌宠",
    "奶茶", "美食", "甜品", "设计", "艺术", "B站", "二次元",
    "cos", "漫展", "声优", "galgame", "抽卡", "氪金",
]


def boost_interest_items(items: List[FeedItem]) -> List[FeedItem]:
    """给匹配人设兴趣的内容加权（概率×2）。

    在 simulate_scroll 中选择时，兴趣匹配的条目有更高概率被"注意到"。
    """
    for item in items:
        content_lower = item.content.lower()
        if any(kw in content_lower for kw in _INTEREST_KEYWORDS):
            item.relevance = min(1.5, item.relevance * 2.0)  # boost但设上限1.5
            item.category = _classify_interest(content_lower)
    return items


def _classify_interest(content: str) -> str:
    """简单分类。"""
    if any(kw in content for kw in ["原神", "星穹", "游戏", "抽卡", "氪金"]):
        return "游戏"
    if any(kw in content for kw in ["动漫", "番剧", "二次元", "cos", "漫展"]):
        return "动漫"
    if any(kw in content for kw in ["猫", "宠物", "萌宠"]):
        return "宠物"
    if any(kw in content for kw in ["奶茶", "美食", "甜品"]):
        return "美食"
    if any(kw in content for kw in ["音乐", "周杰伦", "YOASOBI"]):
        return "音乐"
    return "其他"


# 时段刷手机概率（schedule period → probability）
_SCROLL_PROBABILITIES = {
    "sleeping": 0.0,
    "waking": 0.15,
    "meal": 0.3,
    "lazy": 0.6,
    "active": 0.2,
    "night_owl": 0.7,
    "skip_class": 0.5,
}

# 上次刷手机的时间
_last_scroll_time: float = 0
_SCROLL_COOLDOWN: int = 45 * 60  # 45分钟冷却


def should_scroll_now(schedule_period: str = "active") -> bool:
    """判断现在是否应该"刷手机"。"""
    global _last_scroll_time

    prob = _SCROLL_PROBABILITIES.get(schedule_period, 0.2)
    if prob <= 0:
        return False

    # 冷却时间
    if time.time() - _last_scroll_time < _SCROLL_COOLDOWN:
        return False

    if random.random() < prob:
        _last_scroll_time = time.time()
        return True
    return False


def mark_scrolled():
    """标记刚刷过手机（外部调用，如 hot_topics fetch 后）。"""
    global _last_scroll_time
    _last_scroll_time = time.time()


# ============================================================
# 手机电量细节（人格化小彩蛋）
# ============================================================

_PHONE_BATTERY_MESSAGES = [
    "手机快没电了...等会儿回你🔋",
    "充着电呢，手机好烫🥵",
    "刚找充电线找了半天...",
    "手机要关机了啊啊啊",
    "电量红了...先不说了",
    "充电ing...充得好慢啊",
]


def get_phone_battery_quirk(trigger_chance: float = 0.008) -> Optional[str]:
    """极小概率触发手机电量相关吐槽（0.8%）。"""
    if random.random() > trigger_chance:
        return None
    return random.choice(_PHONE_BATTERY_MESSAGES)


# ============================================================
# "回想中"迟疑感
# ============================================================

_RECALL_HESITATIONS = [
    "等一下我找找...刚才刷到那个叫啥来着...",
    "就是那个...诶忘了名字了，反正就是...",
    "刚刚刷到一个...等等我想想...",
    "嘶...刚看到的，叫什么来着...",
]


def get_recall_hesitation(trigger_chance: float = 0.06) -> Optional[str]:
    """引用feed时的迟疑前缀（6%概率）。"""
    if random.random() > trigger_chance:
        return None
    return random.choice(_RECALL_HESITATIONS)


# ============================================================
# 清理
# ============================================================

def clear_feed():
    """清空feed记忆（用于测试/重置）。"""
    global _feed_store, _mentioned_ids, _ignored_categories, _framing_history
    _feed_store.clear()
    _mentioned_ids.clear()
    _ignored_categories.clear()
    _framing_history.clear()
    logger.debug("[Feed] 记忆已清空")
