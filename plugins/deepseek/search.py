"""联网搜索模块（Phase 3）。

使用 Tavily Search API 实现：
- 智能判断是否需要搜索
- 执行搜索并格式化结果
- 搜索结果注入 prompt
"""
import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from nonebot import logger

from .config import (
    TAVILY_API_KEY, SEARCH_ENABLED, SEARCH_MAX_RESULTS,
    SEARCH_CACHE_TTL
)
from . import api

# ============================================================
# 数据结构
# ============================================================

@dataclass
class SearchResult:
    query: str
    results: List[Dict[str, str]]  # [{title, url, snippet}]
    answer: str = ""  # Tavily 的 AI 摘要


# ============================================================
# 搜索触发判断
# ============================================================

# 显式搜索关键词
_EXPLICIT_SEARCH_KEYWORDS = [
    "搜一下", "搜索", "查一下", "查查", "帮我查", "帮我搜",
    "百度一下", "谷歌", "google", "搜一搜",
]

# 时间敏感词（暗示需要最新信息）
_TIME_SENSITIVE_KEYWORDS = [
    "今天", "今日", "最新", "最近", "现在", "刚刚", "昨天",
    "明天", "本周", "这周", "本月", "今年", "新闻", "热搜",
]

# 事实性问题模式
_FACT_PATTERNS = [
    r".*多少钱", r".*价格", r".*在哪", r".*地址",
    r".*怎么去", r".*什么时候", r".*几点",
    r".*是谁", r".*是什么", r".*怎么样",
    r"现在.*", r"今天.*天气.*",
]


def should_search(user_msg: str) -> bool:
    """判断用户消息是否需要联网搜索。"""
    if not SEARCH_ENABLED or not TAVILY_API_KEY:
        return False

    msg = user_msg.strip()

    # 1. 显式搜索请求
    if any(kw in msg for kw in _EXPLICIT_SEARCH_KEYWORDS):
        return True

    # 2. 时间敏感词 + 疑问句
    has_time = any(kw in msg for kw in _TIME_SENSITIVE_KEYWORDS)
    is_question = any(kw in msg for kw in ["吗", "?", "？", "怎么", "为什么", "啥", "多少", "呢", "是什么"])
    if has_time and is_question:
        return True

    # 3. 事实性问题模式
    for pattern in _FACT_PATTERNS:
        if re.match(pattern, msg):
            return True

    # 4. 长消息中的搜索意图（超过10字且包含"查"/"搜"/"了解"）
    if len(msg) > 10 and any(kw in msg for kw in ["查", "搜", "了解", "知道", "介绍"]):
        return True

    return False


# ============================================================
# 搜索执行
# ============================================================

# 简单的内存缓存
_search_cache: Dict[str, tuple] = {}  # query -> (result, timestamp)


async def search(query: str, max_results: int = None) -> Optional[SearchResult]:
    """执行 Tavily 搜索。"""
    if not TAVILY_API_KEY:
        logger.warning("[搜索] TAVILY_API_KEY 未配置")
        return None

    import time
    if max_results is None:
        max_results = SEARCH_MAX_RESULTS

    # 检查缓存
    cache_key = query.strip().lower()
    if cache_key in _search_cache:
        cached_result, cached_time = _search_cache[cache_key]
        if time.time() - cached_time < SEARCH_CACHE_TTL:
            logger.info(f"[搜索] 命中缓存: {query[:30]}")
            return cached_result

    try:
        from tavily import AsyncTavilyClient
        client = AsyncTavilyClient(api_key=TAVILY_API_KEY)

        logger.info(f"[搜索] 执行搜索: {query[:50]}")
        response = await client.search(
            query=query,
            max_results=max_results,
            search_depth="advanced",    # 深度搜索，结果更全更新
            include_answer=True,
            days=3,                     # 只要最近3天的结果
        )

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")[:300],
            })

        answer = response.get("answer", "")

        search_result = SearchResult(
            query=query,
            results=results,
            answer=answer,
        )

        # 写入缓存
        _search_cache[cache_key] = (search_result, time.time())
        # 缓存上限
        if len(_search_cache) > 100:
            oldest_key = min(_search_cache, key=lambda k: _search_cache[k][1])
            del _search_cache[oldest_key]

        logger.info(f"[搜索] 完成: {len(results)} 条结果")
        return search_result

    except ImportError:
        logger.error("[搜索] tavily-python 未安装，执行: pip install tavily-python")
        return None
    except Exception as e:
        logger.error(f"[搜索] Tavily 调用失败: {e}")
        return None


# ============================================================
# 结果格式化
# ============================================================

def format_search_for_prompt(result: Optional[SearchResult]) -> str:
    """将搜索结果格式化为 prompt 注入文本。"""
    if not result or not result.results:
        return ""

    lines = [f"【联网搜索结果】（查询：{result.query}）"]

    if result.answer:
        lines.append(f"AI摘要：{result.answer[:300]}")

    for i, item in enumerate(result.results[:3], 1):
        title = item["title"][:60]
        snippet = item["snippet"][:200]
        url = item["url"]
        # 直接包含完整URL，方便AI分享
        lines.append(f"{i}. [{title}] {snippet}\n   链接：{url}")

    lines.append("请基于以上最新信息回答，引用时注明来源。如果信息不够准确，坦诚说明。")

    return "\n".join(lines)


def extract_search_query(user_msg: str) -> str:
    """从用户消息中提取搜索关键词（去除礼貌用语等）。"""
    # 去除常见搜索前缀
    cleaned = user_msg
    prefixes = ["帮我搜一下", "帮我查一下", "搜一下", "查一下", "搜索", "查查", "帮我查", "帮我搜", "百度一下"]
    for p in prefixes:
        if cleaned.startswith(p):
            cleaned = cleaned[len(p):]
            break

    # 去除尾部语气词
    cleaned = re.sub(r"[吗呢吧呀啊哦~]+$", "", cleaned).strip()

    return cleaned or user_msg
