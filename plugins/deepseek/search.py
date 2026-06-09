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

# 显式搜索关键词（用户明确要求搜索）
_EXPLICIT_SEARCH_KEYWORDS = [
    "搜一下", "搜索", "查一下", "查查", "帮我查", "帮我搜",
    "百度一下", "谷歌", "google", "搜一搜", "查一查",
    "帮我找", "找一下", "搜搜", "看看新闻", "有什么新闻",
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
    r".*是谁", r".*是什么",
    r"现在.*",
]

# 闲聊排除模式（这些场景即使匹配了时间敏感词也不搜索）
_CASUAL_EXCLUDE_PATTERNS = [
    r'^.{0,4}(吗|嘛|吧|呢|啊|呀|哦|啦|哈|嘛)$',  # 短句语气词结尾
    r'^(嗯|哦|好|行|对|是|嗯嗯|好的|哈哈|嘿嘿|呜|哼|额|唔|666)',  # 简短回应
    r'^(你在|你在吗|在吗|在不在|干嘛|干嘛呢|干啥)',  # 问候/闲聊
    r'(想你|喜欢你|讨厌|哼|抱抱|亲亲|摸摸|乖|可爱)',  # 情感表达
    r'(晚安|早安|早上好|晚上好|下午好|中午好|拜拜|再见|明天见)',  # 寒暄
    r'^(啥|怎么了|怎么啦|咋了|咋啦|出了什么事)',  # 简短追问
]


def should_search(user_msg: str) -> dict:
    """判断用户消息是否需要联网搜索。

    Returns:
        {"need_search": bool, "is_explicit": bool}
        - need_search: 是否需要搜索
        - is_explicit: 是否用户明确要求搜索（决定是否展示链接）
    """
    empty_result = {"need_search": False, "is_explicit": False}
    if not SEARCH_ENABLED or not TAVILY_API_KEY:
        return empty_result

    msg = user_msg.strip()

    # 排除：简单的地点陈述
    _location_only_patterns = [
        r'^我在[一-龥]{2,4}$',
        r'^来[一-龥]{2,4}了?$',
        r'^到[一-龥]{2,4}了?$',
        r'^去[一-龥]{2,4}$',
        r'^[一-龥]{2,4}人$',
        r'^[一-龥]{2,4}(的|呢)$',
        r'^[一-龥]{2,4}(今天|现在)?天气',
    ]
    for pattern in _location_only_patterns:
        if re.match(pattern, msg):
            return empty_result

    # 排除：闲聊场景
    for pattern in _CASUAL_EXCLUDE_PATTERNS:
        if re.match(pattern, msg):
            return empty_result

    # 排除：过短消息（<6字且不含搜索关键词）
    if len(msg) < 6 and not any(kw in msg for kw in _EXPLICIT_SEARCH_KEYWORDS):
        return empty_result

    # 1. 显式搜索请求 → 需要搜索 + 展示链接
    if any(kw in msg for kw in _EXPLICIT_SEARCH_KEYWORDS):
        return {"need_search": True, "is_explicit": True}

    # 2. 时间敏感词 + 疑问句 → 需要搜索 + 不展示链接（仅提供上下文）
    has_time = any(kw in msg for kw in _TIME_SENSITIVE_KEYWORDS)
    is_question = any(kw in msg for kw in ["吗", "?", "？", "怎么", "为什么", "啥", "多少", "呢", "是什么"])
    if has_time and is_question:
        return {"need_search": True, "is_explicit": False}

    # 3. 事实性问题模式 → 需要搜索 + 不展示链接
    for pattern in _FACT_PATTERNS:
        if re.match(pattern, msg):
            if re.match(r'^[一-龥]{2,4}怎么样$', msg):
                return empty_result
            return {"need_search": True, "is_explicit": False}

    # 4. 长消息中的搜索意图 → 需要搜索 + 不展示链接
    if len(msg) > 10 and any(kw in msg for kw in ["查", "搜", "了解", "知道", "介绍"]):
        return {"need_search": True, "is_explicit": False}

    return empty_result


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

        # 质量检查：结果太少或太短时用同义词重试一次
        if len(results) < 2 or all(len(r.get("snippet", "")) < 30 for r in results):
            logger.info(f"[搜索] 结果质量低，尝试同义词重搜")
            alt_query = _get_synonym_query(query)
            if alt_query != query:
                alt_result = await _tavily_search(client, alt_query, max_results)
                if alt_result and len(alt_result.get("results", [])) > len(results):
                    results = [
                        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")[:300]}
                        for r in alt_result.get("results", [])
                    ]
                    answer = alt_result.get("answer", answer)

        search_result = SearchResult(query=query, results=results, answer=answer)

        # 写入缓存（统一上限 200）
        _search_cache[cache_key] = (search_result, time.time())
        # 缓存上限
        if len(_search_cache) > 200:
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


async def _tavily_search(client, query: str, max_results: int) -> dict:
    """封装 Tavily 搜索调用。"""
    try:
        return await client.search(
            query=query, max_results=max_results,
            search_depth="advanced", include_answer=True, days=3,
        )
    except Exception:
        return {}


def _get_synonym_query(query: str) -> str:
    """生成同义词查询（简单策略：替换关键词）。"""
    synonyms = {
        "怎么": "如何", "如何": "怎么",
        "什么": "啥", "啥": "什么",
        "价格": "多少钱", "多少钱": "价格",
        "在哪": "地址", "地址": "在哪",
    }
    for old, new in synonyms.items():
        if old in query:
            return query.replace(old, new, 1)
    return query


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
