"""性能监控模块 — 追踪响应时间、API 效率、Token 消耗。

轻量级监控，不影响主流程性能。
数据只保留最近 1 小时，定期清理。
"""
import time
from typing import Dict, Any, Optional, List
from collections import defaultdict, deque
from datetime import datetime

from nonebot import logger


# ============================================================
# 响应时间追踪
# ============================================================

# 阶段耗时记录：stage_name -> deque of (timestamp, duration_ms)
_stage_timings: Dict[str, deque] = defaultdict(lambda: deque(maxlen=100))

# API 调用记录
_api_calls: deque = deque(maxlen=200)

# 总响应时间记录
_response_times: deque = deque(maxlen=100)


class StageTimer:
    """上下文管理器：自动记录阶段耗时。"""

    def __init__(self, stage_name: str):
        self.stage_name = stage_name
        self.start_time = 0

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = (time.time() - self.start_time) * 1000
        _stage_timings[self.stage_name].append((time.time(), duration_ms))
        if duration_ms > 5000:  # 超过 5 秒告警
            logger.warning(f"[性能] {self.stage_name} 耗时 {duration_ms:.0f}ms")
        return False



def track_response(duration_ms: float):
    """记录总响应时间。"""
    _response_times.append((time.time(), duration_ms))


# ============================================================
# API 效率监控
# ============================================================

def track_api_call(
    task_type: str,
    duration_ms: float,
    tokens_used: int = 0,
    success: bool = True,
    error: str = "",
):
    """记录 API 调用。"""
    _api_calls.append({
        "timestamp": time.time(),
        "task_type": task_type,
        "duration_ms": duration_ms,
        "tokens_used": tokens_used,
        "success": success,
        "error": error,
    })


# ============================================================
# 统计报告
# ============================================================

def _cleanup_old_records():
    """清理超过 1 小时的记录。"""
    cutoff = time.time() - 3600

    for stage_name in list(_stage_timings.keys()):
        dq = _stage_timings[stage_name]
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    while _api_calls and _api_calls[0]["timestamp"] < cutoff:
        _api_calls.popleft()

    while _response_times and _response_times[0][0] < cutoff:
        _response_times.popleft()


def get_performance_report() -> Dict[str, Any]:
    """生成性能报告。"""
    _cleanup_old_records()

    report = {
        "timestamp": datetime.now().isoformat(),
        "stage_timings": {},
        "api_stats": {},
        "response_stats": {},
    }

    # 阶段耗时统计
    for stage_name, timings in _stage_timings.items():
        if not timings:
            continue
        durations = [t[1] for t in timings]
        report["stage_timings"][stage_name] = {
            "count": len(durations),
            "avg_ms": sum(durations) / len(durations),
            "max_ms": max(durations),
            "min_ms": min(durations),
            "p95_ms": sorted(durations)[int(len(durations) * 0.95)] if len(durations) >= 20 else max(durations),
        }

    # API 统计
    if _api_calls:
        calls = list(_api_calls)
        success_count = sum(1 for c in calls if c["success"])
        total_tokens = sum(c["tokens_used"] for c in calls)
        durations = [c["duration_ms"] for c in calls]

        # 按 task_type 分组
        by_type = defaultdict(list)
        for c in calls:
            by_type[c["task_type"]].append(c)

        report["api_stats"] = {
            "total_calls": len(calls),
            "success_rate": success_count / len(calls),
            "total_tokens": total_tokens,
            "avg_duration_ms": sum(durations) / len(durations),
            "by_type": {
                t: {
                    "count": len(cs),
                    "avg_ms": sum(c["duration_ms"] for c in cs) / len(cs),
                    "tokens": sum(c["tokens_used"] for c in cs),
                }
                for t, cs in by_type.items()
            },
        }

    # 总响应时间统计
    if _response_times:
        durations = [t[1] for t in _response_times]
        report["response_stats"] = {
            "count": len(durations),
            "avg_ms": sum(durations) / len(durations),
            "max_ms": max(durations),
            "min_ms": min(durations),
            "p95_ms": sorted(durations)[int(len(durations) * 0.95)] if len(durations) >= 20 else max(durations),
        }

    return report


def log_performance_summary():
    """输出性能摘要到日志。"""
    report = get_performance_report()

    # 只在有数据时输出
    if not report["response_stats"]:
        return

    resp = report["response_stats"]
    api = report["api_stats"]

    lines = [f"[性能] 响应: 平均{resp['avg_ms']:.0f}ms P95={resp['p95_ms']:.0f}ms"]

    if api:
        lines.append(f"[性能] API: {api['total_calls']}次 成功率{api['success_rate']:.0%} Token={api['total_tokens']}")

    # 最慢的 3 个阶段
    stages = report["stage_timings"]
    if stages:
        slowest = sorted(stages.items(), key=lambda x: x[1]["avg_ms"], reverse=True)[:3]
        for name, stats in slowest:
            lines.append(f"[性能] {name}: 平均{stats['avg_ms']:.0f}ms (max={stats['max_ms']:.0f}ms)")

    logger.info("\n".join(lines))
