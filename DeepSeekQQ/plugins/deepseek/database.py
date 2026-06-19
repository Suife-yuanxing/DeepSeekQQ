"""数据库 Facade — 导入子模块并初始化所有表。

所有外部代码继续 `from .database import xxx`，无需修改。
"""
from datetime import datetime
from typing import Optional

from nonebot import logger

from .config import AFFECTION_LEVELS
from .db_affection import check_and_trigger_milestone
from .db_affection import decay_affection
from .db_affection import get_affection
from .db_affection import get_affection_decay_hint
from .db_affection import update_affection
from .db_cache import get_article_cache
from .db_cache import save_article_cache
from .db_core import checkpoint_db
from .db_core import close_db
from .db_core import get_db
from .db_memories import count_memories
from .db_memories import get_keep_ids
from .db_memories import get_last_bot_reply_time
from .db_memories import get_oldest_memories
from .db_memories import get_recent_memories
from .db_memories import has_recent_message
from .db_memories import has_user_message_today
from .db_memories import save_message
from .db_memories import trim_memories
from .db_memories_deep import boost_shared_memory
from .db_memories_deep import decay_shared_memories
from .db_memories_deep import find_matching_meme
from .db_memories_deep import get_important_dates
from .db_memories_deep import get_private_memes
from .db_memories_deep import get_recall_candidates
from .db_memories_deep import get_shared_memories
from .db_memories_deep import get_today_dates
from .db_memories_deep import get_upcoming_dates
from .db_memories_deep import save_important_date
from .db_memories_deep import save_private_meme
from .db_memories_deep import save_shared_memory
from .db_mood import decay_user_mood
from .db_mood import get_bot_mood
from .db_mood import get_catgirl_mood
from .db_mood import get_last_mood_snapshot
from .db_mood import get_mood_care_hint
from .db_mood import get_user_mood
from .db_mood import save_mood_snapshot
from .db_mood import update_bot_mood
from .db_mood import update_catgirl_mood
from .db_mood import update_user_mood
from .db_preferences import get_quality_stats
from .db_preferences import get_top_preference
from .db_preferences import get_top_preferences
from .db_preferences import get_user_preferences
from .db_preferences import save_reply_quality
from .db_preferences import update_user_preference
from .db_proactive import get_recent_greetings
from .db_proactive import get_silent_private_users
from .db_proactive import get_today_proactive_count
from .db_proactive import get_today_proactive_count_by_scene
from .db_proactive import has_proactive_today
from .db_proactive import log_proactive
from .db_reminders import cancel_reminder
from .db_reminders import find_reminder_by_content
from .db_reminders import get_due_reminders
from .db_reminders import get_user_reminders
from .db_reminders import mark_reminder_done
from .db_reminders import reschedule_reminder
from .db_reminders import save_reminder
from .db_session import append_memory_summary
from .db_session import get_active_sessions
from .db_session import get_last_conversation_context
from .db_session import get_memory_summary
from .db_session import get_or_create_user_profile
from .db_session import get_relationship_style
from .db_session import get_session_state
from .db_session import get_undisclosed_facts
from .db_session import mark_disclosed
from .db_session import save_session_state
from .db_session import update_relationship_style
from .db_session import update_user_profile
from .db_social import decay_relationships
from .db_social import find_matching_group_meme
from .db_social import get_group_meme_hint
from .db_social import get_group_memes
from .db_social import get_group_relationships_summary
from .db_social import get_group_role_hint
from .db_social import get_relationship
from .db_social import get_relationships
from .db_social import get_social_reference_hint
from .db_social import get_social_references
from .db_social import record_relationship
from .db_social import record_social_reference
from .db_social import save_group_meme
from .db_tags import boost_memory_tag
from .db_tags import decay_memory_tags
from .db_tags import get_all_memory_tags_for_user
from .db_tags import get_relevant_memory_tags
from .db_tags import prune_memory_tags
from .db_tags import save_memory_tags

# ============================================================
# 早晚安优化：昨晚聊天结束时间查询
# ============================================================

async def get_last_night_end_time(session_id: str) -> Optional[float]:
    """获取昨晚聊天结束时间（最后一条消息的时间戳）。

    用于动态调整早安时间：如果昨晚聊到很晚，第二天早安推迟。
    """
    from datetime import datetime
    from datetime import timedelta

    from .db_core import get_db
    db = await get_db()
    now = datetime.now()
    # 昨天 18:00 到今天 06:00 的消息
    yesterday_6pm = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0).timestamp()
    today_6am = now.replace(hour=6, minute=0, second=0).timestamp()
    async with db.execute(
        "SELECT MAX(timestamp) FROM memories WHERE session_id = ? AND archived = 0 AND timestamp BETWEEN ? AND ?",
        (session_id, yesterday_6pm, today_6am)
    ) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            return float(row[0])
    return None


async def get_last_night_mood_summary(session_id: str) -> Optional[str]:
    """获取昨晚对话的情绪摘要（用于早安时携带上下文）。

    返回：'positive'/'negative'/'neutral'/None
    """
    from datetime import datetime
    from datetime import timedelta

    from .db_core import get_db
    db = await get_db()
    now = datetime.now()
    yesterday_6pm = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0).timestamp()
    today_6am = now.replace(hour=6, minute=0, second=0).timestamp()

    # 查找昨晚最后几条 bot 回复
    async with db.execute(
        "SELECT content FROM memories WHERE session_id = ? AND role = 'assistant' AND archived = 0 AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 5",
        (session_id, yesterday_6pm, today_6am)
    ) as cursor:
        rows = await cursor.fetchall()
        if not rows:
            return None
        # 简单情绪判断
        last_msgs = " ".join(r[0] for r in rows)
        negative_kw = ["难过", "伤心", "不开心", "生气", "烦", "累", "焦虑", "担心", "害怕"]
        positive_kw = ["开心", "高兴", "哈哈", "笑", "棒", "好", "喜欢", "爱"]
        neg_count = sum(1 for kw in negative_kw if kw in last_msgs)
        pos_count = sum(1 for kw in positive_kw if kw in last_msgs)
        if neg_count > pos_count:
            return "negative"
        elif pos_count > neg_count:
            return "positive"
        return "neutral"


async def get_last_greeting_time(user_id: str, greeting_type: str) -> Optional[float]:
    """获取上一次发送早安/晚安的时间戳。

    用于控制问候频率，避免每天机械发送。
    """
    from .db_core import get_db
    db = await get_db()
    scene = "morning" if greeting_type == "morning" else "night"
    async with db.execute(
        "SELECT MAX(timestamp) FROM proactive_log WHERE user_id = ? AND scene IN (?, ?) AND type = 'private'",
        (user_id, scene, f"{scene}_triggered")
    ) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            return float(row[0])
    return None


async def record_farewell(user_id: str, session_id: str):
    """记录用户道别时间（用于晚安后调侃逻辑）。"""
    import json
    import time

    from .db_core import get_db
    db = await get_db()
    # 读取现有 snapshot，合并 farewell_time 而非覆盖
    async with db.execute(
        "SELECT bot_mood_snapshot FROM session_state WHERE session_id = ?",
        (session_id,)
    ) as cursor:
        row = await cursor.fetchone()
    existing = {}
    if row and row[0]:
        try:
            existing = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            pass
    existing["farewell_time"] = time.time()
    await db.execute(
        "UPDATE session_state SET bot_mood_snapshot = ? WHERE session_id = ?",
        (json.dumps(existing, ensure_ascii=False), session_id)
    )
    await db.commit()


async def get_last_farewell_time(session_id: str) -> Optional[float]:
    """获取上次道别时间。"""
    import json

    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT bot_mood_snapshot FROM session_state WHERE session_id = ?",
        (session_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                data = json.loads(row[0])
                return data.get("farewell_time")
            except (json.JSONDecodeError, TypeError):
                pass
    return None


async def init_db():
    """初始化所有表和索引。"""
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL,
            archived INTEGER DEFAULT 0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS affection (
            user_id TEXT PRIMARY KEY,
            score REAL DEFAULT 0,
            level INTEGER DEFAULT 1,
            title TEXT DEFAULT "陌生人",
            last_interaction REAL,
            total_chats INTEGER DEFAULT 0,
            streak_days INTEGER DEFAULT 0,
            last_streak_date TEXT,
            first_interaction REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS catgirl_mood (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mood TEXT DEFAULT "平淡",
            score REAL DEFAULT 50,
            last_updated REAL
        )
    """)
    await db.execute(
        "INSERT OR IGNORE INTO catgirl_mood (id, mood, score, last_updated) VALUES (1, '平淡', 50, ?)",
        (datetime.now().timestamp(),)
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_mood (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            valence REAL DEFAULT 0.0,
            arousal REAL DEFAULT 0.2,
            dominant TEXT DEFAULT '平静',
            trigger_reason TEXT DEFAULT '',
            trigger_time REAL DEFAULT 0,
            last_updated REAL DEFAULT 0
        )
    """)
    await db.execute(
        "INSERT OR IGNORE INTO bot_mood (id, valence, arousal, dominant, trigger_reason, trigger_time, last_updated) "
        "VALUES (1, 0.0, 0.2, '平静', '', 0, ?)",
        (datetime.now().timestamp(),)
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            content TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            confidence REAL DEFAULT 0.5,
            hit_count INTEGER DEFAULT 0,
            created_at REAL,
            last_used REAL,
            tier TEXT DEFAULT 'short_term',
            UNIQUE(user_id, tag_type, content)
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_tags_confidence ON memory_tags(user_id, confidence DESC)"
    )
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN confidence REAL DEFAULT 0.5")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN hit_count INTEGER DEFAULT 0")
    except Exception:
        pass
    await db.execute("""
        CREATE TABLE IF NOT EXISTS memory_summaries (
            session_id TEXT PRIMARY KEY,
            summary TEXT,
            key_moments TEXT,
            updated_at REAL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id, timestamp)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_role ON memories(role, timestamp)")
    # BUGFIX: 复合索引覆盖按 role 过滤的常用查询
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_session_role_ts ON memories(session_id, role, timestamp)")
    # BUGFIX: 覆盖 decay_affection 子查询 (session_id LIKE 'private_%' + timestamp + archived)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_session_ts_archived ON memories(session_id, timestamp, archived)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS article_cache (
            url_hash TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            author TEXT,
            summary TEXT,
            fetched_at REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proactive_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL,
            scene TEXT DEFAULT ''
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_user_scene ON proactive_log(user_id, scene, timestamp)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_proactive_user_type_ts ON proactive_log(user_id, type, timestamp)"
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS morning_skip_state (
            user_id TEXT PRIMARY KEY,
            consecutive_skips INTEGER DEFAULT 0,
            last_morning_date TEXT DEFAULT ''
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_mood (
            user_id TEXT PRIMARY KEY,
            valence REAL DEFAULT 0,
            arousal REAL DEFAULT 0.2,
            dominant TEXT DEFAULT '平静',
            last_updated REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            content TEXT NOT NULL,
            trigger_time REAL NOT NULL,
            repeat_type TEXT DEFAULT 'none',
            status TEXT DEFAULT 'pending',
            created_at REAL NOT NULL,
            original_msg TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_trigger ON reminders(status, trigger_time)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id, status)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS promises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            promise_text TEXT NOT NULL,
            due_hint TEXT DEFAULT '',
            created_at REAL NOT NULL,
            due_at REAL NOT NULL,
            fulfilled INTEGER DEFAULT 0,
            forgotten INTEGER DEFAULT 0,
            fulfilled_at REAL,
            apologized_at REAL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_promises_user_due ON promises(user_id, fulfilled, due_at)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_promises_due ON promises(fulfilled, due_at)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT NOT NULL,
            pref_type TEXT NOT NULL,
            pref_key TEXT NOT NULL,
            pref_value REAL DEFAULT 0,
            sample_count INTEGER DEFAULT 0,
            last_updated REAL,
            UNIQUE(user_id, pref_type, pref_key)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_pref ON user_preferences(user_id, pref_type)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reply_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            quality_score REAL DEFAULT 0,
            feedback_type TEXT,
            created_at REAL,
            emotion_at_reply TEXT,
            params_used TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_reply_quality_user ON reply_quality(user_id, created_at)")
    # 记忆系统深化表（v13 迁移的备份保障）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS shared_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_desc TEXT NOT NULL,
            emotion_tag TEXT DEFAULT '',
            context TEXT DEFAULT '',
            importance REAL DEFAULT 0.5,
            recall_count INTEGER DEFAULT 0,
            created_at REAL,
            last_recalled REAL DEFAULT 0
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_shared_mem_user ON shared_memories(user_id, importance DESC)"
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS private_memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            meme_type TEXT NOT NULL,
            content TEXT NOT NULL,
            origin_context TEXT DEFAULT '',
            trigger_keywords TEXT DEFAULT '',
            frequency REAL DEFAULT 0.3,
            usage_count INTEGER DEFAULT 0,
            created_at REAL,
            last_used REAL DEFAULT 0,
            UNIQUE(user_id, meme_type, content)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS important_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date_type TEXT NOT NULL,
            date_value TEXT NOT NULL,
            description TEXT DEFAULT '',
            repeat_yearly BOOLEAN DEFAULT 1,
            created_at REAL,
            UNIQUE(user_id, date_type, date_value)
        )
    """)
    # === 以下表由 migration 首次创建，此处为安全兜底 ===
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_state (
            session_id TEXT PRIMARY KEY,
            last_topic TEXT DEFAULT '',
            last_emotion TEXT DEFAULT '',
            last_interaction REAL DEFAULT 0,
            context_summary TEXT DEFAULT '',
            bot_mood_snapshot TEXT DEFAULT '{}',
            scratchpad TEXT DEFAULT NULL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_session_state_interaction ON session_state(last_interaction)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS emotion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            emotion_label TEXT,
            valence REAL,
            arousal REAL,
            trigger_text TEXT,
            cause_chain TEXT,
            timestamp REAL
        )
    """)
    # 替换旧的无用索引（仅有 timestamp），改为复合索引覆盖常用查询
    await db.execute("DROP INDEX IF EXISTS idx_emotion_log_ts")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_emotion_log_user ON emotion_log(user_id, timestamp)")
    # BUGFIX: 重新添加 timestamp 单列索引，供 web_admin 无 user_id 过滤的查询使用
    await db.execute("CREATE INDEX IF NOT EXISTS idx_emotion_log_ts ON emotion_log(timestamp)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            relationship_style TEXT DEFAULT 'neutral',
            nickname TEXT DEFAULT '',
            first_interaction REAL,
            total_messages INTEGER DEFAULT 0,
            last_known_mood TEXT DEFAULT '',
            known_interests TEXT DEFAULT '',
            bot_self_summary TEXT DEFAULT ''
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS relationship_milestones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            milestone_type TEXT NOT NULL,
            milestone_value INTEGER,
            triggered_at REAL,
            triggered BOOLEAN DEFAULT 0,
            UNIQUE(user_id, milestone_type)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_disclosures (
            user_id TEXT NOT NULL,
            disclosure_key TEXT NOT NULL,
            revealed_at REAL,
            reveal_count INTEGER DEFAULT 1,
            UNIQUE(user_id, disclosure_key)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS mood_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            valence REAL,
            arousal REAL,
            dominant TEXT,
            snapshot_time REAL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_mood_snap_user ON mood_snapshots(user_id, snapshot_time)")
    # B8: bot_personality 表已移除 — 从未被任何代码读写，是死表
    await db.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            member_id TEXT NOT NULL,
            nickname TEXT DEFAULT '',
            last_active REAL,
            relationship TEXT DEFAULT 'stranger',
            personality_tags TEXT DEFAULT '',
            talk_frequency REAL DEFAULT 0,
            UNIQUE(group_id, member_id)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id, last_active)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS group_social_graph (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            member_a TEXT NOT NULL,
            member_b TEXT NOT NULL,
            rel_type TEXT DEFAULT 'stranger',
            strength REAL DEFAULT 0.1,
            evidence TEXT DEFAULT '',
            interaction_count INTEGER DEFAULT 1,
            created_at REAL,
            last_interaction REAL,
            UNIQUE(group_id, member_a, member_b)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_social_graph_group ON group_social_graph(group_id, strength DESC)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS group_memes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT NOT NULL,
            meme_type TEXT NOT NULL,
            content TEXT NOT NULL,
            trigger_keywords TEXT DEFAULT '',
            creator_id TEXT DEFAULT '',
            frequency REAL DEFAULT 0.3,
            usage_count INTEGER DEFAULT 0,
            created_at REAL,
            last_used REAL DEFAULT 0,
            UNIQUE(group_id, meme_type, content)
        )
    """)
    # 意见记忆表：bot表达过的立场，防止前后矛盾
    await db.execute("""
        CREATE TABLE IF NOT EXISTS opinion_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            bot_stance TEXT NOT NULL,
            user_stance TEXT DEFAULT '',
            agreement_level TEXT DEFAULT 'neutral',
            created_at REAL NOT NULL,
            last_mentioned_at REAL,
            mention_count INTEGER DEFAULT 1,
            UNIQUE(user_id, topic)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_opinion_user ON opinion_memory(user_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_opinion_topic ON opinion_memory(topic)")

    # 主动消息退订与退避表（HF-1 + HF-2）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proactive_opt_out (
            user_id TEXT PRIMARY KEY,
            opted_out_at REAL NOT NULL,
            reason TEXT DEFAULT '',
            created_at REAL DEFAULT (unixepoch())
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proactive_backoff (
            user_id TEXT PRIMARY KEY,
            ignore_count INTEGER DEFAULT 0,
            backoff_until REAL DEFAULT 0,
            last_ignored_at REAL DEFAULT 0,
            updated_at REAL DEFAULT (unixepoch())
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS social_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            person_name TEXT NOT NULL,
            relationship TEXT DEFAULT '',
            mentioned_count INTEGER DEFAULT 1,
            context TEXT DEFAULT '',
            created_at REAL,
            last_mentioned REAL,
            UNIQUE(user_id, person_name)
        )
    """)

    # 真人化 P2-1：微事件发送历史（持久化冷却期追踪）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS micro_event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            event_key TEXT NOT NULL,
            event_text TEXT NOT NULL,
            sent_at REAL NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_micro_event_user_key "
        "ON micro_event_log(user_id, event_key, sent_at)"
    )

    # 真人化 P2-2：用户回复风格基线（疲劳检测基线学习）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS fatigue_baselines (
            user_id TEXT PRIMARY KEY,
            sample_count INTEGER DEFAULT 0,
            avg_reply_length REAL DEFAULT 0,
            avg_reply_gap REAL DEFAULT 0,
            sticker_rate REAL DEFAULT 0,
            question_rate REAL DEFAULT 0,
            last_updated REAL DEFAULT 0
        )
    """)
    await db.commit()
