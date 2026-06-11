"""技术优化测试 — 智能上下文、Token 预算、性能监控。"""
import pytest
import time
from unittest.mock import patch
pytestmark = [pytest.mark.unit]



# ============================================================
# 智能上下文选择测试
# ============================================================

class TestContextSelection:
    def test_short_list_unchanged(self):
        """消息少于 max_count 时原样返回"""
        from plugins.deepseek.context_optimizer import select_context_messages
        messages = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好呀"}]
        result = select_context_messages(messages, "你好", max_count=10)
        assert len(result) == 2

    def test_recent_messages_prioritized(self):
        """最近 5 条消息应始终被选中"""
        from plugins.deepseek.context_optimizer import select_context_messages
        messages = [{"role": "user", "content": f"消息{i}"} for i in range(20)]
        result = select_context_messages(messages, "最新消息", max_count=10)
        # 最近 5 条应在结果中
        result_contents = [m["content"] for m in result]
        for i in range(15, 20):
            assert f"消息{i}" in result_contents

    def test_keyword_relevant_messages_selected(self):
        """与当前消息关键词相关的旧消息应被选中"""
        from plugins.deepseek.context_optimizer import select_context_messages
        messages = [
            {"role": "user", "content": "我喜欢玩游戏"},
            {"role": "assistant", "content": "什么游戏呀"},
            {"role": "user", "content": "原神"},
            {"role": "assistant", "content": "原神很好玩"},
            {"role": "user", "content": "今天天气不错"},
            {"role": "assistant", "content": "是呀"},
            {"role": "user", "content": "晚上吃什么"},
            {"role": "assistant", "content": "随便吧"},
            {"role": "user", "content": "游戏好玩吗"},
            {"role": "assistant", "content": "好玩"},
        ]
        result = select_context_messages(messages, "原神怎么玩", max_count=5)
        result_text = " ".join(m["content"] for m in result)
        # 应该选中包含"游戏"/"原神"的消息
        assert "游戏" in result_text or "原神" in result_text

    def test_emotional_messages_valued(self):
        """情绪明显的消息应比普通消息获得更高评分"""
        from plugins.deepseek.context_optimizer import select_context_messages
        # 构造足够多消息，让情绪消息不在"最近5条保底"范围内
        # 但通过 max_count 让情绪消息有机会被选中
        messages = []
        for i in range(30):
            messages.append({"role": "user", "content": f"普通消息{i}"})
        # 在中间插入情绪消息
        messages[10] = {"role": "user", "content": "今天好开心啊"}
        messages[20] = {"role": "user", "content": "好难过不想说话"}

        # max_count=20，最近5条保底 + 15条由分数选出
        result = select_context_messages(messages, "随便聊聊", max_count=20)
        result_text = " ".join(m["content"] for m in result)
        # 情绪消息应被选中（因为有 +1.5 的情绪加分）
        assert "开心" in result_text or "难过" in result_text


# ============================================================
# Token 预算管理测试
# ============================================================

class TestTokenBudget:
    def test_estimate_tokens_chinese(self):
        """中文 token 估算（B21: ~0.7 字/token）"""
        from plugins.deepseek.context_optimizer import estimate_tokens
        tokens = estimate_tokens("你好世界")
        assert 4 <= tokens <= 7  # B21: 4 CJK chars / 0.7 ≈ 5.7 tokens

    def test_estimate_tokens_english(self):
        """英文 token 估算"""
        from plugins.deepseek.context_optimizer import estimate_tokens
        tokens = estimate_tokens("hello world")
        assert 2 <= tokens <= 4

    def test_fit_messages_to_budget(self):
        """超出预算时应裁剪消息"""
        from plugins.deepseek.context_optimizer import fit_messages_to_budget
        messages = [{"role": "system", "content": "你是猫娘"}]
        for i in range(20):
            messages.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i}" * 10})
        result = fit_messages_to_budget(messages, "你是猫娘", max_input_tokens=500, reserve_output=200)
        assert len(result) < len(messages)

    def test_fit_preserves_system_and_last_user(self):
        """裁剪后应保留 system 和最后一条 user"""
        from plugins.deepseek.context_optimizer import fit_messages_to_budget
        messages = [
            {"role": "system", "content": "你是猫娘"},
            {"role": "user", "content": "第一条" * 50},
            {"role": "assistant", "content": "回复" * 50},
            {"role": "user", "content": "最后一条"},
        ]
        result = fit_messages_to_budget(messages, "你是猫娘", max_input_tokens=100, reserve_output=50)
        assert result[0]["role"] == "system"
        assert result[-1]["content"] == "最后一条"


# ============================================================
# 摘要缓存测试
# ============================================================

class TestSummaryCache:
    def test_cache_set_and_get(self):
        """设置和获取缓存"""
        from plugins.deepseek.context_optimizer import set_cached_summary, get_cached_summary
        set_cached_summary("test_session", "这是摘要", 30)
        result = get_cached_summary("test_session", 35)
        assert result == "这是摘要"

    def test_cache_miss_after_new_messages(self):
        """新消息超过阈值时缓存失效"""
        from plugins.deepseek.context_optimizer import set_cached_summary, get_cached_summary
        set_cached_summary("test_session2", "旧摘要", 30)
        result = get_cached_summary("test_session2", 50)  # 差了 20 条
        assert result is None

    def test_cache_miss_for_unknown_session(self):
        """未知 session 返回 None"""
        from plugins.deepseek.context_optimizer import get_cached_summary
        result = get_cached_summary("nonexistent", 10)
        assert result is None


# ============================================================
# 性能监控测试
# ============================================================

class TestPerformanceMonitor:
    def test_stage_timer(self):
        """StageTimer 应记录耗时"""
        from plugins.deepseek.performance_monitor import StageTimer, _stage_timings
        with StageTimer("test_stage"):
            time.sleep(0.01)
        assert len(_stage_timings["test_stage"]) > 0
        assert _stage_timings["test_stage"][-1][1] >= 10  # 至少 10ms

    def test_track_api_call(self):
        """track_api_call 应记录调用"""
        from plugins.deepseek.performance_monitor import track_api_call, _api_calls
        initial_len = len(_api_calls)
        track_api_call("chat", 100.0, tokens_used=50, success=True)
        assert len(_api_calls) == initial_len + 1

    def test_performance_report(self):
        """报告应包含所有字段"""
        from plugins.deepseek.performance_monitor import get_performance_report, track_response
        track_response(500.0)
        report = get_performance_report()
        assert "stage_timings" in report
        assert "api_stats" in report
        assert "response_stats" in report

    def test_context_stats(self):
        """上下文统计应正确计算"""
        from plugins.deepseek.context_optimizer import get_context_stats
        messages = [{"role": "user", "content": f"消息{i}"} for i in range(20)]
        selected = messages[:10]
        stats = get_context_stats(messages, selected, "系统提示")
        assert stats["original_count"] == 20
        assert stats["selected_count"] == 10
        assert stats["compression_ratio"] == 0.5
        assert stats["token_saved"] > 0
