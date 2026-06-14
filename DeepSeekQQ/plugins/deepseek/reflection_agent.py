"""ReflectionAgent 配置与 Token 预算追踪。

P2-1: 自我反思 — 后置 LLM 反思，反馈到人格和记忆系统。

修正原方案将日 token 预算从 2000 提升到 15000（实际估算 ~10400 + 50% 余量）。
增加 API 调用次数限制作为双重保险。

反思维度（JSON 输出格式）：
{
    "quality": 0.0~1.0,             回复质量
    "diversity": 0.0~1.0,           表达多样性（是否重复套路）
    "appropriateness": 0.0~1.0,     情境恰当性
    "persona_consistency": 0.0~1.0, 人设一致性
    "issues": ["..."],              发现的问题（可选）
    "suggestions": ["..."]          改进建议（可选）
}

Usage:
    tracker = DailyTokenTracker(
        budget=REFLECTION_CONFIG["daily_token_budget"],
        call_budget=REFLECTION_CONFIG["daily_call_budget"],
    )
    if tracker.can_proceed(estimated_tokens=1300):
        result = await call_reflection_api(...)
        tracker.record(actual_tokens=result.usage.total_tokens)
"""
from datetime import date


REFLECTION_CONFIG = {
    "enabled": True,

    # 采样与批处理
    "sample_rate": 0.20,          # 采样 20% 的回复进行反思
    "batch_size": 5,              # 每批 5 条，减少 API 调用次数
    "min_interval_seconds": 60,   # 批次间最小间隔，避免连续调用

    # Token 预算（修正：原方案 2000/天 严重低估）
    # 实际估算：单批 ~1300 token，日均 8 批 = ~10,400 token
    # 留 50% 余量后设为 15,000
    "daily_token_budget": 15_000,

    # API 调用次数限制（双重保险）
    "daily_call_budget": 12,      # 每日最多 12 次批处理调用

    # 模型
    "model": "deepseek-chat",
    "max_tokens_per_call": 300,   # 结构化 JSON 输出，300 token 足够
}


class DailyTokenTracker:
    """日 token 用量追踪，超预算自动暂停。

    每天自动重置计数。同时追踪 token 用量和 API 调用次数，
    任一超限即暂停当日剩余反思。
    """

    def __init__(self, budget: int, call_budget: int):
        self.budget = budget
        self.call_budget = call_budget
        self._used_tokens = 0
        self._used_calls = 0
        self._date = None

    def _reset_if_new_day(self):
        """跨天自动重置计数。"""
        today = date.today()
        if self._date != today:
            self._used_tokens = 0
            self._used_calls = 0
            self._date = today

    def can_proceed(self, estimated_tokens: int) -> bool:
        """检查是否还有预算进行下一次反思调用。

        Args:
            estimated_tokens: 预估本次调用消耗的 token 数

        Returns:
            True 如果预算充足，False 如果超限
        """
        self._reset_if_new_day()
        return (
            self._used_tokens + estimated_tokens <= self.budget
            and self._used_calls < self.call_budget
        )

    def record(self, actual_tokens: int):
        """记录一次实际反思调用的 token 消耗。"""
        self._reset_if_new_day()
        self._used_tokens += actual_tokens
        self._used_calls += 1

    @property
    def remaining(self) -> dict:
        """返回剩余预算。"""
        self._reset_if_new_day()
        return {
            "tokens": max(0, self.budget - self._used_tokens),
            "calls": max(0, self.call_budget - self._used_calls),
        }

    @property
    def used(self) -> dict:
        """返回已使用量。"""
        self._reset_if_new_day()
        return {
            "tokens": self._used_tokens,
            "calls": self._used_calls,
        }
