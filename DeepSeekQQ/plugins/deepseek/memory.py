"""记忆系统向后兼容重导出模块。

实际实现已拆分为4个子模块：
- memory_crud: save_reply, save_and_get_context, save_and_get_context_with_history, apply_affection_delta
- memory_tags: _get_relevant_memories, _extract_memory_tags, _embed_new_tags, _is_memory_relevant, _cleanup_memory_cache
- memory_cache: recover_session_context, _update_session_state, _update_scratchpad_task, _build_bot_emotion_memory_hint, _format_time_ago
- memory_compression: _summarize_and_compress, _learn_preferences, get_user_pref_hints, _evaluate_reply_quality, _adjust_reply_strategy, _extract_shared_memories, _extract_private_memes, _extract_important_dates, _extract_social_references, _extract_group_memes, get_shared_memory_hint, get_private_meme_hint, get_date_hint
"""
# 公开 API — CRUD
from .memory_crud import (
    apply_affection_delta,
    save_and_get_context,
    save_and_get_context_with_history,
    save_reply,
)

# 公开 API — 记忆提示
from .memory_compression import (
    get_date_hint,
    get_private_meme_hint,
    get_shared_memory_hint,
    get_user_pref_hints,
)

# 公开 API — 会话恢复
from .memory_cache import recover_session_context

# 内部函数 — 供测试使用
from .memory_cache import (
    _build_bot_emotion_memory_hint,
    _format_time_ago,
    _update_scratchpad_task,
    _update_session_state,
)
from .memory_compression import (
    _adjust_reply_strategy,
    _evaluate_reply_quality,
    _extract_group_memes,
    _extract_important_dates,
    _extract_private_memes,
    _extract_shared_memories,
    _extract_social_references,
    _learn_preferences,
    _summarize_and_compress,
    _sync_profile_summary,
)
from .memory_tags import (
    MEMORY_COOLDOWN_ROUNDS,
    MAX_MEMORY_PER_REPLY,
    _MEMORY_CACHE_MAX_USERS,
    _cleanup_memory_cache,
    _embed_new_tags,
    _extract_memory_tags,
    _get_relevant_memories,
    _is_memory_relevant,
    _recently_used_memories,
)
