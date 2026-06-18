"""定价数据自动获取 — 从官网抓取最新模型定价

支持的来源:
  - DeepSeek: api-docs.deepseek.com
  - OpenRouter: openrouter.ai (aggregator fallback)
  - 手动维护的 defaults（始终最新）

缓存策略:
  - 本地 JSON 缓存 24 小时
  - 获取失败时降级到缓存 → 硬编码默认值
  - 支持环境变量 TOKENLENS_PRICING_CACHE 指定缓存文件路径
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("tokenlens.pricing_fetcher")

# 默认缓存路径
DEFAULT_CACHE_PATH = Path.home() / ".tokenlens" / "pricing_cache.json"

# 缓存有效期（秒）
CACHE_TTL = 86400  # 24h

# ─── 硬编码默认值（定期手动更新，始终反映最新官方价格） ───
# 价格单位: RMB / 百万 token
# USD → RMB 按汇率 7.25 计算
#
# ⚠️ 同步维护：此表与 pricing.py 的 _DEFAULT_PRICING 必须保持一致。
#   pricing.py 是运行时定价的权威来源，此表仅作为网络获取失败时的
#   离线兜底。更新价格时请同时修改两处。

FALLBACK_PRICING: dict[str, dict[str, float]] = {
    # DeepSeek 官方人民币定价 (2026-06-17)
    # V4 Pro: ¥3.00 输入 / ¥0.025 缓存命中 / ¥6.00 输出
    #   (USD 参考: $0.435 / $0.003625 / $0.87)
    # V4 Flash: ¥1.00 输入 / ¥0.02 缓存命中 / ¥2.00 输出
    #   (USD 参考: $0.14 / $0.0028 / $0.28)
    "deepseek-v4-pro":         {"input": 3.00, "cache_read": 0.025, "output": 6.00},
    "deepseek-v4-flash":       {"input": 1.00, "cache_read": 0.02,  "output": 2.00},

    # 小米 MiMo (对标 DeepSeek)
    "mimo-v2.5-pro":           {"input": 3.00, "cache_read": 0.025, "output": 6.00},
    "mimo-v2.5":               {"input": 1.00, "cache_read": 0.02,  "output": 2.00},

    # Kimi/Moonshot 官方 (USD→RMB @7.25)
    # $0.95 / $0.16 / $4.00
    "kimi-k2.6":               {"input": 6.89, "cache_read": 1.16, "output": 29.0},

    # Claude 家族 (Anthropic 官方, USD→RMB @7.25)
    # Sonnet 4: $3 / $0.30 / $15, Opus 4: $15 / $1.50 / $75
    "claude-sonnet-4-20250514": {"input": 21.75, "cache_read": 2.18, "output": 108.75},
    "claude-opus-4-20250514":   {"input": 108.75, "cache_read": 10.88, "output": 543.75},
}

# OpenRouter 模型名 → 内部名映射
_OPENROUTER_MAP = {
    "deepseek/deepseek-v4-pro":   "deepseek-v4-pro",
    "deepseek/deepseek-v4-flash": "deepseek-v4-flash",
    "moonshotai/kimi-k2.6":       "kimi-k2.6",
    "anthropic/claude-sonnet-4":  "claude-sonnet-4-20250514",
    "anthropic/claude-opus-4":    "claude-opus-4-20250514",
}

# ─── 数据源配置 ─────────────────────────────────────────

@dataclass
class PriceSource:
    """定价数据源"""
    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)

    async def fetch(self, client: httpx.AsyncClient) -> dict[str, dict[str, float]] | None:
        raise NotImplementedError


class LiteLLMSource(PriceSource):
    """LiteLLM 社区定价库 — ccusage 的主要定价来源

    从 BerriAI/litellm GitHub 仓库获取 model_prices_and_context_window.json
    包含 1300+ 模型的官方定价，社区持续维护更新。

    参考: https://github.com/BerriAI/litellm
    """

    # LiteLLM 模型名 → TokenLens 内部名映射
    #
    # ⚠️ 注意: LiteLLM 社区数据可能滞后于官方定价更新。
    # DeepSeek V4 Pro/Flash (2026-04) 至今未收录于 LiteLLM，其 deepseek-chat
    # 条目仍为旧 V3 定价 ($0.28/M)。因此不映射 deepseek-chat，由
    # FALLBACK_PRICING 硬编码值兜底。
    #
    # 当 LiteLLM 社区添加了 deepseek-v4-pro / deepseek-v4-flash 条目后，
    # 可以解除下面被注释的映射。
    _MODEL_MAP: dict[str, str] = {
        # DeepSeek — V4 系列暂不映射（LiteLLM 尚未收录，旧条目定价不准确）
        # "deepseek/deepseek-v4-pro": "deepseek-v4-pro",       # TODO: LiteLLM 收录后启用
        # "deepseek/deepseek-v4-flash": "deepseek-v4-flash",   # TODO: LiteLLM 收录后启用
        # Kimi/Moonshot
        "moonshot/kimi-k2.6": "kimi-k2.6",
        # Claude
        "claude-sonnet-4-20250514": "claude-sonnet-4-20250514",
        "claude-opus-4-20250514": "claude-opus-4-20250514",
        "anthropic/claude-sonnet-4-20250514": "claude-sonnet-4-20250514",
        "anthropic/claude-opus-4-20250514": "claude-opus-4-20250514",
    }

    def __init__(self):
        super().__init__(
            name="litellm",
            url=(
                "https://raw.githubusercontent.com/BerriAI/litellm/main/"
                "model_prices_and_context_window.json"
            ),
        )

    async def fetch(self, client: httpx.AsyncClient) -> dict[str, dict[str, float]] | None:
        try:
            resp = await client.get(self.url, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"LiteLLM 返回 {resp.status_code}")
                return None
            data = resp.json()
            if not isinstance(data, dict) or len(data) < 10:
                logger.warning("LiteLLM 数据异常（模型数量过少）")
                return None
            return self._parse(data)
        except Exception as e:
            logger.warning(f"LiteLLM 获取失败: {e}")
            return None

    def _parse(self, data: dict) -> dict[str, dict[str, float]] | None:
        """解析 LiteLLM 定价 JSON

        LiteLLM 格式:
          "model_name": {
            "input_cost_per_token": 0.000003,        # $/token
            "output_cost_per_token": 0.000006,
            "cache_read_input_token_cost": 0.00000015,
            "cache_creation_input_token_cost": ...,
            "input_cost_per_token_above_200k_tokens": ...,  # Claude 分级
            ...
          }

        转换为:
          "internal_name": {
            "input": 3.15,        # ¥/百万token
            "cache_read": 0.026,
            "output": 6.31,
          }
        """
        USD_TO_RMB = float(os.getenv("TOKENLENS_USD_TO_RMB", "7.25"))
        SCALE = 1_000_000  # per-token → per-million

        result: dict[str, dict[str, float]] = {}

        # 尝试匹配所有已知模型
        for lite_key, internal_name in self._MODEL_MAP.items():
            model_data = data.get(lite_key)
            if model_data is None:
                # 尝试模糊匹配
                for data_key in data:
                    if lite_key.lower() in data_key.lower():
                        model_data = data[data_key]
                        break
            if model_data is None:
                continue

            input_price = model_data.get("input_cost_per_token")
            output_price = model_data.get("output_cost_per_token")
            cache_read_price = model_data.get("cache_read_input_token_cost")

            # 分级定价（Claude 200K 阈值）：优先使用 above_200k 价格
            # 实际实现中使用标准价格，above_200k 留待后续优化
            if input_price is not None and output_price is not None:
                result[internal_name] = {
                    "input": round(float(input_price) * SCALE * USD_TO_RMB, 4),
                    "output": round(float(output_price) * SCALE * USD_TO_RMB, 4),
                }
                # cache_read 仅在 LiteLLM 明确提供时设置
                if cache_read_price is not None and float(cache_read_price) > 0:
                    result[internal_name]["cache_read"] = round(
                        float(cache_read_price) * SCALE * USD_TO_RMB, 6
                    )

        logger.info(f"LiteLLM: 匹配到 {len(result)} 个模型定价")
        return result if result else None


class DeepSeekSource(PriceSource):
    """DeepSeek 官方定价页"""

    def __init__(self):
        super().__init__(
            name="deepseek",
            url="https://api-docs.deepseek.com/zh-cn/quick_start/pricing",
        )

    async def fetch(self, client: httpx.AsyncClient) -> dict[str, dict[str, float]] | None:
        try:
            resp = await client.get(self.url, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"DeepSeek 页面返回 {resp.status_code}")
                return None
            html = resp.text
            return self._parse(html)
        except Exception as e:
            logger.warning(f"DeepSeek 获取失败: {e}")
            return None

    def _parse(self, html: str) -> dict[str, dict[str, float]] | None:
        """从 HTML 中提取定价表

        DeepSeek 定价页结构:
          - 表格行包含模型名、输入价格、缓存命中价格、输出价格
          - 价格单位: 元/百万tokens (RMB) 或 $/百万tokens (USD)
        """
        result: dict[str, dict[str, float]] = {}

        # 尝试多种模式匹配
        # 模式 1: 人民币价格表 (¥/百万tokens)
        # 查找 "deepseek-chat" 或 "deepseek-v4" 行
        models_found = 0

        # 简化的 HTML 表格解析：找 <tr> 中包含模型名和价格的
        # 匹配如: deepseek-chat → ¥3 / ¥0.025 / ¥6
        rmb_pattern = re.compile(
            r'(deepseek[-\w]*).*?'
            r'[¥￥]\s*([\d.]+)\s*(?:/|每).*?'
            r'[¥￥]\s*([\d.]+)\s*(?:/|每).*?'
            r'[¥￥]\s*([\d.]+)',
            re.IGNORECASE | re.DOTALL,
        )

        # 更鲁棒的方法：提取所有带价格的表格行
        # 先去掉 HTML 标签，保留文本结构
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)

        # 找价格行: "模型 输入价格 缓存命中 输出价格"
        # DeepSeek 页面通常格式: ¥3.00 / ¥0.025 / ¥6.00
        price_line = re.compile(
            r'(deepseek[-\w]*|v4[-\w]*).*?'
            r'[¥￥]\s*([\d.]+).*?'
            r'[¥￥]\s*([\d.]+).*?'
            r'[¥￥]\s*([\d.]+)',
            re.IGNORECASE,
        )

        for match in price_line.finditer(text):
            model_raw = match.group(1).strip().lower()
            try:
                inp = float(match.group(2))
                cache = float(match.group(3))
                out = float(match.group(4))
            except (ValueError, IndexError):
                continue

            # 映射模型名
            model = self._map_model(model_raw)
            if model and inp > 0 and out > 0:
                result[model] = {"input": inp, "cache_read": cache, "output": out}
                models_found += 1

        if models_found == 0:
            return None
        return result

    def _map_model(self, raw: str) -> str | None:
        """原始模型名 → 内部模型名"""
        raw = raw.lower().replace(" ", "").replace("-", "-")
        mapping = {
            "deepseek-chat": "deepseek-v4-pro",
            "deepseek-v4": "deepseek-v4-pro",
            "deepseek-v4-pro": "deepseek-v4-pro",
            "deepseek-v4-flash": "deepseek-v4-flash",
            "v4-pro": "deepseek-v4-pro",
            "v4-flash": "deepseek-v4-flash",
            "deepseek-reasoner": "deepseek-reasoner",
        }
        for key, val in mapping.items():
            if key in raw:
                return val
        return None


class OpenRouterSource(PriceSource):
    """OpenRouter 定价 API (JSON 格式)"""

    def __init__(self):
        super().__init__(
            name="openrouter",
            url="https://openrouter.ai/api/v1/models",
        )

    async def fetch(self, client: httpx.AsyncClient) -> dict[str, dict[str, float]] | None:
        try:
            resp = await client.get(self.url, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"OpenRouter API 返回 {resp.status_code}")
                return None
            data = resp.json()
            return self._parse(data)
        except Exception as e:
            logger.warning(f"OpenRouter 获取失败: {e}")
            return None

    def _parse(self, data: dict) -> dict[str, dict[str, float]] | None:
        """解析 OpenRouter models API 响应

        注意: OpenRouter 不暴露 cache_read/cache_creation 价格，
        因此只提取 input/output，cache_read 由 hardcoded 默认值提供。
        """
        result: dict[str, dict[str, float]] = {}
        models_data = data.get("data", [])

        for model_entry in models_data:
            model_id = model_entry.get("id", "")
            if model_id not in _OPENROUTER_MAP:
                continue

            pricing = model_entry.get("pricing", {})
            if not pricing:
                continue

            internal_name = _OPENROUTER_MAP[model_id]

            # OpenRouter 价格单位是 $/token, 需转为 ¥/百万token
            USD_TO_RMB = float(os.getenv("TOKENLENS_USD_TO_RMB", "7.25"))

            prompt_price = float(pricing.get("prompt", "0")) * 1_000_000 * USD_TO_RMB
            completion_price = float(pricing.get("completion", "0")) * 1_000_000 * USD_TO_RMB

            if prompt_price > 0 or completion_price > 0:
                result[internal_name] = {
                    "input": round(prompt_price, 4),
                    "output": round(completion_price, 4),
                    # cache_read 不在此设置，由硬编码默认值提供（更准确）
                }

        return result if result else None


# ─── 定价获取器 ─────────────────────────────────────────

class PricingFetcher:
    """定价获取器：从多个来源获取最新定价，带缓存降级"""

    def __init__(self, cache_path: Path | str | None = None):
        self._cache_path = Path(cache_path) if cache_path else DEFAULT_CACHE_PATH
        # 优先级: DeepSeek 官方 > LiteLLM 社区 > OpenRouter
        self._sources: list[PriceSource] = [
            DeepSeekSource(),
            LiteLLMSource(),
            OpenRouterSource(),
        ]

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def load_cache(self) -> dict[str, dict[str, float]] | None:
        """从本地缓存加载定价"""
        if not self._cache_path.exists():
            return None
        try:
            with open(self._cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            ts = cached.get("_fetched_at", 0)
            if time.time() - ts > CACHE_TTL:
                logger.info("定价缓存已过期")
                return None
            pricing = cached.get("pricing", {})
            if pricing:
                logger.info(f"从缓存加载定价: {len(pricing)} 模型, "
                           f"获取于 {time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}")
                return pricing
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"缓存文件损坏: {e}")
        return None

    def save_cache(self, pricing: dict[str, dict[str, float]]) -> None:
        """保存定价到本地缓存"""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "_fetched_at": time.time(),
            "_fetched_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pricing": pricing,
        }
        with open(self._cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        logger.info(f"定价缓存已保存: {self._cache_path}")

    async def fetch_all(self, force: bool = False) -> dict[str, dict[str, float]]:
        """从所有来源获取最新定价

        Args:
            force: 跳过缓存，强制重新获取

        Returns:
            {model_name: {input, cache_read, output}}  单位: RMB/百万token
        """
        # 1. 先检查缓存（非强制模式）
        if not force:
            cached = self.load_cache()
            if cached:
                # 合并硬编码默认值（缓存可能缺少某些模型）
                merged = dict(FALLBACK_PRICING)
                merged.update(cached)
                return merged

        # 2. 从各来源获取
        # 策略: 硬编码默认值（FALLBACK_PRICING）始终优先。
        # 上游来源（OpenRouter, web scraping）可能滞后或出错——
        # 例如 OpenRouter 的 deepseek-v4-flash 价格为错误估算。
        # 因此上游数据只用于「添加新模型」，不覆盖已有硬编码值。
        merged = dict(FALLBACK_PRICING)  # 兜底
        async with httpx.AsyncClient() as client:
            for source in self._sources:
                try:
                    fresh = await source.fetch(client)
                    if fresh:
                        logger.info(f"[{source.name}] 获取到 {len(fresh)} 个模型定价")
                        for model, prices in fresh.items():
                            if model not in merged:
                                # 新模型：直接添加
                                merged[model] = dict(prices)
                            # 已有模型：不覆盖 input/output（硬编码值更可靠）
                            # 只在缓存缺少 cache_read 时补充
                            elif "cache_read" not in merged[model] and "cache_read" in prices:
                                merged[model]["cache_read"] = prices["cache_read"]
                except Exception as e:
                    logger.warning(f"[{source.name}] 获取异常: {e}")

        # 3. 保存缓存
        self.save_cache(merged)
        return merged

    def fetch_sync(self, force: bool = False, timeout: int = 30) -> dict[str, dict[str, float]]:
        """同步封装（用于 CLI）"""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.fetch_all(force=force))
        else:
            # 已在事件循环中，用线程池
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.fetch_all(force=force))
                return future.result(timeout=timeout)


# ─── 便捷函数 ──────────────────────────────────────────

# 全局单例
_fetcher: PricingFetcher | None = None


def get_fetcher(cache_path: Path | str | None = None) -> PricingFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = PricingFetcher(cache_path=cache_path)
    return _fetcher


async def refresh_pricing(force: bool = False) -> dict[str, dict[str, float]]:
    """刷新定价数据（异步）"""
    return await get_fetcher().fetch_all(force=force)
