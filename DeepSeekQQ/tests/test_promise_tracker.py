# -*- coding: utf-8 -*-
"""promise_tracker 测试 — 承诺提取、到期估算、遗忘机制。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import time
from plugins.deepseek.promise_tracker import (
    extract_promises, should_forget, estimate_due_time,
    get_forgotten_apology, get_fulfill_prefix,
)

pytestmark = [pytest.mark.unit]


class TestExtractPromises:
    """承诺提取测试。"""

    def test_extract_mingtian(self):
        """提取'明天'承诺。"""
        results = extract_promises("那我明天告诉你吧", "user123", "sess456")
        assert len(results) >= 1
        assert results[0]["due_hint"] == "明天"
        assert results[0]["user_id"] == "user123"

    def test_extract_xiaci(self):
        """提取'下次'承诺。"""
        results = extract_promises("下次再跟你说哦", "user123", "sess456")
        assert len(results) >= 1
        assert any(r["due_hint"] == "下次" for r in results)

    def test_extract_huitou(self):
        """提取'回头'承诺。"""
        results = extract_promises("回头帮你查一下", "user123", "sess456")
        assert len(results) >= 1
        assert any(r["due_hint"] == "回头" for r in results)

    def test_extract_dengxia(self):
        """提取'等下'承诺。"""
        results = extract_promises("等下我看看哈", "user123", "sess456")
        assert len(results) >= 1

    def test_extract_wandian(self):
        """提取'晚点'承诺。"""
        results = extract_promises("晚点给你发", "user123", "sess456")
        assert len(results) >= 1

    def test_exclude_greeting(self):
        """排除非承诺模式（明天见）。"""
        results = extract_promises("明天见啦", "user123", "sess456")
        # "明天见" 应该被排除
        assert len(results) == 0

    def test_exclude_dont_know(self):
        """排除不确定性表达。"""
        results = extract_promises("明天还不知道呢", "user123", "sess456")
        assert len(results) == 0

    def test_empty_text(self):
        """空文本不产生承诺。"""
        results = extract_promises("", "user123", "sess456")
        assert len(results) == 0

    def test_no_promise_pattern(self):
        """没有承诺模式的文本。"""
        results = extract_promises("今天天气真好啊", "user123", "sess456")
        assert len(results) == 0

    def test_short_match_skipped(self):
        """太短的匹配不算承诺。"""
        results = extract_promises("明天吧", "user123", "sess456")
        # "明天吧" 太短（<4字），应跳过
        assert len(results) == 0

    def test_multiple_promises(self):
        """一则消息中多个承诺。"""
        results = extract_promises(
            "明天帮你查一下，下次再详细说", "user123", "sess456"
        )
        assert len(results) >= 1  # 至少提取一个


class TestEstimateDueTime:
    """到期时间估算测试。"""

    def test_mingtian_due(self):
        now = time.time()
        due, offset = estimate_due_time("明天", now)
        # 应该在24-28小时后
        assert 86400 <= due - now <= 100800  # 86400+14400
        assert 0 <= offset <= 14400

    def test_dengxia_due(self):
        now = time.time()
        due, offset = estimate_due_time("等下", now)
        # 应该在0.5-2小时后
        assert 1800 <= due - now <= 7200
        assert 1800 <= offset <= 7200

    def test_xiaci_due(self):
        now = time.time()
        due, offset = estimate_due_time("下次", now)
        # 应该在1-3天后
        assert 86400 <= due - now <= 259200
        assert 86400 <= offset <= 259200

    def test_unknown_hint_defaults_to_day(self):
        now = time.time()
        due, offset = estimate_due_time("未知", now)
        assert due > now


class TestShouldForget:
    """故意遗忘测试。"""

    def test_distribution_reasonable(self):
        """20%概率遗忘，200次中应有大约40次。"""
        results = [should_forget() for _ in range(500)]
        forget_count = sum(results)
        # 允许较大误差（15-25%）
        assert 50 <= forget_count <= 150, f"Expected ~100, got {forget_count}"


class TestTemplates:
    """模板生成测试。"""

    def test_apology_not_empty(self):
        msg = get_forgotten_apology("帮你查资料")
        assert len(msg) > 5
        assert "帮你查资料" in msg or "..." in msg  # 可能被format替换

    def test_fulfill_prefix_not_empty(self):
        msg = get_fulfill_prefix("告诉你答案")
        assert len(msg) > 3


# ============================================================
# 真人化 P2-3：改进正则 + LLM 辅助提取测试
# ============================================================

class TestImprovedRegex:
    """改进承诺正则测试（真人化 P2-3）"""

    def test_mingtian_with_verb(self):
        """'明天我帮你看一下' → 应能提取承诺"""
        results = extract_promises("明天我帮你看一下那个东西", "user1", "sess1")
        assert len(results) >= 1
        assert "帮你看一下" in results[0]["promise_text"] or "帮你看" in results[0]["promise_text"]

    def test_mingtian_go_do(self):
        """'明天我去查查' → 应能提取"""
        results = extract_promises("明天我去查查那个", "user1", "sess1")
        assert len(results) >= 1

    def test_mingtian_tell_you(self):
        """'明天告诉你答案' → 应能提取"""
        results = extract_promises("明天告诉你答案吧", "user1", "sess1")
        assert len(results) >= 1
        assert any("告诉你" in r["promise_text"] or "告诉" in r["promise_text"] for r in results)

    def test_cross_word_promise(self):
        """'下次我去找找看有没有' → 跨词承诺应能提取"""
        results = extract_promises("下次我去找找看有没有合适的", "user1", "sess1")
        assert len(results) >= 1

    def test_still_excludes_greeting(self):
        """改进后仍排除'明天见'等道别语"""
        results = extract_promises("明天见啦各位", "user1", "sess1")
        assert len(results) == 0

    def test_still_excludes_uncertain(self):
        """改进后仍排除'不知道'等不确定表达"""
        results = extract_promises("明天还不知道能不能去", "user1", "sess1")
        assert len(results) == 0

    def test_promise_has_source_tag(self):
        """提取的承诺应有 source 标记"""
        results = extract_promises("下次帮你查一下资料", "user1", "sess1")
        if results:
            assert "source" in results[0]
            assert results[0]["source"] == "regex"


class TestLLMAssistedExtraction:
    """LLM 辅助承诺提取测试（真人化 P2-3）"""

    def test_detect_implicit_promise_with_hint(self):
        """含动作提示词的文本应尝试 LLM 检测"""
        import asyncio
        async def _test():
            from plugins.deepseek.promise_tracker import detect_implicit_promise
            result = await detect_implicit_promise("今天天气真好啊")
            assert result is None  # 无提示词 → 不触发 LLM
        asyncio.run(_test())

    def test_extract_promises_with_llm_falls_back(self):
        """正则无命中时降级到 LLM 辅助"""
        import asyncio
        async def _test():
            from plugins.deepseek.promise_tracker import extract_promises_with_llm
            results = await extract_promises_with_llm("今天天气不错", "user1", "sess1")
            assert isinstance(results, list)
        asyncio.run(_test())

    def test_regex_high_confidence_no_llm(self):
        """正则高置信度时不触发 LLM"""
        import asyncio
        async def _test():
            from plugins.deepseek.promise_tracker import extract_promises_with_llm
            results = await extract_promises_with_llm("明天帮你查一下资料", "user1", "sess1")
            assert len(results) >= 1
            for r in results:
                assert r["source"] == "regex"
        asyncio.run(_test())


# ============================================================
# 真人化 P2-5：渐进式遗忘测试
# ============================================================

class TestProgressiveForgetting:
    """渐进式遗忘测试（真人化 P2-5）"""

    def test_not_due_yet_no_forget(self):
        """还没到期 → 遗忘概率为 0"""
        future_time = time.time() + 86400
        from plugins.deepseek.promise_tracker import should_forget, get_forget_probability

        prob = get_forget_probability(future_time)
        assert prob == 0.0

        # 还没到期 → should_forget 必定 False
        for _ in range(30):
            assert not should_forget(future_time)

    def test_early_stage_low_probability(self):
        """到期 0-2h → 遗忘概率 ~10%"""
        recent_time = time.time() - 1800  # 过期 0.5 小时
        from plugins.deepseek.promise_tracker import get_forget_probability

        prob = get_forget_probability(recent_time)
        assert prob == 0.10, f"预期 10%，实际 {prob:.2%}"

    def test_mid_stage_medium_probability(self):
        """到期 2-6h → 遗忘概率 ~30%"""
        mid_time = time.time() - 14400  # 过期 4 小时
        from plugins.deepseek.promise_tracker import get_forget_probability

        prob = get_forget_probability(mid_time)
        assert prob == 0.30, f"预期 30%，实际 {prob:.2%}"

    def test_late_stage_high_probability(self):
        """到期 6-24h → 遗忘概率 ~60%"""
        late_time = time.time() - 43200  # 过期 12 小时
        from plugins.deepseek.promise_tracker import get_forget_probability

        prob = get_forget_probability(late_time)
        assert prob == 0.60, f"预期 60%，实际 {prob:.2%}"

    def test_very_late_stage(self):
        """到期 >24h → 遗忘概率 ~80%（仍有 20% 记得）"""
        very_late = time.time() - 100000  # 过期约 27.8 小时
        from plugins.deepseek.promise_tracker import get_forget_probability

        prob = get_forget_probability(very_late)
        assert prob == 0.80, f"预期 80%，实际 {prob:.2%}"

    def test_forgiveness_window(self):
        """道歉窗口应为 7 天（604800 秒）"""
        from plugins.deepseek.promise_tracker import _FORGIVEN_WINDOW

        assert _FORGIVEN_WINDOW == 86400 * 7, \
            f"道歉窗口应为 604800 秒，实际 {_FORGIVEN_WINDOW}"

    def test_should_forget_distribution(self):
        """过期 0-2h 时统计分布应在 ~10%"""
        recent = time.time() - 3600
        from plugins.deepseek.promise_tracker import should_forget

        forgets = sum(should_forget(recent) for _ in range(500))
        assert 20 <= forgets <= 100, f"预期 ~50 次遗忘（10%），实际 {forgets}"

    def test_legacy_no_due_at(self):
        """向后兼容：无 due_at 时默认 20%"""
        from plugins.deepseek.promise_tracker import should_forget

        forgets = sum(should_forget() for _ in range(500))
        assert 50 <= forgets <= 150, f"预期 ~100 次遗忘，实际 {forgets}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
