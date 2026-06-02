"""分享内容抓取与缓存。
- 修复 B 站正则 typo
- 分享卡片去重
- 内存缓存全局 TTL 清理（防泄漏）
- 按平台解析 + 通用 fallback
- 全局 URL 抓取冷却（防重复请求）+ 容量上限防泄漏
"""
import os
import re
import json
import hashlib
from datetime import datetime
from typing import Optional, List, Dict, Any
from collections import OrderedDict

import aiohttp

from .config import SHARE_TTL, VOICE_DIR
from .database import get_article_cache, save_article_cache
from .api import get_http_session

_recent_shares: Dict[str, List[Dict[str, Any]]] = {}

# 使用 OrderedDict 实现 LRU，限制最大容量防止无限增长
class LRUCooldownDict(OrderedDict):
    MAX_SIZE = 500  # 最多缓存 500 个 URL

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        else:
            if len(self) >= self.MAX_SIZE:
                oldest = next(iter(self))
                del self[oldest]
        super().__setitem__(key, value)

_url_fetch_cooldown: Dict[str, float] = LRUCooldownDict()
URL_FETCH_COOLDOWN_SECONDS = 300  # 5分钟内不重复抓取同一URL


# ==================== 内容有效性校验 ====================

def _is_valid_article(article: Optional[Dict[str, str]]) -> bool:
    if not article or not article.get("summary"):
        return False
    if article.get("needs_paste") or article.get("platform") == "小黑盒":
        return True
    if article.get("restricted"):
        return True
    summary = article["summary"].strip()
    if len(summary) < 80:
        return False
    invalid_markers = [
        "页面框架", "内容被截断", "未登录", "登录后查看",
        "内容为空", "只有框架", "技术性参数", "shallowReactive"
    ]
    return not any(m in summary for m in invalid_markers)


def _is_valid_share(s: Dict[str, Any]) -> bool:
    summary = s.get("summary", "")
    if not summary:
        return False
    if s.get("needs_paste") or s.get("platform") == "小黑盒":
        return True
    if s.get("restricted"):
        return True
    if len(summary.strip()) < 80:
        return False
    invalid_markers = [
        "页面框架", "内容被截断", "未登录", "登录后查看",
        "内容为空", "只有框架", "技术性参数", "shallowReactive"
    ]
    return not any(m in summary for m in invalid_markers)


# ==================== 增强网页抓取 ====================

async def fetch_url_content(url: str) -> Optional[Dict[str, str]]:
    """增强版网页抓取，按平台解析，带缓存和全局冷却。"""
    now = datetime.now().timestamp()
    last_fetch = _url_fetch_cooldown.get(url, 0)
    if now - last_fetch < URL_FETCH_COOLDOWN_SECONDS:
        # 冷却中，尝试返回缓存
        cache_key = hashlib.md5(url.encode()).hexdigest()
        cached = await get_article_cache(cache_key)
        if cached:
            return cached
        return None

    cache_key = hashlib.md5(url.encode()).hexdigest()
    cached = await get_article_cache(cache_key)
    if cached:
        return cached

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9"
    }

    try:
        session = await get_http_session()
        async with session.get(
            url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True
        ) as resp:
            if resp.status != 200:
                return None
            final_url = str(resp.url)
            html = await resp.text()
            if len(html) > 500_000:
                html = html[:500_000]

            result = _parse_by_platform(html, final_url)
            if not result:
                result = _parse_generic(html)

            if result and result.get("summary"):
                _url_fetch_cooldown[url] = now
                await save_article_cache(
                    cache_key, url,
                    result.get("title", ""),
                    result.get("author", ""),
                    result["summary"]
                )
            return result

    except Exception as e:
        print(f"[分享] 抓取失败 {url[:60]}: {e}")
        return None


def _parse_by_platform(html: str, url: str) -> Optional[Dict[str, str]]:
    """按平台解析。返回统一格式的字典。"""
    base_fields = {
        "comments": "",
        "cached": False,
        "restricted": False,
        "needs_paste": False,
        "url": url
    }

    if "xiaoheike" in url or "xiaoheihe" in url or "xiaoheih" in url:
        title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        if not title:
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else "小黑盒分享"
        desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        desc = desc.group(1) if desc else ""
        return {
            **base_fields,
            "title": title,
            "author": "小黑盒",
            "summary": f"[小黑盒内容无法自动读取，需要用户粘贴正文] {desc[:200]}",
            "restricted": True,
            "platform": "小黑盒",
            "needs_paste": True,
        }

    if "bilibili.com/read" in url or "bilibili.com/opus" in url:
        title = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html)
        title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else "B站专栏"
        author = re.search(r'"name":"([^"]+)"', html)
        author = author.group(1) if author else "未知UP"
        content = re.search(
            r'<div[^>]*id="read-article-holder"[^>]*>(.*?)</div>\s*<div[^>]*class="[^"]*bottom-bar',
            html, re.DOTALL
        )
        if not content:
            content = re.search(
                r'<div[^>]*class="[^"]*opus-module-content[^"]*"[^>]*>(.*?)</div>',
                html, re.DOTALL
            )
        text = _clean_html(content.group(1)) if content else ""
        return {
            **base_fields,
            "title": title,
            "author": author,
            "summary": text[:1200],
            "platform": "bilibili",
        }

    if "zhihu.com" in url:
        title = re.search(r'<h1[^>]*class="[^"]*QuestionHeader-title[^"]*"[^>]*>(.*?)</h1>', html)
        if not title:
            title = re.search(r'<h1[^>]*class="[^"]*Post-Title[^"]*"[^>]*>(.*?)</h1>', html)
        title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else "知乎文章"
        author = re.search(r'"author":"([^"]+)"', html) or re.search(
            r'<a[^>]*class="[^"]*AuthorInfo-name[^"]*"[^>]*>(.*?)</a>', html
        )
        author = re.sub(r'<[^>]+>', '', author.group(1)).strip() if author else "未知作者"
        content = re.search(
            r'<div[^>]*class="[^"]*RichContent-inner[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        if not content:
            content = re.search(
                r'<div[^>]*class="[^"]*Post-RichTextContainer[^"]*"[^>]*>(.*?)</div>',
                html, re.DOTALL
            )
        text = _clean_html(content.group(1)) if content else ""
        return {
            **base_fields,
            "title": title,
            "author": author,
            "summary": text[:1200],
            "platform": "zhihu",
        }

    if "mp.weixin.qq.com" in url:
        title = re.search(r'<h2[^>]*class="rich_media_title[^"]*"[^>]*>(.*?)</h2>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else "公众号文章"
        author = re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', html, re.DOTALL)
        author = re.sub(r'<[^>]+>', '', author.group(1)).strip() if author else "未知公众号"
        content = re.search(
            r'<div[^>]*class="rich_media_content[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
        text = _clean_html(content.group(1)) if content else ""
        return {
            **base_fields,
            "title": title,
            "author": author,
            "summary": text[:1200],
            "platform": "weixin",
        }

    return None


def _parse_generic(html: str) -> Optional[Dict[str, str]]:
    """通用解析。安全清理大HTML。"""
    if len(html) > 500_000:
        html = html[:500_000]

    text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<nav\b[^<]*(?:(?!</nav>)<[^<]*)*</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer\b[^<]*(?:(?!</footer>)<[^<]*)*</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<header\b[^<]*(?:(?!</header>)<[^<]*)*</header>', '', text, flags=re.DOTALL | re.IGNORECASE)

    article = re.search(r'<article[^>]*>(.*?)</article>', text, re.DOTALL)
    body = article.group(1) if article else text

    title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
    title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else "无标题"

    clean = _clean_html(body)
    if len(clean) < 200:
        body_match = re.search(r'<body[^>]*>(.*?)</body>', html, re.DOTALL)
        if body_match:
            clean = _clean_html(body_match.group(1))

    comments = ""
    comment_blocks = re.findall(
        r'<div[^>]*class="[^"]*comment[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL
    )
    if not comment_blocks:
        comment_blocks = re.findall(
            r'<div[^>]*class="[^"]*reply[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL
        )
    if comment_blocks:
        comments = "\n".join([_clean_html(m)[:200] for m in comment_blocks[:5]])

    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for script in scripts:
        if 'comment' in script.lower() or 'reply' in script.lower():
            json_matches = re.findall(r'({[\s\S]*?"content"[\s\S]*?})', script)
            if json_matches and not comments:
                try:
                    for jm in json_matches[:5]:
                        jd = json.loads(jm)
                        if isinstance(jd, dict) and "content" in jd:
                            comments += jd["content"] + "\n"
                except Exception:
                    pass
            break

    return {
        "title": title[:100],
        "author": "未知",
        "summary": clean[:1200],
        "comments": comments[:800],
        "cached": False,
        "restricted": False,
        "needs_paste": False,
        "platform": "generic",
    }


def _clean_html(html_fragment: str) -> str:
    """清洗 HTML 为纯文本。"""
    if not html_fragment:
        return ""
    text = html_fragment.replace('</p>', '\n').replace('</div>', '\n').replace('</br>', '\n').replace('<br>', '\n')
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&quot;', '"').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = re.sub(r'\n\s*\n', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = '\n'.join(line.strip() for line in text.split('\n') if line.strip())
    return text.strip()


# ==================== 分享缓存处理 ====================

def _cleanup_expired_shares(session_id: str):
    """清理单个 session 的过期分享。"""
    if session_id not in _recent_shares:
        return
    now = datetime.now().timestamp()
    valid = []
    seen_urls = set()
    for s in reversed(_recent_shares[session_id]):
        if now - s.get("time", 0) >= SHARE_TTL:
            continue
        url = s.get("url", s.get("source", ""))
        if url not in seen_urls:
            seen_urls.add(url)
            valid.append(s)
    valid = list(reversed(valid))[-5:]
    if valid:
        _recent_shares[session_id] = valid
    else:
        del _recent_shares[session_id]


async def global_cleanup_shares():
    """全局清理所有过期分享缓存（建议每小时调用一次）。"""
    now = datetime.now().timestamp()
    expired_sessions = []
    for sid, shares in list(_recent_shares.items()):
        valid = [s for s in shares if now - s.get("time", 0) < SHARE_TTL]
        if valid:
            _recent_shares[sid] = valid[-5:]
        else:
            expired_sessions.append(sid)
    for sid in expired_sessions:
        del _recent_shares[sid]
    # 清理全局 URL 冷却（LRU 会自动处理，但这里也清理过期的）
    expired_urls = [u for u, t in list(_url_fetch_cooldown.items()) if now - t > URL_FETCH_COOLDOWN_SECONDS * 2]
    for u in expired_urls:
        del _url_fetch_cooldown[u]
    if expired_sessions or expired_urls:
        print(f"[分享] 全局清理完成，释放 {len(expired_sessions)} 个 session, {len(expired_urls)} 个 URL 冷却")


async def extract_and_cache_shares(event, session_id: str) -> bool:
    """提取消息中的分享内容并缓存。修复了卡片重复添加问题。"""
    msg = event.get_message()
    shares = []
    seen_urls = set()

    for seg in msg:
        if seg.type == "text":
            text = seg.data.get("text", "")
            urls = re.findall(r'https?://[^\s\]\)]+', text)
            for url in urls:
                url = url.rstrip('.,;:!?)]}\'\"')
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                article = await fetch_url_content(url)
                if _is_valid_article(article):
                    display = f"{article.get('title', '无标题')} - {article.get('author', '未知')}"
                    shares.append({
                        "type": "网页",
                        "source": display,
                        "url": url,
                        "summary": article["summary"],
                        "comments": article.get("comments", ""),
                        "cached": article.get("cached", False),
                        "restricted": article.get("restricted", False),
                        "platform": article.get("platform", "unknown"),
                        "needs_paste": article.get("needs_paste", False),
                        "time": datetime.now().timestamp()
                    })
                else:
                    print(f"[分享] 链接内容无效或无法读取，跳过缓存: {url[:60]}")

        elif seg.type == "image":
            shares.append({
                "type": "图片",
                "source": seg.data.get("url") or seg.data.get("file", "未知图片"),
                "summary": "[图片内容暂无法直接识别]",
                "time": datetime.now().timestamp()
            })

        elif seg.type == "json":
            try:
                card = json.loads(seg.data.get("data", "{}"))
                card_added = False
                if "meta" in card:
                    for k, v in card["meta"].items():
                        if not isinstance(v, dict):
                            continue
                        card_url = v.get("jumpUrl") or v.get("url") or v.get("qqdocurl")
                        title = v.get("title", "分享卡片")
                        desc = v.get("desc", "") or v.get("description", "")
                        if card_url and card_url.startswith("http"):
                            if card_url in seen_urls:
                                card_added = True
                                break
                            seen_urls.add(card_url)
                            article = await fetch_url_content(card_url)
                            if _is_valid_article(article):
                                shares.append({
                                    "type": "网页",
                                    "source": f"{article.get('title', title)} - {article.get('author', '未知')}",
                                    "url": card_url,
                                    "summary": article["summary"],
                                    "comments": article.get("comments", ""),
                                    "cached": article.get("cached", False),
                                    "restricted": article.get("restricted", False),
                                    "platform": article.get("platform", "unknown"),
                                    "needs_paste": article.get("needs_paste", False),
                                    "time": datetime.now().timestamp()
                                })
                                card_added = True
                                break
                            else:
                                print(f"[分享] 卡片URL抓取无效: {card_url[:60]}")
                                shares.append({
                                    "type": "分享卡片",
                                    "source": title,
                                    "summary": f"{desc} {card_url}".strip()[:500],
                                    "time": datetime.now().timestamp()
                                })
                                card_added = True
                                break
                        else:
                            shares.append({
                                "type": "分享卡片",
                                "source": title,
                                "summary": desc[:500],
                                "time": datetime.now().timestamp()
                            })
                            card_added = True
                            break
                if not card_added and "prompt" in card:
                    shares.append({
                        "type": "分享卡片",
                        "source": card.get("prompt", "分享"),
                        "summary": str(card)[:500],
                        "time": datetime.now().timestamp()
                    })
            except Exception as e:
                print(f"[分享] JSON卡片解析失败: {e}")

        elif seg.type == "xml":
            raw = seg.data.get("data", "")
            shares.append({
                "type": "分享消息",
                "source": "XML消息",
                "summary": raw[:300],
                "time": datetime.now().timestamp()
            })

        elif seg.type == "file":
            shares.append({
                "type": "文件",
                "source": seg.data.get("name", "未知文件"),
                "summary": f"大小: {seg.data.get('size', '?')} 字节",
                "time": datetime.now().timestamp()
            })

    if shares:
        if session_id not in _recent_shares:
            _recent_shares[session_id] = []
        _recent_shares[session_id].extend(shares)
        _cleanup_expired_shares(session_id)
        print(f"[分享] 缓存了 {len(shares)} 条内容: {[s['type'] for s in shares]}")
        return True
    return False


def format_shares_for_prompt(shares: List[Dict[str, Any]], user_msg: str = "") -> str:
    valid_shares = [s for s in shares if _is_valid_share(s)]
    if not valid_shares:
        return ""
    multi_keywords = ["分别", "都讲", "全部", "每个", "这些", "讲讲"]
    want_multi = any(kw in user_msg for kw in multi_keywords)
    target = valid_shares[-3:] if want_multi else valid_shares[-1:]
    lines = ["【用户最近分享的外部内容】"]
    for i, s in enumerate(target, 1):
        lines.append(f"{i}. [{s['type']}] {s['source']}")
        if s.get('summary'):
            lines.append(f"   摘要: {s['summary'][:400]}")
        if s.get('needs_paste') and s.get('platform') == '小黑盒':
            lines.append(f"   ⚠️ 小黑盒的内容网页端无法自动读取。请用户把正文复制粘贴过来，我再帮你分析~")
        elif s.get('restricted'):
            lines.append(f"   ⚠️ 该内容来自{s.get('platform', '第三方平台')}，网页端无法获取完整正文，需要登录APP查看。请基于标题和自身知识回答，不要编造正文细节。")
        if s.get('comments'):
            lines.append(f"   热评: {s['comments'][:300]}")
    lines.append("注意：如果用户接下来的问题与上述内容明显相关，请基于这些内容结合上下文回答；如果不相关，请正常聊天，不必刻意提及。")
    return "\n".join(lines)


# ==================== 专业分析模式 ====================

def _extract_keywords(text: str) -> set:
    stopwords = {
        "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
        "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
        "自己", "这", "那"
    }
    words = re.findall(r'[\u4e00-\u9fa5]{2,6}', text[:500])
    return set(w for w in words if w not in stopwords and len(w) > 1)


def _check_articles_related(shares: List[Dict[str, Any]]) -> bool:
    if len(shares) < 2:
        return False
    texts = [s.get("summary", "") for s in shares[-2:]]
    if len(texts) < 2 or not all(texts):
        return False
    kw1 = _extract_keywords(texts[0])
    kw2 = _extract_keywords(texts[1])
    if not kw1 or not kw2:
        return False
    intersection = kw1 & kw2
    return len(intersection) >= 3 or (len(intersection) / min(len(kw1), len(kw2))) > 0.15


def build_analysis_prompt(shares: List[Dict[str, Any]], user_question: str) -> str:
    valid_shares = [s for s in shares if _is_valid_share(s)]
    if not valid_shares:
        return ""

    comment_keywords = ["评论", "评论区", "留言", "网友", "热评", "高赞", "大家怎么说", "怎么看评论"]
    ask_comment = any(kw in user_question for kw in comment_keywords)

    needs_paste = any(s.get("needs_paste") and s.get("platform") == "小黑盒" for s in valid_shares)
    if needs_paste:
        return "[小黑盒内容需要用户粘贴正文后才能分析]"

    is_related = _check_articles_related(valid_shares)
    target_shares = valid_shares[-2:] if is_related else valid_shares[-1:]

    content_blocks = []
    has_restricted = False
    for i, s in enumerate(target_shares, 1):
        block = f"【内容{i}】类型：{s.get('type', '未知')} | 来源：{s.get('source', '未知')}"
        if s.get("restricted") and not s.get("needs_paste"):
            block += f"\n⚠️ 状态：该内容来自{s.get('platform', '第三方平台')}，网页端无法获取完整正文，仅有标题和描述。"
            block += f"\n标题描述：{s.get('summary', '')[:300]}"
            has_restricted = True
        else:
            block += f"\n摘要：{s.get('summary', '')[:600]}"
        if ask_comment and s.get("comments"):
            block += f"\n评论区精选：{s['comments'][:400]}"
        content_blocks.append(block)

    relation_hint = ""
    if is_related and len(target_shares) > 1:
        relation_hint = "\n注意：上述两篇内容有关联，请做对比分析或联动解读，指出它们的共同点和差异。"

    comment_hint = ""
    if ask_comment:
        comment_hint = "\n用户特别关注了评论区/网友观点，请结合上述摘要和评论区内容回答，如果评论内容不足，请诚实说明。"

    restricted_hint = ""
    if has_restricted:
        restricted_hint = "\n⚠️ 重要：部分内容因平台限制无法获取正文，请诚实告知用户'这个链接需要登录APP才能看完整内容'，然后基于标题和自身知识做简要回答，绝对不要编造正文细节。"

    prompt = f"""【分析任务】用户分享了 {len(target_shares)} 个内容，请基于以下材料回答用户问题。

{'\n\n'.join(content_blocks)}
{relation_hint}{comment_hint}{restricted_hint}

用户的问题：{user_question}

要求：
1. 先基于上述材料做客观、有条理的分析（分点或分段）
2. 分析要具体，引用材料中的细节和数据，不要泛泛而谈
3. {'如果有多篇内容，请做对比或联动分析，不要孤立看待每篇' if is_related else '聚焦核心论点，深入剖析'}
4. 分析完后，用你猫娘的语气做一句简短个性化点评（调侃、吐槽、认同都可以）
5. 整体语气仍然是你在聊天，但分析部分要专业、有信息量、有深度
6. 如果材料不足以下结论，请诚实说明，不要编造"""
    return prompt


def get_recent_shares(session_id: str) -> List[Dict[str, Any]]:
    """获取某个 session 的最近分享缓存。"""
    _cleanup_expired_shares(session_id)
    return _recent_shares.get(session_id, [])
