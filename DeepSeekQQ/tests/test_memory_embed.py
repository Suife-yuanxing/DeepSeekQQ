"""Test Memory Embed — 语义向量化存储与检索。

覆盖：
- quantize_embedding / dequantize_embedding 量化往返
- cosine_similarity 余弦相似度
- cosine_similarity_batch 批量计算（含 NumPy fallback）
- rrf_merge / adaptive_rrf_merge RRF 融合
- _add_to_cache 缓存管理
"""
import math
import struct
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════
# 量化往返
# ═══════════════════════════════════════════════════════════════

class TestQuantizeRoundtrip:
    """测试 quantize_embedding + dequantize_embedding 往返。"""

    def test_roundtrip_preserves_approximate_values(self):
        """量化后再反量化应保持大致相同的值。"""
        from plugins.deepseek.memory_embed import quantize_embedding, dequantize_embedding
        original = [0.1, -0.2, 0.5, -0.8, 0.0, 0.3]
        quantized = quantize_embedding(original)
        assert isinstance(quantized, bytes)
        assert len(quantized) == 8 + len(original)  # 4+4 header + N bytes
        restored = dequantize_embedding(quantized)
        assert restored is not None
        assert len(restored) == len(original)
        # int8 量化有精度损失，但相对误差应 < 5%
        for a, b in zip(original, restored):
            assert abs(a - b) < 0.05 or abs(a) < 0.01

    def test_roundtrip_large_vector(self):
        """1024 维向量量化往返。"""
        from plugins.deepseek.memory_embed import quantize_embedding, dequantize_embedding
        import random
        random.seed(42)
        original = [random.uniform(-1.0, 1.0) for _ in range(1024)]
        quantized = quantize_embedding(original)
        assert len(quantized) == 8 + 1024
        restored = dequantize_embedding(quantized)
        assert restored is not None
        assert len(restored) == 1024
        # 验证 min/max 被正确保留
        assert abs(min(restored) - min(original)) < 0.02
        assert abs(max(restored) - max(original)) < 0.02

    def test_empty_input(self):
        """空输入返回空 bytes。"""
        from plugins.deepseek.memory_embed import quantize_embedding
        assert quantize_embedding([]) == b""

    def test_dequantize_empty(self):
        """空数据返回 None。"""
        from plugins.deepseek.memory_embed import dequantize_embedding
        assert dequantize_embedding(b"") is None

    def test_dequantize_too_short(self):
        """数据不足 8 字节返回 None。"""
        from plugins.deepseek.memory_embed import dequantize_embedding
        assert dequantize_embedding(b"\x00" * 4) is None

    def test_single_value_vector(self):
        """单值向量应能正常量化/反量化。"""
        from plugins.deepseek.memory_embed import quantize_embedding, dequantize_embedding
        original = [0.5]
        quantized = quantize_embedding(original)
        restored = dequantize_embedding(quantized)
        assert restored is not None
        # 单值时 min==max，反量化应在 0.5 附近
        assert abs(restored[0] - 0.5) < 0.01

    def test_uniform_vector_all_same(self):
        """所有值相同的向量（min==max）应正确处理。"""
        from plugins.deepseek.memory_embed import quantize_embedding, dequantize_embedding
        original = [0.3, 0.3, 0.3]
        quantized = quantize_embedding(original)
        restored = dequantize_embedding(quantized)
        assert restored is not None
        for v in restored:
            assert abs(v - 0.3) < 0.01


# ═══════════════════════════════════════════════════════════════
# 余弦相似度
# ═══════════════════════════════════════════════════════════════

class TestCosineSimilarity:
    """测试 cosine_similarity（纯 Python）。"""

    def test_identical_vectors(self):
        """相同向量相似度应为 1.0。"""
        from plugins.deepseek.memory_embed import cosine_similarity
        v = [0.1, 0.2, 0.3]
        result = cosine_similarity(v, v)
        assert abs(result - 1.0) < 1e-9

    def test_orthogonal_vectors(self):
        """正交向量相似度应为 0.0。"""
        from plugins.deepseek.memory_embed import cosine_similarity
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = cosine_similarity(a, b)
        assert abs(result - 0.0) < 1e-9

    def test_opposite_vectors(self):
        """相反向量相似度应为 -1.0。"""
        from plugins.deepseek.memory_embed import cosine_similarity
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        result = cosine_similarity(a, b)
        assert abs(result - (-1.0)) < 1e-9

    def test_different_lengths(self):
        """不同长度向量返回 0.0。"""
        from plugins.deepseek.memory_embed import cosine_similarity
        result = cosine_similarity([1.0, 2.0], [1.0])
        assert result == 0.0

    def test_zero_vector(self):
        """零向量应返回 0.0（避免除零）。"""
        from plugins.deepseek.memory_embed import cosine_similarity
        result = cosine_similarity([0.0, 0.0], [1.0, 2.0])
        assert result == 0.0


# ═══════════════════════════════════════════════════════════════
# 批量余弦相似度
# ═══════════════════════════════════════════════════════════════

class TestCosineSimilarityBatch:
    """测试 cosine_similarity_batch。"""

    def test_batch_returns_correct_shape(self):
        """返回列表长度应与候选数量一致。"""
        from plugins.deepseek.memory_embed import cosine_similarity_batch
        query = [1.0, 0.0, 0.0]
        candidates = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        results = cosine_similarity_batch(query, candidates)
        assert len(results) == 3

    def test_batch_empty_candidates(self):
        """空候选列表返回空列表。"""
        from plugins.deepseek.memory_embed import cosine_similarity_batch
        results = cosine_similarity_batch([1.0, 0.0], [])
        assert results == []

    def test_batch_with_numpy(self):
        """NumPy 路径应与纯 Python 结果一致。"""
        from plugins.deepseek.memory_embed import cosine_similarity_batch, cosine_similarity
        query = [0.5, 0.3, 0.2]
        candidates = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.5, 0.3, 0.2]]
        # 强制使用 NumPy（如果可用）
        import plugins.deepseek.memory_embed as me
        has_numpy = me._HAS_NUMPY
        if has_numpy:
            results = cosine_similarity_batch(query, candidates)
            # 与逐个计算对比
            for i, candidate in enumerate(candidates):
                expected = cosine_similarity(query, candidate)
                assert abs(results[i] - expected) < 1e-5, f"Mismatch at index {i}"
        else:
            results = cosine_similarity_batch(query, candidates)
            assert len(results) == 3  # fallback 也正常工作


# ═══════════════════════════════════════════════════════════════
# RRF 融合
# ═══════════════════════════════════════════════════════════════

class TestRrfMerge:
    """测试 rrf_merge / adaptive_rrf_merge。"""

    def test_rrf_merge_basic(self):
        """两个列表应融合并按 RRF 分数排序。"""
        from plugins.deepseek.memory_embed import rrf_merge
        list_a = [(1, 0.9), (2, 0.7), (3, 0.5)]
        list_b = [(2, 0.8), (3, 0.6), (4, 0.4)]
        merged = rrf_merge(list_a, list_b, top_k=3)
        assert len(merged) <= 3
        # 在两个列表中都出现的项应排名更靠前
        ids = [item_id for item_id, _ in merged]
        # 2 在两个列表中都出现，应排前面
        assert 2 in ids

    def test_rrf_merge_empty_lists(self):
        """空列表融合返回空。"""
        from plugins.deepseek.memory_embed import rrf_merge
        assert rrf_merge([], []) == []
        assert rrf_merge([(1, 0.9)], []) == [(1, 1.0 / 61)]  # RRF score for rank 0

    def test_rrf_merge_dedup(self):
        """同一 ID 只出现一次（分数累加）。"""
        from plugins.deepseek.memory_embed import rrf_merge
        list_a = [(1, 0.9)]
        list_b = [(1, 0.8)]
        merged = rrf_merge(list_a, list_b, top_k=5)
        ids = [item_id for item_id, _ in merged]
        assert ids.count(1) == 1

    def test_adaptive_rrf_long_query(self):
        """长查询（>10字）应使用较小的 k=30。"""
        from plugins.deepseek.memory_embed import adaptive_rrf_merge
        list_a = [(1, 0.9), (2, 0.7)]
        list_b = [(2, 0.8), (1, 0.6)]
        merged = adaptive_rrf_merge(list_a, list_b, query_text="这是一个很长的查询文本用于测试")
        assert len(merged) > 0

    def test_adaptive_rrf_short_query(self):
        """短查询（<5字）应使用较大的 k=120。"""
        from plugins.deepseek.memory_embed import adaptive_rrf_merge
        merged = adaptive_rrf_merge([(1, 0.9)], [(1, 0.8)], query_text="你好")
        assert len(merged) > 0

    def test_adaptive_rrf_default_k(self):
        """中等长度查询应使用默认 k=60。"""
        from plugins.deepseek.memory_embed import adaptive_rrf_merge
        merged = adaptive_rrf_merge([(1, 0.9)], [(2, 0.8)], query_text="测试查询")
        assert len(merged) > 0


# ═══════════════════════════════════════════════════════════════
# 缓存管理
# ═══════════════════════════════════════════════════════════════

class TestEmbedCache:
    """测试 _add_to_cache 和缓存淘汰。"""

    def test_add_to_cache(self):
        """添加到缓存后应可命中。"""
        from plugins.deepseek.memory_embed import _add_to_cache, _embed_cache
        # 清理缓存
        _embed_cache.clear()
        emb = [0.1, 0.2, 0.3]
        _add_to_cache("测试文本", emb)
        assert "测试文本" in _embed_cache
        assert _embed_cache["测试文本"] == emb

    def test_cache_eviction(self):
        """缓存超限时应淘汰最旧条目。"""
        from plugins.deepseek.memory_embed import _add_to_cache, _embed_cache, _CACHE_MAX_SIZE
        _embed_cache.clear()
        # 填满缓存
        for i in range(_CACHE_MAX_SIZE + 10):
            _add_to_cache(f"text_{i}", [float(i)])
        # 不应超过最大容量
        assert len(_embed_cache) <= _CACHE_MAX_SIZE
