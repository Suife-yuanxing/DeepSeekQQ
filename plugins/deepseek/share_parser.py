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

from .config import SHARE_TTL, VOICE_DIR, URL_FETCH_COOLDOWN
from .database import get_article_cache, save_article_cache
from .api import get_http_session
from nonebot import logger

_recent_shares: Dict[str, List[Dict[str, Any]]] = {}
_QQ_FACE_MAP={"0":"微笑","1":"撇嘴","2":"色","3":"发呆","4":"得意","5":"流泪","6":"害羞","7":"闭嘴","8":"睡","9":"大哭","10":"尴尬","11":"发怒","12":"调皮","13":"呲牙","14":"惊讶","15":"难过","16":"酷","17":"冷汗","18":"抓狂","19":"吐","20":"偷笑","21":"愉快","22":"白眼","23":"傲慢","30":"奋斗","34":"晕","37":"骷髅","41":"爱情","44":"拥抱","53":"玫瑰","55":"爱心","56":"心碎","60":"赞","63":"胜利","73":"裂开","81":"委屈"}


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
URL_FETCH_COOLDOWN_SECONDS = URL_FETCH_COOLDOWN  # 5分钟内不重复抓取同一URL


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
        logger.warning(f"[分享] 抓取失败 {url[:60]}: {e}")
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

    if "douyin.com" in url or "v.douyin.com" in url:
        title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        if not title:
            title = re.search(r'"desc"\s*:\s*"([^"]{4,})"', html)
        if not title:
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else "抖音视频"

        desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        if not desc:
            desc = re.search(r'"desc"\s*:\s*"([^"]{4,})"', html)
        desc_text = desc.group(1).strip() if desc else ""

        author = re.search(r'"nickname"\s*:\s*"([^"]+)"', html)
        if not author:
            author = re.search(r'<meta[^>]*name="author"[^>]*content="([^"]*)"', html)
        author = author.group(1).strip() if author else "抖音用户"

        # 提取封面图
        image = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
        image_url = image.group(1) if image else ""

        # 提取视频时长
        duration = re.search(r'"duration"\s*:\s*(\d+)', html)
        duration_text = ""
        if duration:
            secs = int(duration.group(1))
            if secs > 60:
                duration_text = f"({secs // 60}分{secs % 60}秒)"
            else:
                duration_text = f"({secs}秒)"

        summary_parts = []
        if desc_text and desc_text != title:
            summary_parts.append(desc_text[:500])
        summary = " ".join(summary_parts) if summary_parts else title

        return {
            **base_fields,
            "title": title[:100],
            "author": author,
            "summary": f"[抖音视频{duration_text}] {summary}"[:800],
            "platform": "douyin",
            "image_url": image_url,
            "restricted": True,  # 视频内容无法文字提取
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
        logger.info(f"[分享] 全局清理完成，释放 {len(expired_sessions)} 个 session, {len(expired_urls)} 个 URL 冷却")


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
                    logger.warning(f"[分享] 链接内容无效或无法读取，跳过缓存: {url[:60]}")

        elif seg.type == "image":
            sub_type = seg.data.get("sub_type", 0)
            summary = seg.data.get("summary", "")
            if sub_type == 1 or "动画表情" in summary:
                emoji_desc = summary.replace("[动画表情]", "").strip()
                if not emoji_desc: emoji_desc = "一个表情"
                shares.append({"type": "表情", "source": f"用户发了{emoji_desc}", "summary": f"[用户发送了QQ表情：{emoji_desc}]", "time": datetime.now().timestamp()})
            else:
                img_url = seg.data.get("url") or seg.data.get("file", "未知图片")
                # 三层降级识别图片：视觉模型 → OCR → 占位
                img_desc = "[图片内容暂无法直接识别]"
                try:
                    from .vision import analyze_image
                    if img_url and img_url != "未知图片":
                        vision_result = await analyze_image(img_url, "请用中文简洁描述这张图片的主要内容，2-3句话")
                        if vision_result and vision_result != "[图片内容暂无法识别]" and vision_result != "[图片文件不存在]":
                            img_desc = f"[图片内容: {vision_result}]"
                except Exception as e:
                    logger.warning(f"[图片识别] 异常: {e}")
                shares.append({"type": "图片", "source": img_url, "summary": img_desc, "time": datetime.now().timestamp()})
        elif seg.type == "face":
            face_id = seg.data.get("id", "")
            face_text = _QQ_FACE_MAP.get(str(face_id), "表情")
            shares.append({"type": "表情", "source": f"用户发了QQ表情[{face_text}]", "summary": f"[用户发送了QQ内置表情：{face_text}]", "time": datetime.now().timestamp()})
        elif seg.type == "mface":
            ed = seg.data.get("summary", "") or seg.data.get("desc", "") or "表情"
            shares.append({"type": "表情", "source": f"用户发了商城表情[{ed}]", "summary": f"[用户发送了QQ商城表情：{ed}]", "time": datetime.now().timestamp()})


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
                                logger.warning(f"[分享] 卡片URL抓取无效: {card_url[:60]}")
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
                logger.warning(f"[分享] JSON卡片解析失败: {e}")

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
        logger.info(f"[分享] 缓存了 {len(shares)} 条内容: {[s['type'] for s in shares]}")
        return True
    return False


def get_recent_shares(session_id: str) -> List[Dict[str, Any]]:
    """获取某个 session 的最近分享缓存。"""
    _cleanup_expired_shares(session_id)
    return _recent_shares.get(session_id, [])
