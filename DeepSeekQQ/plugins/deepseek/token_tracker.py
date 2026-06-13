"""Token 使用量追踪与 API 成本控制。

借鉴 WTFLLM 项目的成本控制理念：
- 实时追踪每次 API 调用的 token 消耗
- 提供每日统计和累计成本
- 支持预算告警（可选）

统计维度：
- 按任务类型分组（chat/extract/search/compress/image）
- 按时间窗口统计（当日/本周/本月/总计）
- 估算费用（基于 DeepSeek 官方定价）
"""

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ============================================================
# DeepSeek 定价（USD / 百万 token）
# ============================================================

PRICING = {
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    "deepseek-v3": {"input": 0.14, "output": 0.28},
    "deepseek-r1": {"input": 0.55, "output": 2.19},
    "default": {"input": 0.14, "output": 0.28},
}

# 大致的 token 估算系数（中文 ~1.5 chars/token, English ~4 chars/token）
CHARS_PER_TOKEN = 1.5

# 统计文件路径
_STATS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
_STATS_FILE = os.path.join(_STATS_DIR, "token_stats.json")


# ============================================================
# 数据结构
# ============================================================


@dataclass
class CallRecord:
    """单次 API 调用记录。"""

    task_type: str
    model: str
    input_chars: int
    output_chars: int
    timestamp: float
    cached: bool = False

    @property
    def input_tokens(self) -> int:
        return max(1, int(self.input_chars / CHARS_PER_TOKEN))

    @property
    def output_tokens(self) -> int:
        return max(1, int(self.output_chars / CHARS_PER_TOKEN))

    @property
    def cost_usd(self) -> float:
        price = PRICING.get(self.model, PRICING["default"])
        return (self.input_tokens * price["input"] + self.output_tokens * price["output"]) / 1_000_000


@dataclass
class DailyStats:
    """每日统计。"""

    date: str  # YYYY-MM-DD
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_task: Dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ============================================================
# 追踪器
# ============================================================


class TokenTracker:
    """Token 使用量追踪器。

    使用方式：
        tracker = get_tracker()
        tracker.record("chat", "deepseek-chat", input_chars=500, output_chars=200)
        stats = tracker.get_stats()
    """

    def __init__(self):
        self._records: List[CallRecord] = []
        self._daily: Dict[str, DailyStats] = {}
        self._max_records = 10000  # 最多保留记录数
        self._load()

    def record(
        self,
        task_type: str,
        model: str = "default",
        input_chars: int = 0,
        output_chars: int = 0,
        cached: bool = False,
    ):
        """记录一次 API 调用。"""
        record = CallRecord(
            task_type=task_type,
            model=model,
            input_chars=input_chars,
            output_chars=output_chars,
            timestamp=time.time(),
            cached=cached,
        )

        self._records.append(record)
        # 限制记录数量
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records:]

        # 更新每日统计
        date_str = _date_str(record.timestamp)
        if date_str not in self._daily:
            self._daily[date_str] = DailyStats(date=date_str)
        ds = self._daily[date_str]
        ds.calls += 1
        if not cached:
            ds.input_tokens += record.input_tokens
            ds.output_tokens += record.output_tokens
            ds.cost_usd += record.cost_usd
        ds.by_task[task_type] += 1

        # 每 100 次调用记录一次日志
        total_calls = sum(d.calls for d in self._daily.values())
        if total_calls % 100 == 0:
            logger.info(
                f"[Token] 累计 {total_calls} 次调用, "
                f"今日 {ds.calls} 次, "
                f"预估费用 ${ds.cost_usd:.4f}"
            )

    def get_stats(self) -> Dict[str, Any]:
        """获取统计摘要。"""
        today = _date_str(time.time())
        today_stats = self._daily.get(today, DailyStats(date=today))

        # 本月统计
        month_prefix = today[:7]  # YYYY-MM
        month_calls = 0
        month_cost = 0.0
        month_input = 0
        month_output = 0
        for date_str, ds in self._daily.items():
            if date_str.startswith(month_prefix):
                month_calls += ds.calls
                month_cost += ds.cost_usd
                month_input += ds.input_tokens
                month_output += ds.output_tokens

        # 总计
        total_calls = sum(d.calls for d in self._daily.values())
        total_cost = sum(d.cost_usd for d in self._daily.values())
        total_input = sum(d.input_tokens for d in self._daily.values())
        total_output = sum(d.output_tokens for d in self._daily.values())

        # 任务类型分布
        task_dist = defaultdict(int)
        for ds in self._daily.values():
            for task, count in ds.by_task.items():
                task_dist[task] += count

        # 缓存命中相关的节省
        cached_calls = sum(1 for r in self._records if r.cached)
        cache_rate = cached_calls / max(1, len(self._records))

        return {
            "today": {
                "date": today,
                "calls": today_stats.calls,
                "input_tokens": today_stats.input_tokens,
                "output_tokens": today_stats.output_tokens,
                "cost_usd": round(today_stats.cost_usd, 6),
            },
            "month": {
                "calls": month_calls,
                "input_tokens": month_input,
                "output_tokens": month_output,
                "cost_usd": round(month_cost, 6),
            },
            "total": {
                "calls": total_calls,
                "input_tokens": total_input,
                "output_tokens": total_output,
                "cost_usd": round(total_cost, 6),
            },
            "by_task": dict(task_dist),
            "cache_hit_rate": round(cache_rate, 3),
            "recent_calls": [
                {
                    "task": r.task_type,
                    "model": r.model,
                    "tokens_in": r.input_tokens,
                    "tokens_out": r.output_tokens,
                    "cost_usd": round(r.cost_usd, 6),
                    "cached": r.cached,
                    "time": time.strftime("%H:%M:%S", time.localtime(r.timestamp)),
                }
                for r in self._records[-20:]  # 最近 20 次
            ],
        }

    def _load(self):
        """从文件加载持久化的统计数据。"""
        try:
            if os.path.exists(_STATS_FILE):
                with open(_STATS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for date_str, d in data.get("daily", {}).items():
                    self._daily[date_str] = DailyStats(
                        date=date_str,
                        calls=d.get("calls", 0),
                        input_tokens=d.get("input_tokens", 0),
                        output_tokens=d.get("output_tokens", 0),
                        cost_usd=d.get("cost_usd", 0.0),
                        by_task=defaultdict(int, d.get("by_task", {})),
                    )
                logger.info(f"[Token] 已加载 {len(self._daily)} 天的统计数据")
        except Exception:
            pass

    def persist(self):
        """持久化统计数据到文件。"""
        try:
            os.makedirs(_STATS_DIR, exist_ok=True)
            data = {
                "daily": {
                    date_str: {
                        "calls": ds.calls,
                        "input_tokens": ds.input_tokens,
                        "output_tokens": ds.output_tokens,
                        "cost_usd": ds.cost_usd,
                        "by_task": dict(ds.by_task),
                    }
                    for date_str, ds in self._daily.items()
                }
            }
            with open(_STATS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[Token] 持久化失败: {e}")

    def reset_daily(self):
        """重置今日统计（午夜调用）。"""
        today = _date_str(time.time())
        self._daily.pop(today, None)


def _date_str(ts: float) -> str:
    """时间戳 → YYYY-MM-DD 字符串。"""
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# ============================================================
# 全局单例
# ============================================================

_tracker: Optional[TokenTracker] = None


def get_tracker() -> TokenTracker:
    """获取全局 TokenTracker 单例。"""
    global _tracker
    if _tracker is None:
        _tracker = TokenTracker()
    return _tracker
