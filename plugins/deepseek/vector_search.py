"""向量语义检索 — 轻量级混合召回。

替代纯关键词匹配，支持：
1. 默认模式：字符 n-gram + IDF 加权 + 余弦相似度（零依赖）
2. 增强模式：ChromaDB + sentence-transformers（pip install chromadb sentence-transformers）

核心思路借鉴 WTFLLM 项目：混合 dense（语义向量）+ sparse（关键词BM25）+ 时间衰减。
"""

import math
import re
import time
from collections import defaultdict
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger

# ============================================================
# 中文文本处理
# ============================================================

# CJK 统一表意文字基本区间
_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
# 中文标点
_CN_PUNCT = set("，。！？；：""''（）【】《》、…—～·")


def _tokenize_ngrams(text: str, n: int = 2) -> List[str]:
    """提取中文 n-gram token。

    对于中文文本，2-3 gram 既能捕捉词组又不会太稀疏。
    同时保留完整的数字和英文单词。
    """
    tokens = []
    # 提取中文字符序列
    chars = _CJK_RE.findall(text)
    # character bigrams + trigrams
    for i in range(len(chars) - n + 1):
        tokens.append("".join(chars[i : i + n]))
    # 保留完整的中文词（3-4 gram 作为词组）
    for i in range(len(chars) - 3 + 1):
        tokens.append("".join(chars[i : i + 3]))
    # 提取英文/数字 token
    for token in re.findall(r"[a-zA-Z0-9]{2,}", text):
        tokens.append(token.lower())
    return tokens


# ============================================================
# TF-IDF 向量化（轻量级，零依赖）
# ============================================================


class LightweightVectorizer:
    """轻量级 TF-IDF 向量化器。

    纯 Python 实现，无需 sklearn/scipy。适合中小规模文本（<10000条）。
    支持增量添加文档和实时查询。
    """

    def __init__(self):
        self._documents: Dict[str, str] = {}  # doc_id -> text
        self._idf: Dict[str, float] = {}  # token -> idf
        self._doc_count = 0
        self._dirty = False  # 是否需要重新计算 IDF

    def add_document(self, doc_id: str, text: str):
        """添加文档到索引。"""
        self._documents[doc_id] = text
        self._doc_count += 1
        self._dirty = True

    def remove_document(self, doc_id: str):
        """移除文档。"""
        if doc_id in self._documents:
            del self._documents[doc_id]
            self._doc_count -= 1
            self._dirty = True

    def _recalculate_idf(self):
        """重新计算 IDF 值。"""
        if not self._dirty:
            return
        df = defaultdict(int)
        for text in self._documents.values():
            unique_tokens = set(_tokenize_ngrams(text))
            for token in unique_tokens:
                df[token] += 1
        # IDF = log((N + 1) / (df + 1)) + 1 (平滑版本)
        N = max(1, self._doc_count)
        self._idf = {
            token: math.log((N + 1) / (count + 1)) + 1
            for token, count in df.items()
        }
        self._dirty = False

    def _vectorize(self, text: str) -> Dict[str, float]:
        """将文本转换为稀疏 TF-IDF 向量。"""
        tokens = _tokenize_ngrams(text)
        if not tokens:
            return {}
        # TF
        tf = defaultdict(float)
        for t in tokens:
            tf[t] += 1.0
        # 归一化 TF
        max_tf = max(tf.values()) if tf else 1.0
        # TF-IDF
        self._recalculate_idf()
        result = {}
        for token, count in tf.items():
            tf_norm = count / max_tf
            idf = self._idf.get(token, 1.0)
            result[token] = tf_norm * idf
        return result

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """搜索最相似的文档，返回 [(doc_id, score), ...]"""
        if not self._documents:
            return []
        query_vec = self._vectorize(query)
        if not query_vec:
            return []
        # 计算余弦相似度
        scores = []
        query_norm = math.sqrt(sum(v**2 for v in query_vec.values()))
        for doc_id, text in self._documents.items():
            doc_vec = self._vectorize(text)
            if not doc_vec:
                continue
            doc_norm = math.sqrt(sum(v**2 for v in doc_vec.values()))
            if query_norm == 0 or doc_norm == 0:
                continue
            dot_product = sum(
                query_vec.get(token, 0) * doc_vec.get(token, 0)
                for token in set(query_vec) | set(doc_vec)
            )
            sim = dot_product / (query_norm * doc_norm)
            if sim > 0:
                scores.append((doc_id, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]

    def clear(self):
        """清空索引。"""
        self._documents.clear()
        self._idf.clear()
        self._doc_count = 0
        self._dirty = False


# ============================================================
# 混合召回引擎
# ============================================================


class HybridRetriever:
    """混合召回：语义向量 + 关键词 + 时间衰减。

    使用方式：
        retriever = HybridRetriever()
        retriever.index("mem_1", "用户喜欢喝冰美式咖啡", {"type": "preference"})
        results = retriever.search("咖啡", top_k=5)
    """

    def __init__(self):
        self._vectorizer = LightweightVectorizer()
        # 元数据
        self._metadata: Dict[str, Dict[str, Any]] = {}
        # 关键词倒排索引
        self._keyword_index: Dict[str, set] = defaultdict(set)

    def index(self, doc_id: str, text: str, metadata: Dict[str, Any] = None, timestamp: float = None):
        """索引文档。"""
        self._vectorizer.add_document(doc_id, text)
        self._metadata[doc_id] = {
            "text": text,
            "timestamp": timestamp or time.time(),
            **(metadata or {}),
        }
        # 更新关键词索引
        for token in _tokenize_ngrams(text):
            self._keyword_index[token].add(doc_id)

    def remove(self, doc_id: str):
        """移除文档。"""
        self._vectorizer.remove_document(doc_id)
        self._metadata.pop(doc_id, None)
        for token_docs in self._keyword_index.values():
            token_docs.discard(doc_id)

    def search(
        self,
        query: str,
        top_k: int = 5,
        semantic_weight: float = 0.5,
        keyword_weight: float = 0.3,
        time_weight: float = 0.2,
        time_halflife: float = 86400 * 7,  # 7天半衰期
    ) -> List[Dict[str, Any]]:
        """混合检索。

        Args:
            query: 查询文本
            top_k: 返回数量
            semantic_weight: 语义相似度权重
            keyword_weight: 关键词匹配权重
            time_weight: 时间衰减权重
            time_halflife: 时间衰减半衰期（秒）

        Returns:
            [{"doc_id": str, "text": str, "score": float, "metadata": dict}, ...]
        """
        now = time.time()
        combined: Dict[str, float] = defaultdict(float)

        # 1. 语义向量检索
        if semantic_weight > 0:
            semantic_results = self._vectorizer.search(query, top_k=len(self._metadata))
            max_sem = max((s for _, s in semantic_results), default=1.0)
            for doc_id, score in semantic_results:
                combined[doc_id] += semantic_weight * (score / max(max_sem, 0.001))

        # 2. 关键词匹配
        if keyword_weight > 0:
            query_tokens = set(_tokenize_ngrams(query))
            keyword_scores: Dict[str, int] = defaultdict(int)
            for token in query_tokens:
                for doc_id in self._keyword_index.get(token, set()):
                    keyword_scores[doc_id] += 1
            max_kw = max(keyword_scores.values(), default=1)
            for doc_id, hits in keyword_scores.items():
                combined[doc_id] += keyword_weight * (hits / max(max_kw, 1))

        # 3. 时间衰减（越新的记忆权重越高）
        if time_weight > 0:
            for doc_id in combined:
                meta = self._metadata.get(doc_id, {})
                ts = meta.get("timestamp", now)
                age = now - ts
                decay = 2.0 ** (-age / time_halflife)
                combined[doc_id] += time_weight * decay

        # 排序 & 格式化
        sorted_ids = sorted(combined.keys(), key=lambda x: combined[x], reverse=True)[:top_k]
        results = []
        for doc_id in sorted_ids:
            meta = self._metadata.get(doc_id, {})
            results.append({
                "doc_id": doc_id,
                "text": meta.get("text", ""),
                "score": round(combined[doc_id], 4),
                "metadata": meta,
            })
        return results

    def clear(self):
        """清空索引。"""
        self._vectorizer.clear()
        self._metadata.clear()
        self._keyword_index.clear()

    def __len__(self):
        return len(self._metadata)


# ============================================================
# 全局检索单例（按用户隔离）
# ============================================================

_user_retrievers: Dict[str, HybridRetriever] = {}


def get_user_retriever(user_id: str) -> HybridRetriever:
    """获取用户的混合检索引擎。"""
    if user_id not in _user_retrievers:
        _user_retrievers[user_id] = HybridRetriever()
    return _user_retrievers[user_id]


async def index_user_memories(user_id: str):
    """将用户的所有记忆标签索引进向量引擎。

    应在 bot 启动或用户首次交互时调用。
    """
    from .db_tags import get_all_memory_tags_for_user

    retriever = get_user_retriever(user_id)
    # 避免重复索引
    if len(retriever) > 0:
        return

    try:
        tags = await get_all_memory_tags_for_user(user_id)
        for tag in tags:
            doc_id = f"tag_{tag.get('id', '')}"
            text = f"[{tag.get('tag_type', '')}] {tag.get('content', '')}"
            retriever.index(
                doc_id=doc_id,
                text=text,
                metadata={
                    "type": tag.get("tag_type", ""),
                    "confidence": tag.get("confidence", 0.5),
                    "weight": tag.get("weight", 1.0),
                },
                timestamp=tag.get("last_used", time.time()),
            )
        if tags:
            logger.info(f"[向量检索] 已为用户 {user_id[:8]} 索引 {len(tags)} 条记忆")
    except Exception as e:
        logger.warning(f"[向量检索] 索引用户 {user_id[:8]} 记忆失败: {e}")


def hybrid_search_memories(
    user_id: str, query: str, top_k: int = 5
) -> List[Dict[str, Any]]:
    """搜索用户记忆（混合召回）。

    供 memory.py 调用，替代纯关键词匹配。
    """
    retriever = get_user_retriever(user_id)
    if len(retriever) == 0:
        return []
    return retriever.search(query, top_k=top_k)


# ============================================================
# ChromaDB 增强模式（可选）
# ============================================================

_HAS_CHROMADB = False
_chroma_client = None
_chroma_collections: Dict[str, Any] = {}

try:
    import chromadb

    _HAS_CHROMADB = True
except ImportError:
    pass


def _get_chroma_collection(user_id: str):
    """获取或创建用户的 ChromaDB 集合。"""
    global _chroma_client
    if _chroma_client is None:
        import os

        persist_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "chroma")
        _chroma_client = chromadb.PersistentClient(path=persist_dir)

    safe_id = user_id.replace("/", "_").replace("\\", "_")
    collection_name = f"memories_{safe_id}"
    if collection_name not in _chroma_collections:
        _chroma_collections[collection_name] = _chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
    return _chroma_collections[collection_name]


async def chroma_index_memories(user_id: str):
    """使用 ChromaDB 索引记忆（需要 pip install chromadb）。"""
    if not _HAS_CHROMADB:
        logger.warning("[ChromaDB] chromadb 未安装，回退到轻量级模式")
        return

    from .db_tags import get_all_memory_tags_for_user

    try:
        collection = _get_chroma_collection(user_id)
        tags = await get_all_memory_tags_for_user(user_id)
        if not tags:
            return

        # 批量添加
        ids = [f"tag_{t.get('id', i)}" for i, t in enumerate(tags)]
        documents = [f"[{t.get('tag_type', '')}] {t.get('content', '')}" for t in tags]
        metadatas = [
            {
                "type": t.get("tag_type", ""),
                "confidence": str(t.get("confidence", 0.5)),
                "weight": str(t.get("weight", 1.0)),
            }
            for t in tags
        ]

        # 删除旧数据后重新添加
        existing = collection.get()["ids"]
        if existing:
            collection.delete(ids=existing)

        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.info(f"[ChromaDB] 已为用户 {user_id[:8]} 索引 {len(tags)} 条记忆")
    except Exception as e:
        logger.warning(f"[ChromaDB] 索引失败: {e}")
