"""模型定价表 — 所有价格以 RMB (元/百万token) 为统一存储单位

定价来源（优先级从高到低）:
  1. 环境变量 TOKENLENS_PRICING_JSON — 手动覆盖
  2. 本地缓存 ~/.tokenlens/pricing_cache.json — 自动刷新
  3. 硬编码默认值 — 定期手动更新，反映最新官方价格

更新方法:
  python -m tools.tokenlens --fetch-pricing       # 手动刷新缓存
  python -m tools.tokenlens --fetch-pricing --verbose  # 详细日志
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("tokenlens.pricing")

# 默认汇率
USD_TO_RMB = float(os.getenv("TOKENLENS_USD_TO_RMB", "7.25"))

# ─── 硬编码默认值 — 官方定价，单位 RMB/百万token ──────
# 最后更新: 2026-06-17
#
# DeepSeek 使用官方人民币定价（api-docs.deepseek.com 标注的 ¥ 价格）。
# 这些价格不通过 USD→RMB 汇率折算，而是 DeepSeek 直接以人民币标价。
#
# DeepSeek V4 Pro:
#   输入 ¥3.00 | 缓存命中 ¥0.025 | 输出 ¥6.00
#   (USD 参考: $0.435 / $0.003625 / $0.87, 内部汇率 ~7.14)
#
# DeepSeek V4 Flash:
#   输入 ¥1.00 | 缓存命中 ¥0.02 | 输出 ¥2.00
#   (USD 参考: $0.14 / $0.0028 / $0.28, 内部汇率 ~7.14)
#   ⚠️ 缓存命中价格于 2026-04-26 下调至 $0.0028 (此前为 $0.0048)
#
# MiMo V2.5 Pro: 对标 DeepSeek V4 Pro 定价
# MiMo V2.5:    对标 DeepSeek V4 Flash 定价
#
# Kimi K2.6 (Moonshot 官方): $0.95 输入 / $0.16 缓存命中 / $4.00 输出
#   按汇率 7.25 折算为人民币
#
# Claude 家族 (Anthropic 官方, USD→RMB @7.25)
#   价格如有变化请通过 TOKENLENS_PRICING_JSON 环境变量覆盖

_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    # DeepSeek 家族 — 官方人民币定价
    "deepseek-v4-pro":   {"input": 3.00, "cache_read": 0.025, "output": 6.00},
    "deepseek-v4-flash": {"input": 1.00, "cache_read": 0.02,  "output": 2.00},
    # 小米 MiMo (对标 DeepSeek)
    "mimo-v2.5-pro":     {"input": 3.00, "cache_read": 0.025, "output": 6.00},
    "mimo-v2.5":         {"input": 1.00, "cache_read": 0.02,  "output": 2.00},
    # Kimi/Moonshot — USD 官方价 × 7.25 汇率
    # $0.95 / $0.16 / $4.00
    "kimi-k2.6":         {"input": 6.89, "cache_read": 1.16, "output": 29.0},
    # Claude 家族 (Anthropic 官方, USD→RMB @7.25)
    # Sonnet 4: $3 / $0.30 / $15, Opus 4: $15 / $1.50 / $75
    "claude-sonnet-4-20250514": {"input": 21.75, "cache_read": 2.18, "output": 108.75},
    "claude-opus-4-20250514":   {"input": 108.75, "cache_read": 10.88, "output": 543.75},
}

# 缓存文件路径
CACHE_PATH = Path.home() / ".tokenlens" / "pricing_cache.json"


def _load_pricing() -> dict[str, dict[str, float]]:
    """加载定价表：环境变量 > 硬编码默认值 > 本地缓存

    硬编码默认值（_DEFAULT_PRICING）是手动验证过的官方定价，
    不会被缓存数据覆盖。缓存数据仅用于：
      1. 添加新模型（硬编码表中没有的）
      2. 补充 cache_read（当硬编码未提供时）

    环境变量 TOKENLENS_PRICING_JSON 具有最高优先级，
    可覆盖所有值（包括硬编码的 input/output/cache_read）。
    """
    merged = dict(_DEFAULT_PRICING)

    # 记录哪些模型的值来自硬编码（不应被缓存覆盖）
    hardcoded_models = set(_DEFAULT_PRICING.keys())

    # 1. 尝试加载本地缓存（仅添加新模型，不覆盖已有硬编码值）
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            pricing = cached.get("pricing", {})
            if pricing:
                fetched_at = cached.get("_fetched_iso", "unknown")
                new_count = 0
                for model, prices in pricing.items():
                    if model not in merged:
                        # 新模型，直接添加
                        merged[model] = dict(prices)
                        new_count += 1
                    elif "cache_read" not in merged[model] and "cache_read" in prices:
                        # 硬编码模型但缺少 cache_read：从缓存补充
                        merged[model]["cache_read"] = prices["cache_read"]
                logger.debug(f"定价缓存已加载 (获取于 {fetched_at}), "
                           f"新增 {new_count} 模型, "
                           f"保护 {len(hardcoded_models)} 个硬编码模型")
        except (json.JSONDecodeError, KeyError):
            pass

    # 2. 环境变量覆盖（最高优先级）
    override_json = os.getenv("TOKENLENS_PRICING_JSON", "")
    if override_json:
        try:
            override = json.loads(override_json)
            if isinstance(override, dict):
                merged.update(override)
                logger.debug("定价已从环境变量 TOKENLENS_PRICING_JSON 覆盖")
        except json.JSONDecodeError:
            logger.warning("TOKENLENS_PRICING_JSON 解析失败，已忽略")

    return merged


PRICING = _load_pricing()


def reload_pricing() -> dict[str, dict[str, float]]:
    """重新加载定价（缓存刷新后调用）"""
    global PRICING
    PRICING = _load_pricing()
    return PRICING


def get_price(model: str, token_type: str) -> float | None:
    """获取指定模型的单百万 token 价格（RMB）

    token_type: 'input' | 'cache_read' | 'output'
    返回 None 表示未知价格
    """
    model_pricing = PRICING.get(model)
    if model_pricing is None:
        return None
    return model_pricing.get(token_type)


def calc_cost(
    model: str,
    input_tokens: int,
    cache_read_tokens: int,
    output_tokens: int,
) -> float | None:
    """计算单条记录的费用（RMB）

    返回 None 表示模型未知，无法计算
    """
    prices = PRICING.get(model)
    if prices is None:
        return None

    total = 0.0
    total += (input_tokens / 1_000_000) * prices["input"]
    total += (cache_read_tokens / 1_000_000) * prices["cache_read"]
    total += (output_tokens / 1_000_000) * prices["output"]
    return total
