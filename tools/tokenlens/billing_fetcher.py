"""官方账单获取 — 通过余额变化计算真实花费

原理:
  各平台（DeepSeek/Moonshot）只提供余额查询 API，没有公开的用量明细 API。
  但可以通过"余额变化"反推实际花费:
    实际花费 = 上次余额 - 当前余额  （余额减少时）
    实际花费 = 历史累积减少量    （多次快照累加）

流程:
  1. 查当前余额 → 存快照到 ~/.tokenlens/balance_history.json
  2. 与上次快照对比 → 余额减少量 = 实际花费
  3. 对比本地估算，显示偏差

支持的平台:
  - DeepSeek:  GET https://api.deepseek.com/user/balance
  - Moonshot:  GET https://api.moonshot.cn/v1/users/me/balance
  - MiMo:      暂无（计费系统尚未正式上线）
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("tokenlens.billing")

# 余额历史文件
HISTORY_PATH = Path.home() / ".tokenlens" / "balance_history.json"


# ─── 数据模型 ──────────────────────────────────────────

@dataclass
class BalanceSnapshot:
    """余额快照"""
    platform: str
    balance: float
    timestamp: float = 0.0
    currency: str = "CNY"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class PlatformBilling:
    """单平台账单"""
    platform: str
    current_balance: float | None = None  # 当前余额
    previous_balance: float | None = None # 上次余额
    spent_since_last: float = 0.0         # 上次到现在的花费
    total_spent_tracked: float = 0.0      # 历史追踪的总花费
    error: str | None = None
    raw: dict = field(default_factory=dict)


# ─── 余额历史管理 ──────────────────────────────────────

def load_history() -> dict[str, list[dict]]:
    """加载余额历史"""
    if not HISTORY_PATH.exists():
        return {}
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_history(history: dict) -> None:
    """保存余额历史"""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_balance(platform: str, balance: float) -> BalanceSnapshot:
    """记录一次余额快照，返回快照和上次余额"""
    history = load_history()
    snapshots = history.get(platform, [])

    snap = BalanceSnapshot(platform=platform, balance=balance)
    snapshots.append({
        "balance": balance,
        "timestamp": snap.timestamp,
        "iso": datetime.fromtimestamp(snap.timestamp).isoformat(),
    })

    # 只保留最近 365 条
    if len(snapshots) > 365:
        snapshots = snapshots[-365:]

    history[platform] = snapshots
    save_history(history)
    return snap


def get_previous_balance(platform: str) -> float | None:
    """获取上次记录的余额"""
    history = load_history()
    snapshots = history.get(platform, [])
    if snapshots:
        return snapshots[-1]["balance"]
    return None


def calc_total_tracked_spend(platform: str) -> float:
    """计算历史追踪的总花费（所有余额减少量之和）"""
    history = load_history()
    snapshots = history.get(platform, [])
    if len(snapshots) < 2:
        return 0.0

    total = 0.0
    for i in range(1, len(snapshots)):
        prev = snapshots[i - 1]["balance"]
        curr = snapshots[i]["balance"]
        # 余额增加（充值）跳过，余额减少（花费）累加
        if curr < prev:
            total += prev - curr
    return total


# ─── DeepSeek ──────────────────────────────────────────

async def fetch_deepseek_balance(api_key: str | None = None) -> PlatformBilling:
    """查询 DeepSeek 余额"""
    key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return PlatformBilling(platform="deepseek", error="DEEPSEEK_API_KEY 未设置")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code != 200:
                return PlatformBilling(platform="deepseek",
                                     error=f"HTTP {resp.status_code}")

            data = resp.json()
            infos = data.get("balance_infos", [])
            if not infos:
                return PlatformBilling(platform="deepseek", error="无余额数据")

            balance = float(infos[0].get("total_balance", "0"))
    except Exception as e:
        return PlatformBilling(platform="deepseek", error=str(e))

    # 记录快照
    previous = get_previous_balance("deepseek")
    record_balance("deepseek", balance)
    total_spent = calc_total_tracked_spend("deepseek")

    spent = 0.0
    if previous is not None and balance < previous:
        spent = previous - balance

    return PlatformBilling(
        platform="deepseek",
        current_balance=balance,
        previous_balance=previous,
        spent_since_last=spent,
        total_spent_tracked=total_spent,
        raw=data,
    )


# ─── Moonshot/Kimi ─────────────────────────────────────

async def fetch_moonshot_balance(api_key: str | None = None) -> PlatformBilling:
    """查询 Moonshot/Kimi 余额"""
    key = api_key or os.getenv("MOONSHOT_API_KEY", "") or os.getenv("KIMI_API_KEY", "")
    if not key:
        return PlatformBilling(platform="moonshot", error="MOONSHOT_API_KEY 或 KIMI_API_KEY 未设置")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.moonshot.cn/v1/users/me/balance",
                headers={"Authorization": f"Bearer {key}"},
            )
            if resp.status_code != 200:
                return PlatformBilling(platform="moonshot",
                                     error=f"HTTP {resp.status_code}")

            data = resp.json()
            bal = data.get("data", {})
            balance = float(bal.get("available_balance", 0))
    except Exception as e:
        return PlatformBilling(platform="moonshot", error=str(e))

    previous = get_previous_balance("moonshot")
    record_balance("moonshot", balance)
    total_spent = calc_total_tracked_spend("moonshot")

    spent = 0.0
    if previous is not None and balance < previous:
        spent = previous - balance

    return PlatformBilling(
        platform="moonshot",
        current_balance=balance,
        previous_balance=previous,
        spent_since_last=spent,
        total_spent_tracked=total_spent,
        raw=data,
    )


# ─── 统一入口 ──────────────────────────────────────────

@dataclass
class CombinedBilling:
    """多平台账单合并"""
    platforms: dict[str, PlatformBilling] = field(default_factory=dict)
    total_official_spend: float = 0.0     # 官方实际花费（余额变化）
    total_balance: float = 0.0            # 总余额
    local_estimate: float = 0.0           # TokenLens 本地估算
    fetched_at: str = ""
    is_first_run: bool = True             # 首次运行（无历史，无法算花费）

    @property
    def discrepancy_pct(self) -> float | None:
        """官方 vs 本地估算偏差"""
        if self.local_estimate <= 0 or self.total_official_spend <= 0:
            return None
        return (self.total_official_spend - self.local_estimate) / self.local_estimate


async def fetch_all_billing(
    deepseek_key: str | None = None,
    moonshot_key: str | None = None,
) -> CombinedBilling:
    """并行获取所有平台余额"""
    results = {}
    tasks = []

    # DeepSeek
    ds_key = deepseek_key or os.getenv("DEEPSEEK_API_KEY", "")
    if ds_key:
        tasks.append(fetch_deepseek_balance(ds_key))

    # Moonshot
    ms_key = moonshot_key or os.getenv("MOONSHOT_API_KEY", "") or os.getenv("KIMI_API_KEY", "")
    if ms_key:
        tasks.append(fetch_moonshot_balance(ms_key))

    # 并行执行
    if tasks:
        import asyncio
        done = await asyncio.gather(*tasks, return_exceptions=True)
        for result in done:
            if isinstance(result, PlatformBilling):
                results[result.platform] = result
            elif isinstance(result, Exception):
                logger.error(f"余额查询异常: {result}")

    total_spend = sum(s.total_spent_tracked for s in results.values())
    total_balance = sum(
        (s.current_balance or 0) for s in results.values()
    )

    # 判断是否首次运行
    history = load_history()
    is_first = all(
        len(history.get(p, [])) <= 1 for p in results
    )

    return CombinedBilling(
        platforms=results,
        total_official_spend=total_spend,
        total_balance=total_balance,
        fetched_at=datetime.now().isoformat(),
        is_first_run=is_first,
    )


def fetch_billing_sync(timeout: int = 30) -> CombinedBilling:
    """同步封装（CLI 用）"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fetch_all_billing())
    else:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, fetch_all_billing())
            return future.result(timeout=timeout)
