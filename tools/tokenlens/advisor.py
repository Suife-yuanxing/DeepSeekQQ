"""缓存 AI 建议 — 规则引擎 + LLM 增强"""

import os
import time
from typing import Any

import httpx

# 规则层（无需 LLM，即时输出）
RULES = [
    {
        "condition": lambda hr: hr < 0.60,
        "severity": "🔴 异常",
        "advice": "缓存命中率异常偏低——检查是否频繁切换项目、会话是否过长超过 5min TTL",
    },
    {
        "condition": lambda hr: 0.60 <= hr < 0.80,
        "severity": "🟡 偏低",
        "advice": "缓存利用率有提升空间，考虑减少并行项目数以维持缓存热度",
    },
    {
        "condition": lambda hr: 0.80 <= hr < 0.95,
        "severity": "🟢 正常",
        "advice": "缓存策略工作良好",
    },
    {
        "condition": lambda hr: hr >= 0.95,
        "severity": "💎 极佳",
        "advice": "超高效缓存利用，长上下文稳定性优秀",
    },
]

# 趋势告警：连续 N 个 session 命中率下降超过阈值
TREND_DECLINE_SESSIONS = 3
TREND_DECLINE_THRESHOLD = 0.20

# 超高缓存复用告警
CACHE_READ_RATIO_THRESHOLD = 10


def get_rule_advice(
    cache_hit_rate: float,
    cache_read_tokens: int = 0,
    input_tokens: int = 0,
    hit_rate_history: list[float] | None = None,
) -> dict[str, Any]:
    """基于规则检测缓存命中率并输出建议

    返回 {
        "severity": str,
        "advice": str,
        "warnings": [str, ...],
        "llm_enhanced": bool,
        "llm_advice": str | None,
    }
    """
    warnings: list[str] = []

    # 1. 基础命中率检查
    severity = "🟢 正常"
    advice = ""
    for rule in RULES:
        if rule["condition"](cache_hit_rate):
            severity = rule["severity"]
            advice = rule["advice"]
            break

    # 2. 趋势告警
    if hit_rate_history and len(hit_rate_history) >= TREND_DECLINE_SESSIONS:
        recent = hit_rate_history[-TREND_DECLINE_SESSIONS:]
        if (
            len(recent) == TREND_DECLINE_SESSIONS
            and recent[0] - recent[-1] > TREND_DECLINE_THRESHOLD
        ):
            warnings.append(
                f"缓存命中率持续下滑（{recent[0]:.1%} → {recent[-1]:.1%}），"
                "检查是否最近开始频繁切换项目或引入新工具"
            )

    # 3. 单次会话超高缓存复用
    if input_tokens > 0 and cache_read_tokens / input_tokens > CACHE_READ_RATIO_THRESHOLD:
        warnings.append("单次会话超高缓存复用，可考虑进一步延长会话")

    return {
        "severity": severity,
        "advice": advice,
        "warnings": warnings,
        "llm_enhanced": False,
        "llm_advice": None,
    }


async def get_llm_advice(
    model_stats: dict[str, Any],
    cache_hit_rate: float,
    timeout: int = 10,
    max_retries: int = 2,
) -> str | None:
    """调用 DeepSeek API 生成个性化建议

    失败时返回 None（调用方应降级到规则建议）
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None

    prompt = f"""根据以下 token 用量统计数据，给出 2-3 条中文缓存优化建议：

- 模型: {model_stats.get('model', 'unknown')}
- 缓存命中率: {cache_hit_rate:.1%}
- 输入 tokens: {model_stats.get('input', 0):,}
- 缓存读取 tokens: {model_stats.get('cache_read', 0):,}
- 输出 tokens: {model_stats.get('output', 0):,}
- 消息数: {model_stats.get('count', 0):,}
- session 数: {model_stats.get('session_count', 0):,}

请直接给出简洁建议，无需介绍。每条建议不超过一句话。"""

    last_error: str | None = None
    for attempt in range(1 + max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 300,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0]["message"]["content"].strip()
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except (httpx.TimeoutException, httpx.ConnectError, OSError) as e:
            last_error = str(e)

        if attempt < max_retries:
            # 指数退避: 1s, 3s
            await _async_sleep(2**attempt)

    return None


async def _async_sleep(seconds: float) -> None:
    """异步 sleep（避免 time.sleep 阻塞事件循环）"""
    import asyncio
    await asyncio.sleep(seconds)


# LLM 建议缓存
_llm_cache: dict[str, tuple[float, str]] = {}  # key → (timestamp, advice)


def get_cached_or_none(cache_key: str, max_age: float = 3600) -> str | None:
    """获取缓存的 LLM 建议，过期返回 None"""
    entry = _llm_cache.get(cache_key)
    if entry is None:
        return None
    ts, advice = entry
    if time.time() - ts > max_age:
        del _llm_cache[cache_key]
        return None
    return advice


def set_llm_cache(cache_key: str, advice: str) -> None:
    _llm_cache[cache_key] = (time.time(), advice)
