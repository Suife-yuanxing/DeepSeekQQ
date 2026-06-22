"""统计聚合 API — Task 1.16。

数据面板 9 端点（含话题排行/心情日历/成就墙/分享）。
对齐前端 [数据面板.html] 的数据展示。

v2 审计落地：
  - 复用 db_affection（好感等级）/ emotion_log（心情日历）/ db_tags（记忆标签）
  - 话题分类：基于 chat_messages 内容做关键词聚合（不依赖 topic_tracker.py 纯内存方案）
  - 成就墙：新建 achievements_simple 内存定义表 + 解锁逻辑
  - H5: 所有 /{bot_id} 端点强制 ownership 校验
"""
import math
import re
import time
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel

from .deps import get_current_user
from .deps import require_bot_owner
from ..db_core import get_db
from ..db_platform import get_messages as _get_messages

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])

# ============================================================
# 好感度等级表（5 级）
# ============================================================

AFFECTION_TITLES = [
    {"level": 0, "title": "陌生人", "min_score": 0},
    {"level": 1, "title": "点头之交", "min_score": 100},
    {"level": 2, "title": "普通朋友", "min_score": 300},
    {"level": 3, "title": "好朋友", "min_score": 600},
    {"level": 4, "title": "亲密伙伴", "min_score": 1000},
    {"level": 5, "title": "灵魂伴侣", "min_score": 2000},
]


def _calc_affection(total_chats: int) -> dict:
    """基于聊天总数计算好感等级（简化版，与 db_affection 解耦）。"""
    level = 0
    for lv in reversed(AFFECTION_TITLES):
        if total_chats >= lv["min_score"]:
            level = lv["level"]
            title = lv["title"]
            break
    else:
        level = 0
        title = "陌生人"

    # 下一级进度
    next_level = min(level + 1, 5)
    next_min = AFFECTION_TITLES[next_level]["min_score"]
    current_min = AFFECTION_TITLES[level]["min_score"]
    range_size = next_min - current_min
    progress = (total_chats - current_min) / range_size if range_size > 0 else 1.0

    return {
        "level": level,
        "title": title,
        "score": total_chats,
        "progress": min(1.0, progress),
        "total_chats": total_chats,
    }


# ============================================================
# 简单话题分类（基于正则关键词）
# ============================================================

_TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("生活日常", ["吃饭", "睡觉", "起床", "洗澡", "上班", "下班", "上学", "放学",
                  "做饭", "买菜", "打扫", "洗衣服", "出门", "回家", "熬夜", "早起"]),
    ("工作学习", ["工作", "学习", "考试", "作业", "项目", "代码", "bug", "考试",
                  "论文", "报告", "开会", "面试", "辞职", "加班", "实习"]),
    ("情感关系", ["喜欢", "爱", "想", "思念", "难过", "开心", "生气", "伤心",
                  "感动", "吃醋", "对不起", "谢谢", "讨厌", "烦", "累"]),
    ("娱乐兴趣", ["游戏", "动漫", "小说", "电影", "音乐", "综艺", "追剧", "漫画",
                  "原神", "王者", "吃鸡", "B站", "抖音", "小红书", "唱歌"]),
    ("健康运动", ["运动", "健身", "跑步", "减肥", "生病", "感冒", "头痛", "肚子痛",
                  "瑜伽", "游泳", "打球", "散步", "体检", "吃药"]),
    ("科技数码", ["手机", "电脑", "app", "软件", "AI", "人工智能", "编程", "数码",
                  "相机", "耳机", "游戏机", "switch", "ps5"]),
    ("美食", ["好吃", "美食", "外卖", "奶茶", "咖啡", "火锅", "烧烤", "甜品",
              "麻辣烫", "炸鸡", "披萨", "寿司", "拉面", "蛋糕"]),
    ("天气旅行", ["天气", "下雨", "晴天", "下雪", "旅行", "旅游", "飞机", "高铁",
                  "自驾", "酒店", "海边", "爬山", "景点"]),
]


def _classify_topic(text: str) -> str:
    """基于关键词将消息归类到话题。返回第一个匹配的话题名，否则 '其他'。"""
    for topic_name, kws in _TOPIC_KEYWORDS:
        for kw in kws:
            if kw in text:
                return topic_name
    return "其他"


# ============================================================
# 成就系统预定义
# ============================================================

ACHIEVEMENTS = [
    {"id": "first_chat", "name": "初次见面", "desc": "发送第 1 条消息", "icon": "👋",
     "check": lambda stats: stats["total_messages"] >= 1},
    {"id": "chat_7_days", "name": "七日之约", "desc": "连续聊天 7 天", "icon": "📅",
     "check": lambda stats: stats["streak_days"] >= 7},
    {"id": "chat_30_days", "name": "月满情浓", "desc": "连续聊天 30 天", "icon": "🌙",
     "check": lambda stats: stats["streak_days"] >= 30},
    {"id": "msg_100", "name": "小话痨", "desc": "累计 100 条消息", "icon": "💬",
     "check": lambda stats: stats["total_messages"] >= 100},
    {"id": "msg_1000", "name": "千言万语", "desc": "累计 1000 条消息", "icon": "📖",
     "check": lambda stats: stats["total_messages"] >= 1000},
    {"id": "affection_3", "name": "知心好友", "desc": "好感等级达到 3", "icon": "🤝",
     "check": lambda stats: stats["affection"]["level"] >= 3},
    {"id": "affection_5", "name": "灵魂伴侣", "desc": "好感等级达到 5", "icon": "💖",
     "check": lambda stats: stats["affection"]["level"] >= 5},
    {"id": "topics_5", "name": "无话不谈", "desc": "聊过 5 个不同话题", "icon": "🎯",
     "check": lambda stats: stats.get("topic_count", 0) >= 5},
    {"id": "night_owl", "name": "夜猫子", "desc": "在午夜时段（0-5点）聊过 10 次", "icon": "🦉",
     "check": lambda stats: stats.get("night_chats", 0) >= 10},
    {"id": "comeback", "name": "王者归来", "desc": "间隔 7 天以上再次聊天", "icon": "👑",
     "check": lambda stats: stats.get("has_comeback", False)},
]


# ============================================================
# 辅助：获取 Bot 的统计分析
# ============================================================

async def _get_bot_stats(bot_id: int, bot: dict) -> dict:
    """获取 Bot 的完整统计数据。"""
    db = await get_db()
    now = time.time()
    today_start = int(now // 86400 * 86400)

    # 总消息数
    async with db.execute(
        "SELECT COUNT(*) as cnt, MIN(created_at) as first_ts, MAX(created_at) as last_ts FROM chat_messages WHERE bot_id = ?",
        (bot_id,),
    ) as cur:
        row = await cur.fetchone()
    total_messages = row["cnt"] if row and row["cnt"] else 0
    first_ts = row["first_ts"] if row and row["first_ts"] else now
    last_ts = row["last_ts"] if row and row["last_ts"] else now

    # 聊天天数
    chat_days = max(1, int((now - first_ts) / 86400) + 1) if total_messages > 0 else 1

    # 连续天数（最近 24h 内有消息）
    streak_days = 0
    if last_ts > now - 86400:
        async with db.execute(
            "SELECT DISTINCT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') as d FROM chat_messages WHERE bot_id = ? ORDER BY d DESC LIMIT 30",
            (bot_id,),
        ) as cur:
            dates = [r["d"] for r in await cur.fetchall()]
        streak_days = 1
        for i in range(1, len(dates)):
            from datetime import datetime, timedelta
            prev = datetime.strptime(dates[i - 1], "%Y-%m-%d")
            cur_d = datetime.strptime(dates[i], "%Y-%m-%d")
            if (prev - cur_d).days == 1:
                streak_days += 1
            else:
                break

    # 今日消息
    async with db.execute(
        "SELECT COUNT(*) as cnt FROM chat_messages WHERE bot_id = ? AND created_at >= ? AND role = 'user'",
        (bot_id, today_start),
    ) as cur:
        row = await cur.fetchone()
    today_msgs = row["cnt"] if row else 0

    # 好感（基于消息总数估算）
    affection = _calc_affection(total_messages)

    return {
        "total_messages": total_messages,
        "today_messages": today_msgs,
        "chat_days": chat_days,
        "streak_days": streak_days,
        "first_chat_at": first_ts,
        "last_chat_at": last_ts,
        "affection": affection,
    }


async def _get_topic_stats(bot_id: int, days: int = 30) -> list[dict]:
    """获取话题排行。"""
    db = await get_db()
    since = time.time() - days * 86400
    async with db.execute(
        "SELECT content FROM chat_messages WHERE bot_id = ? AND role = 'user' AND created_at >= ? ORDER BY created_at DESC LIMIT 500",
        (bot_id, since),
    ) as cur:
        rows = await cur.fetchall()
    topic_counter: dict[str, int] = {}
    for r in rows:
        topic = _classify_topic(r["content"])
        topic_counter[topic] = topic_counter.get(topic, 0) + 1
    sorted_topics = sorted(topic_counter.items(), key=lambda x: -x[1])
    total = sum(c for _, c in sorted_topics) or 1
    return [
        {"topic_name": t, "count": c, "pct": round(c / total * 100, 1)}
        for t, c in sorted_topics[:8]
    ]


async def _get_mood_data(bot_id: int, days: int = 28) -> list[dict]:
    """获取情绪日历数据。"""
    db = await get_db()
    since = time.time() - days * 86400
    # 从 emotion_log 表获取（如果可用），否则从 chat_messages 估算
    try:
        async with db.execute(
            """SELECT strftime('%Y-%m-%d', timestamp, 'unixepoch', 'localtime') as date,
                      AVG(valence) as avg_valence, AVG(arousal) as avg_arousal,
                      COUNT(*) as cnt
               FROM emotion_log WHERE user_id = ? AND timestamp >= ?
               GROUP BY date ORDER BY date""",
            (str(bot_id), since),
        ) as cur:
            rows = await cur.fetchall()
        if rows:
            return [
                {
                    "date": r["date"],
                    "avg_valence": round(r["avg_valence"], 2),
                    "avg_arousal": round(r["avg_arousal"], 2),
                    "count": r["cnt"],
                }
                for r in rows
            ]
    except Exception:
        pass

    # fallback: 从 chat_messages 估算（按日聚合 bot 回复占比）
    async with db.execute(
        """SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') as date,
                  COUNT(*) as total_msgs
           FROM chat_messages WHERE bot_id = ? AND created_at >= ?
           GROUP BY date ORDER BY date""",
        (bot_id, since),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {
            "date": r["date"],
            "total_msgs": r["total_msgs"],
        }
        for r in rows
    ]


# ============================================================
# 端点
# ============================================================

@router.get("/{bot_id}/summary")
async def get_summary(bot_id: int, user=Depends(get_current_user)):
    """KPI + 关系卡片。"""
    bot = await require_bot_owner(bot_id, user)
    stats = await _get_bot_stats(bot_id, bot)
    return {
        "total_messages": stats["total_messages"],
        "today_messages": stats["today_messages"],
        "chat_days": stats["chat_days"],
        "streak_days": stats["streak_days"],
        "affection_level": stats["affection"]["level"],
        "affection_title": stats["affection"]["title"],
        "affection_score": stats["affection"]["score"],
        "affection_progress": stats["affection"]["progress"],
    }


@router.get("/{bot_id}/relation")
async def get_relation(bot_id: int, user=Depends(get_current_user)):
    """关系卡片详情。"""
    bot = await require_bot_owner(bot_id, user)
    stats = await _get_bot_stats(bot_id, bot)
    days_since = max(1, int((time.time() - stats["first_chat_at"]) / 86400))
    return {
        "days_since_first_chat": days_since,
        "affection": stats["affection"],
        "total_messages": stats["total_messages"],
        "streak_days": stats["streak_days"],
        "last_chat_at": stats["last_chat_at"],
    }


@router.get("/mood")
async def get_mood(
    bot_id: int = Query(...),
    days: int = Query(28, ge=7, le=90),
    user=Depends(get_current_user),
):
    """心情日历数据。"""
    await require_bot_owner(bot_id, user)
    return {"mood_data": await _get_mood_data(bot_id, days)}


@router.get("/mood/{date}")
async def get_mood_detail(
    date: str,
    bot_id: int = Query(...),
    user=Depends(get_current_user),
):
    """指定日期的情绪详情。"""
    bot = await require_bot_owner(bot_id, user)
    db = await get_db()
    day_start = 0
    day_end = time.time()
    try:
        from datetime import datetime
        day_start = datetime.strptime(date, "%Y-%m-%d").timestamp()
        day_end = day_start + 86400
    except ValueError:
        raise HTTPException(400, detail={"code": "invalid_date", "message": "日期格式错误，应为 YYYY-MM-DD"})

    async with db.execute(
        "SELECT COUNT(*) as cnt FROM chat_messages WHERE bot_id = ? AND created_at >= ? AND created_at < ?",
        (bot_id, day_start, day_end),
    ) as cur:
        row = await cur.fetchone()

    return {
        "date": date,
        "conversation_count": row["cnt"] if row else 0,
    }


@router.get("/topics")
async def get_topics(
    bot_id: int = Query(...),
    days: int = Query(30, ge=7, le=365),
    user=Depends(get_current_user),
):
    """话题排行 Top5。"""
    await require_bot_owner(bot_id, user)
    topics = await _get_topic_stats(bot_id, days)
    return {"topics": topics[:5], "total": len(topics)}


@router.get("/active-hours")
async def get_active_hours(
    bot_id: int = Query(...),
    days: int = Query(30, ge=7, le=365),
    user=Depends(get_current_user),
):
    """互动时段分布。"""
    bot = await require_bot_owner(bot_id, user)
    db = await get_db()
    since = time.time() - days * 86400
    async with db.execute(
        """SELECT CAST(strftime('%H', created_at, 'unixepoch', 'localtime') AS INTEGER) as hour,
                  COUNT(*) as cnt
           FROM chat_messages WHERE bot_id = ? AND role = 'user' AND created_at >= ?
           GROUP BY hour ORDER BY hour""",
        (bot_id, since),
    ) as cur:
        rows = await cur.fetchall()
    hour_data = [{"hour": r["hour"], "count": r["cnt"]} for r in rows]
    return {"hours": hour_data}


@router.get("/user-profile")
async def get_user_profile(
    bot_id: int = Query(...),
    user=Depends(get_current_user),
):
    """"Bot 了解你" — 记忆标签 + 偏好。"""
    bot = await require_bot_owner(bot_id, user)
    # 尝试从 memory_tags 获取（旧系统），否则从 chat_messages 提取关键词
    try:
        from ..db_tags import get_relevant_memory_tags, get_all_memory_tags_for_user
        # 旧系统 user_id = bot owner 的 QQ 格式
        owner_id = str(user["id"])
        tags = await get_relevant_memory_tags(owner_id, limit=10)
        if tags:
            return {
                "tags": [
                    {
                        "type": t.get("tag_type", "memory"),
                        "content": t["content"],
                        "confidence": round(t.get("confidence", 0.5), 2),
                    }
                    for t in tags if t.get("confidence", 0) >= 0.3
                ],
                "source": "memory_tags",
            }
    except Exception:
        pass

    # fallback: 从 chat_messages 提取高频关键词
    db = await get_db()
    async with db.execute(
        "SELECT content FROM chat_messages WHERE bot_id = ? AND role = 'user' ORDER BY created_at DESC LIMIT 200",
        (bot_id,),
    ) as cur:
        recent_msgs = [r["content"] for r in await cur.fetchall()]

    # 简单词频统计
    word_count: dict[str, int] = {}
    for msg in recent_msgs:
        for w in re.findall(r'[一-鿿]{2,5}', msg):
            word_count[w] = word_count.get(w, 0) + 1
    sorted_words = sorted(word_count.items(), key=lambda x: -x[1])[:10]
    return {
        "tags": [
            {"type": "interest", "content": w, "count": c}
            for w, c in sorted_words
        ],
        "source": "chat_messages",
    }


@router.get("/achievements")
async def get_achievements(
    bot_id: int = Query(...),
    user=Depends(get_current_user),
):
    """成就墙。"""
    bot = await require_bot_owner(bot_id, user)
    stats = await _get_bot_stats(bot_id, bot)
    topics = await _get_topic_stats(bot_id, 365)
    topic_count = len(topics)
    stats["topic_count"] = topic_count

    # 午夜聊天次数
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM chat_messages
           WHERE bot_id = ? AND role = 'user'
           AND CAST(strftime('%H', created_at, 'unixepoch', 'localtime') AS INTEGER) < 5""",
        (bot_id,),
    ) as cur:
        row = await cur.fetchone()
    night_chats = row["cnt"] if row else 0
    stats["night_chats"] = night_chats

    # 检测是否有回归（间隔 7d+）
    stats["has_comeback"] = False
    async with db.execute(
        "SELECT DISTINCT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') as d FROM chat_messages WHERE bot_id = ? ORDER BY d",
        (bot_id,),
    ) as cur:
        dates = [r["d"] for r in await cur.fetchall()]
    if len(dates) >= 2:
        from datetime import datetime
        for i in range(1, len(dates)):
            prev = datetime.strptime(dates[i - 1], "%Y-%m-%d")
            curr = datetime.strptime(dates[i], "%Y-%m-%d")
            if (curr - prev).days >= 7:
                stats["has_comeback"] = True
                break

    results = []
    for ach in ACHIEVEMENTS:
        try:
            unlocked = ach["check"](stats)
        except Exception:
            unlocked = False
        results.append({
            "id": ach["id"],
            "name": ach["name"],
            "desc": ach["desc"],
            "icon": ach["icon"],
            "unlocked": unlocked,
        })

    return {"achievements": results, "total": len(results), "unlocked": sum(1 for r in results if r["unlocked"])}


@router.post("/{bot_id}/share")
async def share_stats_card(bot_id: int, user=Depends(get_current_user)):
    """生成分享数据卡片（v2 遗漏功能点）。"""
    bot = await require_bot_owner(bot_id, user)
    stats = await _get_bot_stats(bot_id, bot)
    return {
        "share_url": f"https://niannian.bot/share/{bot_id}?t={int(time.time())}",
        "card": {
            "bot_name": bot["bot_name"],
            "affection": stats["affection"]["title"],
            "total_messages": stats["total_messages"],
            "streak_days": stats["streak_days"],
            "chat_days": stats["chat_days"],
        },
    }
