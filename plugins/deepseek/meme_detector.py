"""热梗自动检测模块。

功能：
- 从热搜话题中自动识别新兴网络热梗/流行语
- 过滤假阳性（品牌名、地名、普通新闻标题等）
- 合并到 meme_lexicon 的动态词库
- 新鲜度衰减（72h TTL）

设计原则：
- 每次热梗检测只需一次LLM调用（批量提取），成本可控
- 与 hot_topics.py 联动：热搜获取→梗检测→词库更新
- 使用 GLM-4-Flash（免费）做检测，避免消耗 DeepSeek token
"""
import json
import re
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger


# ============================================================
# 检测 prompt
# ============================================================

_MEME_DETECTION_PROMPT = """你是一个互联网文化观察者。以下是今天的热搜话题列表。

请从中识别出近期流行的**网络热梗/流行语/网络用语**。

规则：
1. 只提取真正的网络用语和梗，不要提取普通新闻标题、品牌名、地名、人名
2. 必须是"梗"——有特定含义、特定使用场景的流行语
3. 2-6个字的短词优先（中文网络梗通常简短）
4. 如果没发现新梗，返回空列表 []

返回严格JSON数组格式（不要markdown代码块）：
[{"word": "梗词条", "meaning": "一句话含义", "mood": "使用场景情绪(开心/无语/调侃/惊讶/感动/吐槽)", "keywords": ["触发词1", "触发词2"]}]

热搜话题：
{topics_text}"""


# ============================================================
# 假阳性过滤规则
# ============================================================

# 禁止词汇模式（品牌/地名/人名/敏感词）
_BLOCKED_PATTERNS = [
    r'^[A-Za-z0-9]+$',           # 纯英文/数字
    r'^(华为|苹果|小米|OPPO|vivo|三星|比亚迪|特斯拉)',  # 品牌
    r'^(北京|上海|广州|深圳|杭州|成都|武汉|南京|重庆)',  # 城市
    r'^(特朗普|拜登|普京|泽连斯基|马斯克)',            # 政治人物/名人
    r'(地震|火灾|车祸|坠机|爆炸|袭击|死亡|遇难)',      # 灾难
    r'(政府|国务院|外交部|解放军|公安局|税务局)',      # 政府
    r'(股票|基金|A股|美股|涨停|跌停|IPO)',            # 金融
    r'(人民币|美元|汇率|CPI|GDP)',                    # 经济指标
]

# 必须包含的特征（至少一个）：
# - 谐音/空耳
# - 缩写/拼音首字母
# - 反讽/自嘲/吐槽色彩
# - 特定圈子用语（游戏圈/二次元/饭圈）
_MEME_FEATURE_KEYWORDS = [
    "梗", "热词", "流行语", "网络用语", "新词", "黑话",
    "谐音", "缩写", "拼音", "空耳", "方言",
    "自嘲", "吐槽", "调侃", "反讽", "阴阳怪气",
    "二次元", "游戏圈", "饭圈", "B站", "抖音",
]


def filter_meme_candidates(candidates: List[Dict]) -> List[Dict]:
    """过滤假阳性候选梗。

    Args:
        candidates: LLM返回的候选列表

    Returns:
        过滤后的有效梗列表
    """
    if not candidates:
        return []

    filtered = []
    for c in candidates:
        word = c.get("word", "").strip()
        meaning = c.get("meaning", "").strip()

        # 基本长度检查
        if len(word) < 2 or len(word) > 12:
            logger.debug(f"[梗检测] 长度不符: {word}")
            continue

        # 纯数字/纯符号
        if word.isdigit() or all(ch in "!@#$%^&*()+-=[]{}/?.,<>;:'\"" for ch in word):
            continue

        # 检查禁止模式
        blocked = False
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, word):
                blocked = True
                logger.debug(f"[梗检测] 命中禁止模式: {word} -> {pattern}")
                break
        if blocked:
            continue

        # 含义不能为空
        if not meaning or len(meaning) < 2:
            continue

        # 标准化
        c["word"] = word
        c["meaning"] = meaning
        c["mood"] = c.get("mood", "日常")
        c["keywords"] = c.get("keywords", [])

        filtered.append(c)

    logger.info(f"[梗检测] 过滤后: {len(filtered)}/{len(candidates)} 有效")
    return filtered


# ============================================================
# LLM 调用
# ============================================================

async def detect_new_memes_from_trending(
    topics: List[Any],
    model: str = None,
) -> List[Dict]:
    """从热搜话题中检测新梗。

    Args:
        topics: HotTopic 对象列表（需要有 .title 属性）
        model: 使用的模型，默认 None 自动选择

    Returns:
        检测到的梗列表 [{word, meaning, mood, keywords, confidence}]
    """
    if not topics:
        return []

    # 构建话题文本
    topic_lines = []
    for t in topics[:30]:  # 最多30个话题
        title = getattr(t, "title", str(t))
        category = getattr(t, "category", "")
        line = f"- [{category}] {title}" if category else f"- {title}"
        topic_lines.append(line)
    topics_text = "\n".join(topic_lines)

    try:
        # 优先用 GLM-4-Flash（免费），失败则用 DeepSeek
        result = await _call_glm_detect(topics_text)
        if result is None:
            result = await _call_deepseek_detect(topics_text)

        if result is None:
            logger.warning("[梗检测] LLM调用失败，跳过本轮检测")
            return []

        # 过滤
        filtered = filter_meme_candidates(result)
        if filtered:
            logger.info(f"[梗检测] 发现新梗: {[m['word'] for m in filtered]}")
        else:
            logger.debug("[梗检测] 本轮未发现新梗")

        return filtered

    except Exception as e:
        logger.error(f"[梗检测] 检测异常: {e}")
        return []


async def _call_glm_detect(topics_text: str) -> Optional[List[Dict]]:
    """用 GLM-4-Flash（免费）做梗检测。"""
    try:
        from .config import GLM_API_KEY
        if not GLM_API_KEY:
            return None

        import aiohttp
        from .api import get_http_session

        session = await get_http_session()
        headers = {
            "Authorization": f"Bearer {GLM_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "glm-4-flash",
            "messages": [
                {"role": "user", "content": _MEME_DETECTION_PROMPT.format(topics_text=topics_text)}
            ],
            "temperature": 0.3,
            "max_tokens": 800,
        }

        async with session.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            if resp.status != 200:
                logger.debug(f"[梗检测] GLM API返回 {resp.status}")
                return None
            data = await resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return _parse_llm_response(content)

    except ImportError:
        return None
    except Exception as e:
        logger.debug(f"[梗检测] GLM调用异常: {e}")
        return None


async def _call_deepseek_detect(topics_text: str) -> Optional[List[Dict]]:
    """用 DeepSeek 做梗检测（GLM不可用时的fallback）。"""
    try:
        from .api import call_deepseek_api

        messages = [
            {"role": "system", "content": "你是互联网文化观察者。只返回JSON数组，不要任何其他文字。"},
            {"role": "user", "content": _MEME_DETECTION_PROMPT.format(topics_text=topics_text)},
        ]
        content = await call_deepseek_api(messages, temperature=0.3)
        return _parse_llm_response(content)
    except Exception as e:
        logger.debug(f"[梗检测] DeepSeek调用异常: {e}")
        return None


def _parse_llm_response(content: str) -> Optional[List[Dict]]:
    """解析LLM返回的JSON。"""
    if not content:
        return None

    content = content.strip()

    # 移除 markdown 代码块
    content = re.sub(r'^```(?:json)?\s*', '', content)
    content = re.sub(r'\s*```$', '', content)

    try:
        result = json.loads(content)
        if isinstance(result, list):
            return result
        # 有时LLM返回 {"memes": [...]}
        if isinstance(result, dict):
            for key in ["memes", "results", "data", "items"]:
                if key in result and isinstance(result[key], list):
                    return result[key]
        return []
    except json.JSONDecodeError:
        # 尝试提取JSON数组
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.debug(f"[梗检测] JSON解析失败: {content[:100]}")
        return None


# ============================================================
# 合并到词库
# ============================================================

def merge_into_lexicon(
    new_memes: List[Dict],
    existing_dynamic_memes: List[Dict],
    max_count: int = 10,
) -> List[Dict]:
    """合并新梗到动态词库。

    去重逻辑：word完全匹配或meaning高度相似的去重。

    Returns:
        合并后的动态词库列表
    """
    if not new_memes:
        return existing_dynamic_memes

    now = time.time()
    merged = list(existing_dynamic_memes)  # 拷贝

    existing_words = {m["word"] for m in merged}
    existing_meanings = {m.get("meaning", "")[:10] for m in merged}

    for meme in new_memes:
        word = meme.get("word", "").strip()
        meaning = meme.get("meaning", "").strip()

        # 去重：word完全相同
        if word in existing_words:
            continue

        # 去重：meaning前10字相同
        if meaning[:10] in existing_meanings:
            continue

        # 添加
        merged.append({
            "word": word,
            "meaning": meaning,
            "example": meme.get("example", f"这也太{word}了吧"),
            "mood": [meme.get("mood", "日常")],
            "affection_min": 0,
            "keywords": meme.get("keywords", []),
            # 动态词库专有字段
            "_dynamic": True,
            "_added_at": now,
        })
        existing_words.add(word)
        existing_meanings.add(meaning[:10])

        if len(merged) >= max_count:
            logger.info(f"[梗检测] 动态词库已满 ({max_count})，停止合并")
            break

    # 清理过期动态梗
    merged = clean_stale_memes(merged)

    return merged


def clean_stale_memes(
    dynamic_memes: List[Dict],
    ttl_hours: int = 72,
) -> List[Dict]:
    """清理过期的动态梗。"""
    now = time.time()
    ttl_seconds = ttl_hours * 3600
    return [
        m for m in dynamic_memes
        if not m.get("_dynamic") or (now - m.get("_added_at", now)) < ttl_seconds
    ]


def decay_meme_freshness(
    dynamic_memes: List[Dict],
) -> List[Dict]:
    """标记动态梗的新鲜度衰减（调用 clean_stale_memes）。"""
    return clean_stale_memes(dynamic_memes)
