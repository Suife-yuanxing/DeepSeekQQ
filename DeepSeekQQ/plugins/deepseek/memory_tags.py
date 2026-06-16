"""记忆标签提取与检索：关键词 + 语义混合检索 + RRF 融合 + 冷却缓存。

包含内部函数：_get_relevant_memories, _extract_memory_tags, _embed_new_tags,
_is_memory_relevant, _cleanup_memory_cache。
"""
import asyncio
import json
import random
import re
import time
from datetime import datetime
from typing import Dict
from typing import List

from nonebot import logger

from . import api
from .database import boost_memory_tag
from .database import get_memory_summary
from .database import get_relevant_memory_tags
from .database import save_memory_tags
from .utils import safe_task

# ---------- 记忆冷却控制 ----------
_recently_used_memories: Dict[str, tuple] = {}  # user_id -> (tags_list, last_access_timestamp)
_recently_used_memories_lock = asyncio.Lock()  # 防止并发读写竞态
MEMORY_COOLDOWN_ROUNDS = 3   # 同一记忆至少间隔3轮才再次使用

MAX_MEMORY_PER_REPLY = 3     # 每次回复最多插入3条记忆（B6: 1→3 提升记忆利用率）
_MEMORY_CACHE_MAX_USERS = 100  # B16: 最大缓存用户数，200→100
_MEMORY_CACHE_TTL_SECONDS = 72 * 3600  # B16: 72小时未活跃自动清理


def _cleanup_memory_cache():
    """B16: 清理不活跃用户的记忆冷却缓存，防止内存泄漏。

    清理条件：
    1. 用户数超过 _MEMORY_CACHE_MAX_USERS（LRU淘汰）
    2. 超过 _MEMORY_CACHE_TTL_SECONDS 未访问（TTL淘汰）
    """
    now = time.time()
    # TTL 淘汰：清理超时未访问的条目
    expired = [k for k, v in _recently_used_memories.items()
               if now - v[1] > _MEMORY_CACHE_TTL_SECONDS]
    for k in expired:
        del _recently_used_memories[k]
    if expired:
        logger.debug(f"[记忆缓存] TTL淘汰 {len(expired)} 个过期条目")

    # LRU 淘汰：超出容量限制
    if len(_recently_used_memories) <= _MEMORY_CACHE_MAX_USERS:
        return
    excess = len(_recently_used_memories) - _MEMORY_CACHE_MAX_USERS
    keys = list(_recently_used_memories.keys())
    for k in keys[:excess]:
        del _recently_used_memories[k]


def _is_memory_relevant(memory_content: str, user_msg: str) -> bool:
    """判断记忆是否与当前用户消息相关（中文 + 英文关键词）。"""
    # CJK 关键词
    user_keywords = set(re.findall(r'[一-鿿]{2,6}', user_msg))
    for kw in user_keywords:
        if kw in memory_content:
            return True
    mem_keywords = set(re.findall(r'[一-鿿]{2,6}', memory_content))
    for kw in mem_keywords:
        if kw in user_msg:
            return True
    # 英文关键词：3个字母以上的单词
    en_user = set(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', user_msg))
    en_mem = set(w.lower() for w in re.findall(r'[a-zA-Z]{3,}', memory_content))
    if en_user & en_mem:
        return True
    return False


async def _get_relevant_memories(user_id: str, session_id: str, current_msg: str, limit: int = 5) -> List[str]:
    """获取相关记忆提示语。关键词 + 语义混合检索 + RRF 融合 + 加权随机选择。

    两路检索:
    1. 关键词: 置信度排序的 top-N 标签 → CJK 关键词重叠检查
    2. 语义: GLM embedding 余弦相似度 → top-10

    通过 RRF (Reciprocal Rank Fusion) 融合两路结果。
    """
    _cleanup_memory_cache()
    try:
        now = datetime.now().timestamp()
        rows = await get_relevant_memory_tags(user_id, limit * 3)

        cooldown_list = _recently_used_memories.get(user_id, ([], 0))[0]

        # ===== 第1路: 关键词检索 =====
        kw_candidates = []
        for row in rows:
            content = row["content"]
            if content in cooldown_list:
                continue
            days_ago = (now - row["last_used"]) / 86400
            if days_ago > 14 and (row["confidence"] if row["confidence"] else 0.5) < 0.3:
                continue
            if _is_memory_relevant(content, current_msg):
                kw_candidates.append((
                    row["id"],
                    content,
                    row["confidence"] if row["confidence"] else 0.5,
                ))

        # ===== 第2路: 语义检索 =====
        sem_ids = set()
        sem_candidates = []
        try:
            from .memory_embed import semantic_search_memories
            sem_results = await semantic_search_memories(user_id, current_msg, top_k=10)
            for tag_id, sim_score in sem_results:
                sem_ids.add(tag_id)
                # 找到对应的 content
                for row in rows:
                    if row["id"] == tag_id:
                        content = row["content"]
                        if content not in cooldown_list:
                            sem_candidates.append((tag_id, content, sim_score))
                        break
        except ImportError:
            logger.debug("[记忆] memory_embed 模块未就绪，跳过语义检索")
        except Exception as e:
            logger.debug(f"[记忆] 语义检索跳过: {e}")

        # ===== RRF 融合 (3C: 自适应权重) =====
        if sem_candidates and kw_candidates:
            try:
                from .memory_embed import adaptive_rrf_merge
                kw_ranked = [(cid, conf) for cid, _, conf in kw_candidates[:10]]
                sem_ranked = [(cid, sim) for cid, _, sim in sem_candidates[:10]]
                merged = adaptive_rrf_merge(kw_ranked, sem_ranked, query_text=current_msg, top_k=limit + 2)

                # 构建融合后的 candidates 列表
                all_by_id = {}
                for cid, content, conf in kw_candidates:
                    all_by_id[cid] = (content, conf)
                for cid, content, sim in sem_candidates:
                    if cid not in all_by_id:
                        all_by_id[cid] = (content, sim)

                candidates = [(all_by_id[cid][0], all_by_id[cid][1])
                              for cid, _ in merged if cid in all_by_id]
            except ImportError:
                # B7 fix: 当 RRF 不可用时，合并两路结果（去重后按分数排序）
                all_by_id = {}
                for cid, content, conf in kw_candidates:
                    all_by_id[cid] = (content, conf)
                for cid, content, sim in sem_candidates:
                    if cid not in all_by_id:
                        all_by_id[cid] = (content, sim)
                    else:
                        existing_content, existing_score = all_by_id[cid]
                        if sim > existing_score:
                            all_by_id[cid] = (existing_content, sim)
                candidates = list(all_by_id.values())
        else:
            # 只有一路有结果时直接使用
            candidates = [(c, conf) for _, c, conf in kw_candidates]
            if not candidates:
                candidates = [(c, sim) for _, c, sim in sem_candidates]

        # ===== 加权随机选择 (B6: 支持多条记忆) =====
        if candidates:
            # 去重：避免返回内容相同的记忆
            seen = set()
            unique_candidates = []
            for content, conf in candidates:
                if content not in seen:
                    seen.add(content)
                    unique_candidates.append((content, conf))

            weights = [max(0.1, c) for _, c in unique_candidates]
            total = sum(weights)
            probs = [w / total for w in weights]
            k = min(MAX_MEMORY_PER_REPLY, len(unique_candidates))
            selected_indices = random.choices(range(len(unique_candidates)), weights=probs, k=k)

            results = []
            async with _recently_used_memories_lock:
                for idx in selected_indices:
                    selected_content = unique_candidates[idx][0]

                    if user_id not in _recently_used_memories:
                        _recently_used_memories[user_id] = ([], time.time())
                    tags_list, _ = _recently_used_memories[user_id]
                    tags_list.append(selected_content)
                    _recently_used_memories[user_id] = (tags_list[-MEMORY_COOLDOWN_ROUNDS:], time.time())

                safe_task(boost_memory_tag(user_id, selected_content))
                results.append(selected_content)

            # 去重返回（choices 可能有重复）
            unique_results = list(dict.fromkeys(results))
            return [f"[{c}]" for c in unique_results]

        # ===== 摘要记忆 fallback =====
        summary = await get_memory_summary(session_id)
        if summary:
            summary_keywords = set(re.findall(r'[一-鿿]{2,}', summary))
            user_keywords = set(re.findall(r'[一-鿿]{2,}', current_msg))
            has_overlap = bool(summary_keywords & user_keywords)
            if has_overlap or random.random() < 0.8:
                return [f"[之前聊过的：{summary[:150]}]"]

        return []
    except Exception as e:
        logger.error(f"[记忆] 检索失败: {e}")
        return []


async def _extract_memory_tags(user_id: str, session_id: str, user_msg: str, reply_text: str):
    """从对话中提取用户标签。新标签初始置信度 0.5。"""
    # Phase 4：群聊也提取记忆标签（范围限定为关键事实）
    is_group = isinstance(session_id, str) and session_id.startswith("group_")
    if is_group:
        # 群聊只提取明确的关键事实（避免噪音）
        if not any(k in user_msg + reply_text for k in ["喜欢", "讨厌", "怕", "不吃", "名字", "生日", "住", "工作", "专业", "养了", "我是"]):
            return

    prompt = f"""从以下对话中，提取关于用户的客观关键信息（偏好、事实、禁忌、情绪）。
只输出 JSON 数组，不要有任何其他文字。没有就输出空数组 []。

用户说：{user_msg}
你回复：{reply_text}

示例输出：
[
  {{"type": "preference", "content": "用户喜欢喝冰美式"}},
  {{"type": "fact", "content": "用户养了一只叫橘子的猫"}},
  {{"type": "taboo", "content": "用户讨厌被叫全名"}}
]"""
    try:
        messages = [
            {"role": "system", "content": "你是一个对话记忆提取助手，只输出JSON数组。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        from .utils import clean_json_text
        clean = clean_json_text(raw)
        tags = json.loads(clean)
        if not isinstance(tags, list):
            return
        ids = await save_memory_tags(user_id, tags)
        logger.info(f"[记忆] 提取并保存了 {len(tags)} 条标签")
        # 异步生成 embedding（新标签）
        if ids:
            safe_task(_embed_new_tags(ids, tags))
    except Exception as e:
        logger.info(f"[记忆] 提取失败（非关键错误）: {e}")


async def _embed_new_tags(ids: list, tags: list):
    """为新增的记忆标签异步生成 embedding。"""
    try:
        from .memory_embed import ensure_tag_embedding
        for tag_id, tag in zip(ids, tags):
            content = tag.get("content", "")
            if content:
                await ensure_tag_embedding(tag_id, content)
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"[记忆] embedding 生成跳过: {e}")
