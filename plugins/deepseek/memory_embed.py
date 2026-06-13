"""语义记忆检索模块 — 基于智谱 GLM Embedding API。

提供记忆标签的语义向量化存储和余弦相似度检索，
配合 memory.py 中的关键词检索使用 RRF (Reciprocal Rank Fusion) 合并结果。

Embedding 模型: GLM embedding-2（免费，1024-dim）
存储格式: SQLite BLOB，int8 量化（每维 1 byte，总计 1024 bytes/条）
"""

import asyncio
import logging
import struct
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import aiohttp

logger = logging.getLogger("deepseek.memory_embed")

# GLM Embedding API
_EMBED_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
_EMBED_MODEL = "embedding-2"
_EMBED_DIM = 1024

# 内存缓存（避免同一条文本重复调用 API）
_embed_cache: Dict[str, List[float]] = {}
_CACHE_MAX_SIZE = 500


# ============================================================
# Embedding API 调用
# ============================================================


async def get_embedding(text: str) -> Optional[List[float]]:
    """获取单条文本的 embedding 向量。

    带内存缓存，相同文本不重复请求 API。
    """
    if not text or not text.strip():
        return None

    text = text.strip()
    if text in _embed_cache:
        return _embed_cache[text]

    from .config import GLM_API_KEY
    if not GLM_API_KEY:
        logger.debug("[Embed] GLM_API_KEY 未配置")
        return None

    try:
        from .api import get_http_session
        session = await get_http_session()
        payload = {"model": _EMBED_MODEL, "input": text}
        headers = {
            "Authorization": f"Bearer {GLM_API_KEY}",
            "Content-Type": "application/json",
        }
        async with session.post(
            _EMBED_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.warning(f"[Embed] API 状态码 {resp.status}: {await resp.text()}")
                return None
            data = await resp.json()
            emb = data.get("data", [{}])[0].get("embedding")
            if emb and len(emb) == _EMBED_DIM:
                _add_to_cache(text, emb)
                return emb
            logger.warning(f"[Embed] 返回向量维度异常: {len(emb) if emb else 0}")
            return None
    except asyncio.TimeoutError:
        logger.warning("[Embed] API 超时 (10s)")
        return None
    except Exception as e:
        logger.warning(f"[Embed] 异常: {type(e).__name__}: {e}")
        return None


async def get_embeddings(texts: List[str]) -> List[Optional[List[float]]]:
    """批量获取 embedding（逐个调用，但可利用缓存）。"""
    tasks = [get_embedding(t) for t in texts]
    return await asyncio.gather(*tasks)


def _add_to_cache(text: str, emb: List[float]) -> None:
    """写入内存缓存，超限时淘汰最旧条目。"""
    if len(_embed_cache) >= _CACHE_MAX_SIZE:
        oldest = next(iter(_embed_cache))
        del _embed_cache[oldest]
    _embed_cache[text] = emb


# ============================================================
# 向量量化（float32 → int8，节省 75% 存储空间）
# ============================================================


def quantize_embedding(emb: List[float]) -> bytes:
    """将 float32 embedding 量化为 int8 字节序列。

    每维映射到 [0, 255]，1 byte/维 → 1024 bytes total。
    """
    if not emb:
        return b""
    min_val = min(emb)
    max_val = max(emb)
    _range = max_val - min_val or 0.001  # 防止除零
    quantized = bytearray(len(emb))
    for i, v in enumerate(emb):
        # 归一化到 [0, 1] 再映射到 [0, 255]
        normalized = (v - min_val) / _range
        quantized[i] = max(0, min(255, int(normalized * 255)))
    # 4 bytes 存 min, 4 bytes 存 max, 其余为量化值
    header = struct.pack("<ff", min_val, max_val)
    return header + bytes(quantized)


def dequantize_embedding(data: bytes) -> Optional[List[float]]:
    """将 int8 量化字节还原为 float32 embedding。"""
    if not data or len(data) < 8:
        return None
    min_val, max_val = struct.unpack("<ff", data[:8])
    quantized = data[8:]
    _range = max_val - min_val or 0.001
    emb = []
    for b in quantized:
        normalized = b / 255.0
        emb.append(min_val + normalized * _range)
    return emb


# ============================================================
# 余弦相似度计算
# ============================================================


def cosine_similarity(a: List[float], b: List[float]) -> float:
    """计算两个向量的余弦相似度 [0, 1]。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ============================================================
# 语义检索 + RRF 融合
# ============================================================


async def semantic_search_memories(
    user_id: str,
    query_text: str,
    top_k: int = 10,
) -> List[Tuple[int, float]]:
    """对用户的记忆标签做语义检索，返回 (tag_id, similarity_score) 列表。

    Args:
        user_id: 用户 ID
        query_text: 当前消息/查询文本
        top_k: 返回数量

    Returns:
        按相似度降序排列的 (tag_id, score) 列表
    """
    if not query_text or not query_text.strip():
        return []

    # 获取查询 embedding
    query_emb = await get_embedding(query_text)
    if not query_emb:
        return []

    # 获取该用户所有有 embedding 的记忆标签（使用异步数据库 API）
    from .db_core import get_db
    db = await get_db()
    rows = []
    async with db.execute(
        "SELECT id, embedding FROM memory_tags "
        "WHERE user_id = ? AND embedding IS NOT NULL "
        "ORDER BY confidence DESC LIMIT 200",
        (user_id,),
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        return []

    # 计算余弦相似度
    results = []
    for row in rows:
        tag_id = row[0]
        emb_blob = row[1]
        if not emb_blob:
            continue
        tag_emb = dequantize_embedding(emb_blob)
        if not tag_emb:
            continue
        sim = cosine_similarity(query_emb, tag_emb)
        if sim > 0.3:  # 最小相似度阈值
            results.append((tag_id, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def rrf_merge(
    list_a: List[Tuple[int, float]],
    list_b: List[Tuple[int, float]],
    k: int = 60,
    top_k: int = 10,
) -> List[Tuple[int, float]]:
    """Reciprocal Rank Fusion: 融合两个排序列表。

    参数:
        list_a, list_b: (id, score) 列表（已按 score 降序）
        k: RRF 平滑常数（默认 60）
        top_k: 返回条数

    返回: 融合后按 RRF 分数降序的 (id, score) 列表
    """
    scores: Dict[int, float] = {}

    for rank, (item_id, _) in enumerate(list_a):
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)

    for rank, (item_id, _) in enumerate(list_b):
        scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged[:top_k]


def adaptive_rrf_merge(
    list_a: List[Tuple[int, float]],
    list_b: List[Tuple[int, float]],
    query_text: str = "",
    top_k: int = 10,
) -> List[Tuple[int, float]]:
    """自适应 RRF 融合（借鉴 vstash arXiv:2604.15484）。

    根据查询长度动态调整 RRF k 值：
    - 长查询（>10字）：k=30 — 降低平滑，提高关键词匹配权重
    - 中等查询（5-10字）：k=60 — 默认平衡
    - 短查询（<5字）：k=120 — 高平滑，提高语义匹配权重

    原理: 短查询关键词少，语义信号更重要；长查询关键词多，精确匹配更重要。
    """
    query_len = len(query_text) if query_text else 5
    if query_len > 10:
        k = 30
    elif query_len < 5:
        k = 120
    else:
        k = 60

    logger.debug(f"[RRF] 自适应 k={k} (查询长度={query_len})")
    return rrf_merge(list_a, list_b, k=k, top_k=top_k)


# ============================================================
# Embedding 存储管理
# ============================================================


async def ensure_tag_embedding(tag_id: int, content: str) -> bool:
    """为指定记忆标签生成并存储 embedding（如果尚未存储）。

    Returns:
        True 如果成功存储或已存在
    """
    if not content:
        return False

    # 检查是否已有 embedding
    from .db_memories import _fetch_one, _execute
    row = _fetch_one(
        "SELECT embedding FROM memory_tags WHERE id = ?", (tag_id,)
    )
    if row and row[0]:
        return True  # 已有

    emb = await get_embedding(content)
    if not emb:
        return False

    quantized = quantize_embedding(emb)
    _execute(
        "UPDATE memory_tags SET embedding = ? WHERE id = ?",
        (quantized, tag_id),
    )
    logger.debug(f"[Embed] 标签 {tag_id} embedding 已存储")
    return True


async def rebuild_all_embeddings(clear_cache: bool = False) -> int:
    """为所有缺少 embedding 的记忆标签生成并存储向量。

    Args:
        clear_cache: 是否清除已有 embedding 重新生成

    Returns:
        成功生成的条数
    """
    from .db_memories import _fetch_all, _execute

    if clear_cache:
        _execute("UPDATE memory_tags SET embedding = NULL")
        logger.info("[Embed] 已清除所有 embedding，准备重建")

    rows = _fetch_all(
        "SELECT id, content FROM memory_tags WHERE embedding IS NULL LIMIT 500"
    )

    count = 0
    for row in rows:
        tag_id, content = row[0], row[1]
        if await ensure_tag_embedding(tag_id, content):
            count += 1
            # 每 50 条打一次日志
            if count % 50 == 0:
                logger.info(f"[Embed] 重建进度: {count}/{len(rows)}")

    logger.info(f"[Embed] 重建完成: {count}/{len(rows)} 条")
    return count
