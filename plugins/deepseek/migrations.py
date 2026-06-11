"""数据库迁移机制 — ECC database-migrations 风格。

版本化迁移文件，按顺序执行，支持状态追踪。

B12 注意：init_db() 在 startup 时先于 migrations 运行，已创建全部表（使用 IF NOT EXISTS）。
迁移仅处理增量变更（ALTER TABLE / 数据迁移）。纯建表的迁移保留为 no-op 以维持版本号连续性。
"""
from typing import Any
from typing import Callable
from typing import Coroutine
from typing import List

import aiosqlite
from nonebot import logger

# 迁移列表：(版本号, 迁移函数)
MIGRATIONS: List[tuple] = []


def migration(version: int):
    """装饰器：注册一个数据库迁移。"""
    def decorator(func: Callable[[aiosqlite.Connection], Coroutine[Any, Any, None]]):
        MIGRATIONS.append((version, func))
        return func
    return decorator


async def ensure_migration_table(db: aiosqlite.Connection):
    """确保迁移状态表存在。"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version INTEGER PRIMARY KEY,
            applied_at REAL,
            description TEXT
        )
    """)
    await db.commit()


async def get_current_version(db: aiosqlite.Connection) -> int:
    """获取当前数据库版本。"""
    await ensure_migration_table(db)
    async with db.execute("SELECT MAX(version) as v FROM _migrations") as cursor:
        row = await cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
        return 0


async def run_migrations(db: aiosqlite.Connection):
    """执行所有未执行的迁移。"""
    current = await get_current_version(db)
    pending = [(v, f) for v, f in MIGRATIONS if v > current]
    if not pending:
        return

    for version, func in sorted(pending, key=lambda x: x[0]):
        try:
            logger.info(f"[迁移] 执行迁移 v{version}...")
            await func(db)
            import time
            await db.execute(
                "INSERT INTO _migrations (version, applied_at) VALUES (?, ?)",
                (version, time.time())
            )
            await db.commit()
            logger.info(f"[迁移] v{version} 完成")
        except Exception as e:
            logger.error(f"[迁移] v{version} 失败: {e}")
            raise


# ============================================================
# 迁移定义
# ============================================================

@migration(1)
async def migrate_v1_add_confidence(db: aiosqlite.Connection):
    """添加 confidence 和 hit_count 到 memory_tags。"""
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN confidence REAL DEFAULT 0.5")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN hit_count INTEGER DEFAULT 0")
    except Exception:
        pass
    await db.commit()


@migration(2)
async def migrate_v2_add_session_state(db: aiosqlite.Connection):
    """添加会话状态持久化表。"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_state (
            session_id TEXT PRIMARY KEY,
            last_topic TEXT DEFAULT '',
            last_emotion TEXT DEFAULT '',
            last_interaction REAL DEFAULT 0,
            context_summary TEXT DEFAULT '',
            bot_mood_snapshot TEXT DEFAULT '{}'
        )
    """)
    await db.commit()


@migration(3)
async def migrate_v3_add_preferences_and_quality(db: aiosqlite.Connection):
    """添加用户偏好表和回复质量评估表（功能③⑦）。"""
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
    await db.commit()


@migration(4)
async def migrate_v4_add_memory_tiers(db: aiosqlite.Connection):
    """添加记忆分层：short_term / long_term 两种衰减速率。"""
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN tier TEXT DEFAULT 'short_term'")
    except Exception:
        pass
    # 将已有高置信度标签升级为长期记忆
    await db.execute(
        "UPDATE memory_tags SET tier = 'long_term' WHERE confidence >= 0.7 AND hit_count >= 3"
    )
    await db.commit()


@migration(5)
async def migrate_v5_add_emotion_log(db: aiosqlite.Connection):
    """添加情绪日志表（Phase 3）。"""
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
    # B14: idx_emotion_log_ts 已被 init_db() 中的复合索引 idx_emotion_log_user(user_id, timestamp) 替代
    await db.execute("CREATE INDEX IF NOT EXISTS idx_emotion_log_user ON emotion_log(user_id, timestamp)")
    await db.commit()


@migration(6)
async def migrate_v6_add_user_profiles(db: aiosqlite.Connection):
    """Phase 4：添加用户画像表（关系风格、昵称、兴趣摘要等）。"""
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
    await db.commit()


@migration(7)
async def migrate_v7_add_milestones_and_first_interaction(db: aiosqlite.Connection):
    """Phase 5：添加关系里程碑追踪表 + affection.first_interaction 字段。"""
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
    # 为 affection 表添加 first_interaction 字段（用于计算认识时长）
    try:
        await db.execute("ALTER TABLE affection ADD COLUMN first_interaction REAL")
    except Exception:
        pass
    await db.commit()


@migration(8)
async def migrate_v8_add_bot_disclosures(db: aiosqlite.Connection):
    """添加 bot 自我披露追踪表。"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_disclosures (
            user_id TEXT NOT NULL,
            disclosure_key TEXT NOT NULL,
            revealed_at REAL,
            reveal_count INTEGER DEFAULT 1,
            UNIQUE(user_id, disclosure_key)
        )
    """)
    await db.commit()


@migration(9)
async def migrate_v9_add_proactive_scene(db: aiosqlite.Connection):
    """proactive_log 表添加 scene 列（修复去重机制）。"""
    async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='proactive_log'") as cur:
        if not await cur.fetchone():
            return
    async with db.execute("PRAGMA table_info(proactive_log)") as cursor:
        columns = [row[1] for row in await cursor.fetchall()]
    if "scene" not in columns:
        await db.execute("ALTER TABLE proactive_log ADD COLUMN scene TEXT DEFAULT ''")
    await db.commit()


@migration(10)
async def migrate_v10_add_mood_snapshots(db: aiosqlite.Connection):
    """添加情绪快照表（情绪记忆功能）。"""
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
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_mood_snap_user ON mood_snapshots(user_id, snapshot_time)"
    )
    await db.commit()


@migration(11)
async def migrate_v11_add_bot_personality(db: aiosqlite.Connection):
    """B8: bot_personality 表已移除 — 从未被使用过，空操作保留版本号。"""
    await db.commit()


@migration(12)
async def migrate_v12_add_group_members(db: aiosqlite.Connection):
    """添加群聊成员画像表。"""
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
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_group_members_group ON group_members(group_id, last_active)"
    )
    await db.commit()


@migration(13)
async def migrate_v13_add_memory_deepening(db: aiosqlite.Connection):
    """记忆系统深化：共同回忆、私人梗、重要日期。"""
    # 共同回忆
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
    # 私人梗
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
    # 重要日期
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
    await db.commit()


@migration(14)
async def migrate_v14_add_social_features(db: aiosqlite.Connection):
    """社交能力增强：社交关系图、群聊梗、社交记忆。"""
    # 社交关系图
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
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_social_graph_group ON group_social_graph(group_id, strength DESC)"
    )
    # 群聊梗
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
    # 社交记忆
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
    await db.commit()


@migration(15)
async def migrate_v15_add_embedding_and_scratchpad(db: aiosqlite.Connection):
    """语义记忆检索 + 工作记忆 + 无损压缩：embedding / scratchpad / archived 列。"""
    for col_sql, table in [
        ("ALTER TABLE memory_tags ADD COLUMN embedding BLOB", "memory_tags"),
        ("ALTER TABLE session_state ADD COLUMN scratchpad TEXT DEFAULT ''", "session_state"),
        ("ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0", "memories"),
    ]:
        try:
            await db.execute(col_sql)
        except Exception:
            pass  # 列已存在
    await db.commit()
