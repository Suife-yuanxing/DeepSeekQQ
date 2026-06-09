"""分享内容抓取与缓存。

支持平台：B站、小红书、抖音、通用链接
功能：分享卡片去重、按平台解析、内存缓存 TTL 清理、全局 URL 抓取冷却
"""
import asyncio
import hashlib
import html as _html
import json
import re
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import unquote

import aiohttp
from nonebot import logger

from .api import get_http_session
from .config import SHARE_TTL
from .config import URL_FETCH_COOLDOWN
from .database import get_article_cache
from .database import save_article_cache
from .image_reply import IMAGE_TYPE_STICKER
from .image_reply import classify_image
from .utils import LRUDict
from .vision import analyze_image
from .vision import extract_vision_text
from .vision import recognize_sticker

_recent_shares: Dict[str, List[Dict[str, Any]]] = {}
_QQ_FACE_MAP={"0":"微笑","1":"撇嘴","2":"色","3":"发呆","4":"得意","5":"流泪","6":"害羞","7":"闭嘴","8":"睡","9":"大哭","10":"尴尬","11":"发怒","12":"调皮","13":"呲牙","14":"惊讶","15":"难过","16":"酷","17":"冷汗","18":"抓狂","19":"吐","20":"偷笑","21":"愉快","22":"白眼","23":"傲慢","24":"饥饿","25":"困","26":"惊恐","27":"流汗","28":"憨笑","29":"悠闲","30":"奋斗","31":"咒骂","32":"疑问","33":"嘘","34":"晕","35":"折磨","36":"衰","37":"骷髅","38":"敲打","39":"再见","40":"发抖","41":"爱情","42":"跳跳","43":"猪头","44":"拥抱","45":"蛋糕","46":"闪电","47":"炸弹","48":"刀","49":"足球","50":"便便","51":"咖啡","52":"饭","53":"玫瑰","54":"凋谢","55":"爱心","56":"心碎","57":"礼物","58":"太阳","59":"月亮","60":"赞","61":"踩","62":"握手","63":"胜利","64":"飞吻","65":"怄火","66":"西瓜","67":"冷酷","68":"色眯眯","69":"好怕怕","73":"裂开","75":"叹气","76":"戳一戳","77":"托腮","78":"歪嘴笑","79":"左看看","80":"右看看","81":"委屈","82":"裂开","96":"抱拳","97":"勾引","98":"拳头","99":"差劲","100":"爱你","101":"NO","102":"OK","103":"转圈","104":"挥手","105":"飞奔","106":"偷看","107":"吓","108":"委屈"}

# 使用 utils.LRUDict 实现 URL 抓取冷却（LRU + 容量上限防泄漏）
_url_fetch_cooldown: LRUDict = LRUDict(max_size=500)


# ==================== 内容有效性校验 ====================

_INVALID_MARKERS = [
    "页面框架", "内容被截断", "未登录", "登录后查看",
    "内容为空", "只有框架", "技术性参数", "shallowReactive"
]


def _is_valid_share(s: Dict[str, Any]) -> bool:
    """校验分享内容是否有效（统一入口，同时供外部模块调用）。

    校验逻辑：
    1. 无摘要 → 无效
    2. needs_paste / 小黑盒 → 有效（虽然抓不到内容，但有标题等基本信息）
    3. restricted → 有效（受限平台，有标题/描述即可）
    4. 摘要 < 80 字符 → 无效（内容太短，解析失败）
    5. 包含无效标记词 → 无效（只抓到了页面框架而非正文）
    """
    summary = s.get("summary", "")
    if not summary:
        return False
    # 小黑盒等需要用户粘贴正文的平台：即使抓不到内容也算有效
    if s.get("needs_paste") or s.get("platform") == "小黑盒":
        return True
    # 受限平台（抖音等）：有标题和描述就算有效
    if s.get("restricted"):
        return True
    # 通用校验：内容太短说明解析失败
    if len(summary.strip()) < 80:
        return False
    # 无效标记词表明只抓到了页面框架
    return not any(m in summary for m in _INVALID_MARKERS)


# ==================== 增强网页抓取 ====================

async def fetch_url_content(url: str) -> Optional[Dict[str, str]]:
    """增强版网页抓取，按平台解析，带缓存和全局冷却。"""
    now = datetime.now().timestamp()
    cache_key = hashlib.md5(url.encode()).hexdigest()

    last_fetch = _url_fetch_cooldown.get(url, 0)
    if now - last_fetch < URL_FETCH_COOLDOWN:
        cached = await get_article_cache(cache_key)
        if cached:
            return cached
        return None

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
    # 抖音需要 Referer 否则可能被反爬
    if "douyin.com" in url:
        headers["Referer"] = "https://www.douyin.com/"

    async def _do_fetch():
        session = await get_http_session()
        async with session.get(
            url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True
        ) as resp:
            if resp.status != 200:
                return None
            final_url = str(resp.url)
            html_content = await resp.text()
            if len(html_content) > 500_000:
                # 在最后一个完整标签处截断，避免在标签中间切断
                truncated = html_content[:500_000]
                last_gt = truncated.rfind('>')
                if last_gt > 0:
                    html = truncated[:last_gt + 1]
                else:
                    html = truncated
            else:
                html = html_content

            result = _parse_by_platform(html, final_url)
            if not result:
                result = _parse_generic(html)

            if result and result.get("summary") and not result.get("fetch_failed"):
                _url_fetch_cooldown[url] = now
                await save_article_cache(
                    cache_key, url,
                    result.get("title", ""),
                    result.get("author", ""),
                    result["summary"]
                )
            return result

    try:
        from .circuit_breaker import get_breaker
        breaker = get_breaker("share_fetch")
        if breaker:
            return await breaker.call(_do_fetch, fallback=lambda: None)
        return await _do_fetch()
    except Exception as e:
        logger.warning(f"[分享] 抓取失败 {url[:60]}: {e}")
        return None


def _strip_html(match: Optional[re.Match], fallback: str = "") -> str:
    """从正则匹配中提取文本并清除 HTML 标签。"""
    if not match:
        return fallback
    return re.sub(r'<[^>]+>', '', match.group(1)).strip()


def _extract_douyin_render_data(html: str) -> Optional[Dict[str, Any]]:
    """从抖音页面的 RENDER_DATA / __NEXT_DATA__ 中提取视频信息。

    抖音是 SPA 页面，视频数据不在 meta 标签中，而在 <script> 标签的 JSON 里：
    - <script id="RENDER_DATA" type="application/json">URL_ENCODED_JSON</script>
    - <script id="__NEXT_DATA__" type="application/json">JSON</script>

    返回 {desc, nickname, duration, cover_url, comment_count, digg_count, music_title} 或 None。
    """
    render_data = None

    # 方式1: RENDER_DATA（URL编码的JSON）
    match = re.search(
        r'<script[^>]*id="RENDER_DATA"[^>]*type="application/json"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if match:
        try:
            decoded = unquote(match.group(1).strip())
            render_data = json.loads(decoded)
        except Exception:
            pass

    # 方式2: __NEXT_DATA__（Next.js SSR）
    if not render_data:
        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if match:
            try:
                render_data = json.loads(match.group(1))
            except Exception:
                pass

    if not render_data:
        return None

    # 尝试从多种已知 JSON 路径定位 aweme 对象
    aweme = None
    for path in [
        ["aweme", "detail", "aweme"],                      # 经典结构
        ["common", "aweme", "detail", "aweme"],             # 新版通用结构
        ["app", "videoInfoRes", "item_list"],               # 另一变体 (取数组首项)
    ]:
        node = render_data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if isinstance(node, dict):
            aweme = node
            break
        elif isinstance(node, list) and node and isinstance(node[0], dict):
            aweme = node[0]
            break

    if not aweme:
        return None

    info: Dict[str, Any] = {}

    if aweme.get("desc"):
        info["desc"] = str(aweme["desc"])

    author = aweme.get("author", {})
    if isinstance(author, dict):
        info["nickname"] = author.get("nickname", "")
        avatar = author.get("avatar_thumb", {})
        if isinstance(avatar, dict):
            urls = avatar.get("url_list", [])
            if urls:
                info["avatar_url"] = str(urls[0])

    video = aweme.get("video", {})
    if isinstance(video, dict):
        info["duration"] = int(video.get("duration", 0) or 0)
        cover = video.get("cover", {}) or video.get("origin_cover", {})
        if isinstance(cover, dict):
            urls = cover.get("url_list", [])
            if urls:
                info["cover_url"] = str(urls[0])

    stats = aweme.get("statistics", {})
    if isinstance(stats, dict):
        info["comment_count"] = int(stats.get("comment_count", 0) or 0)
        info["digg_count"] = int(stats.get("digg_count", 0) or 0)

    music = aweme.get("music", {})
    if isinstance(music, dict) and music.get("title"):
        info["music_title"] = str(music["title"])

    return info if info else None


def _extract_bilibili_video_data(html: str, url: str = "") -> Optional[Dict[str, Any]]:
    """从B站视频页面提取结构化数据。

    优先解析 window.__INITIAL_STATE__ 中的 videoData，
    回退到 og meta 标签。
    """
    info: Dict[str, Any] = {}

    # ── Path 1: window.__INITIAL_STATE__ ──
    pos = html.find("window.__INITIAL_STATE__")
    if pos >= 0:
        eq_pos = html.find("=", pos)
        if eq_pos >= 0:
            brace_pos = html.find("{", eq_pos)
            if brace_pos >= 0:
                depth = 0
                i = brace_pos
                while i < len(html):
                    ch = html[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                if depth == 0:
                    try:
                        data = json.loads(html[brace_pos : i + 1])
                    except (json.JSONDecodeError, TypeError):
                        data = {}
                    vd = data.get("videoData") if isinstance(data, dict) else None
                    if isinstance(vd, dict):
                        info["desc"] = str(vd.get("title", "") or "")
                        info["desc_long"] = str(vd.get("desc", "") or "")
                        owner = vd.get("owner", {})
                        if isinstance(owner, dict):
                            info["nickname"] = str(owner.get("name", "") or "")
                        info["duration"] = int(vd.get("duration", 0) or 0)
                        info["cover_url"] = str(vd.get("pic", "") or "")
                        stat = vd.get("stat", {})
                        if isinstance(stat, dict):
                            info["view_count"] = int(stat.get("view", 0) or 0)
                            info["digg_count"] = int(stat.get("like", 0) or 0)
                            info["comment_count"] = int(stat.get("reply", 0) or 0)
                            info["danmaku_count"] = int(stat.get("danmaku", 0) or 0)
                            info["favorite_count"] = int(stat.get("favorite", 0) or 0)
                        info["pubdate"] = vd.get("pubdate", 0)
                        if info.get("desc"):
                            return info

    # ── Path 2: meta 标签回退 ──
    title_m = re.search(
        r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html
    )
    desc_m = re.search(
        r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html
    )
    image_m = re.search(
        r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html
    )

    if title_m:
        info["desc"] = title_m.group(1).strip()
        if desc_m:
            info["desc_long"] = desc_m.group(1).strip()
        if image_m:
            info["cover_url"] = image_m.group(1).strip()
        return info if info else None

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
        # ── 首选：从 RENDER_DATA / __NEXT_DATA__ 中提取结构化数据 ──
        render_info = _extract_douyin_render_data(html)

        if render_info and render_info.get("desc"):
            desc_text = render_info.get("desc", "")
            nickname = render_info.get("nickname", "抖音用户")
            duration = render_info.get("duration", 0)
            cover_url = render_info.get("cover_url", "")
            digg = render_info.get("digg_count", 0)
            comment_cnt = render_info.get("comment_count", 0)
            music_title = render_info.get("music_title", "")

            # 时长格式化
            duration_text = ""
            if duration:
                if duration >= 60:
                    duration_text = f"({duration // 60}分{duration % 60}秒)"
                else:
                    duration_text = f"({duration}秒)"

            # 互动数据
            stats_parts = []
            if digg:
                stats_parts.append(f"{digg}点赞")
            if comment_cnt:
                stats_parts.append(f"{comment_cnt}评论")
            stats_text = f" [{', '.join(stats_parts)}]" if stats_parts else ""

            summary = f"[抖音视频{duration_text}{stats_text}] {desc_text[:400]}"
            if music_title:
                summary += f" | 🎵{music_title}"

            return {
                **base_fields,
                "title": desc_text[:100] if desc_text else "抖音视频",
                "author": nickname,
                "summary": summary[:800],
                "platform": "douyin",
                "image_url": cover_url,
                "restricted": True,
            }

        # ── 回退：从 meta 标签中提取 ──
        title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        if not title:
            # 仅在 script 标签内搜索 JSON 字段，避免匹配到无关内容
            scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
            for script in scripts:
                m = re.search(r'"desc"\s*:\s*"([^"]{4,})"', script)
                if m:
                    title = m
                    break
        if not title:
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        title_text = _strip_html(title, "")

        desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        desc_text = desc.group(1).strip() if desc else ""

        author = None
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        for script in scripts:
            m = re.search(r'"nickname"\s*:\s*"([^"]+)"', script)
            if m:
                author = m.group(1).strip()
                break
        if not author:
            author = re.search(r'<meta[^>]*name="author"[^>]*content="([^"]*)"', html)
            author = author.group(1).strip() if author else "抖音用户"

        image = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
        image_url = image.group(1) if image else ""

        # 检查是否成功提取到有效内容
        has_valid_title = title_text and title_text not in ("抖音", "抖音视频", "抖音-记录美好生活") and len(title_text) > 2
        has_valid_desc = desc_text and len(desc_text) > 5

        if has_valid_title or has_valid_desc:
            summary_parts = []
            if has_valid_desc and desc_text != title_text:
                summary_parts.append(desc_text[:500])
            summary = " ".join(summary_parts) if summary_parts else title_text
            return {
                **base_fields,
                "title": title_text[:100] if has_valid_title else "抖音视频",
                "author": author,
                "summary": f"[抖音视频] {summary}"[:800],
                "platform": "douyin",
                "image_url": image_url,
                "restricted": True,
            }
        else:
            return {
                **base_fields,
                "title": "抖音视频",
                "author": author,
                "summary": "[抖音视频链接，内容无法读取]",
                "platform": "douyin",
                "image_url": image_url,
                "restricted": True,
                "fetch_failed": True,
            }

    if "xiaoheike" in url or "xiaoheihe" in url or "xiaoheih" in url:
        title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        if not title:
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        title = _strip_html(title, "小黑盒分享")
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

    if "bilibili.com/video" in url or "b23.tv" in url:
        # ── 首选：从 window.__INITIAL_STATE__ 提取结构化数据 ──
        render_info = _extract_bilibili_video_data(html, url)

        if render_info and render_info.get("desc"):
            desc_text = render_info.get("desc", "")
            nickname = render_info.get("nickname", "B站UP主")
            duration = render_info.get("duration", 0)
            cover_url = render_info.get("cover_url", "")
            view_count = render_info.get("view_count", 0)
            comment_cnt = render_info.get("comment_count", 0)
            digg_cnt = render_info.get("digg_count", 0)
            danmaku_cnt = render_info.get("danmaku_count", 0)
            desc_long = render_info.get("desc_long", "")

            # 时长格式化
            duration_text = ""
            if duration:
                if duration >= 60:
                    duration_text = f"({duration // 60}分{duration % 60}秒)"
                else:
                    duration_text = f"({duration}秒)"

            # 互动数据
            stats_parts = []
            if view_count:
                stats_parts.append(f"{view_count}播放")
            if digg_cnt:
                stats_parts.append(f"{digg_cnt}点赞")
            if comment_cnt:
                stats_parts.append(f"{comment_cnt}评论")
            if danmaku_cnt:
                stats_parts.append(f"{danmaku_cnt}弹幕")
            stats_text = f" [{', '.join(stats_parts)}]" if stats_parts else ""

            summary = f"[B站视频{duration_text}{stats_text}] {desc_text[:400]}"
            if desc_long and desc_long != desc_text:
                summary += f" | {desc_long[:300]}"

            return {
                **base_fields,
                "title": desc_text[:100] if desc_text else "B站视频",
                "author": nickname,
                "summary": summary[:800],
                "platform": "bilibili",
                "image_url": cover_url,
                "restricted": True,
            }

        # ── 回退：从 meta 标签提取 ──
        title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
        if not title:
            title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
        title_text = _strip_html(title, "")

        desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
        desc_text = desc.group(1).strip() if desc else ""

        image = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
        image_url = image.group(1) if image else ""

        # 检查是否成功提取到有效内容
        has_valid_title = (
            title_text and len(title_text) > 2
            and "bilibili" not in title_text.lower()
        )
        has_valid_desc = desc_text and len(desc_text) > 5

        if has_valid_title or has_valid_desc:
            summary_parts = []
            if has_valid_desc and desc_text != title_text:
                summary_parts.append(desc_text[:500])
            summary = " ".join(summary_parts) if summary_parts else title_text
            return {
                **base_fields,
                "title": title_text[:100] if has_valid_title else "B站视频",
                "author": "B站UP主",
                "summary": f"[B站视频] {summary}"[:800],
                "platform": "bilibili",
                "image_url": image_url,
                "restricted": True,
            }
        else:
            return {
                **base_fields,
                "title": "B站视频",
                "author": "B站UP主",
                "summary": "[B站视频链接，内容无法读取]",
                "platform": "bilibili",
                "image_url": image_url,
                "restricted": True,
                "fetch_failed": True,
            }

    if "bilibili.com/read" in url or "bilibili.com/opus" in url:
        title = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html)
        title = _strip_html(title, "B站专栏")
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
        title = _strip_html(title, "知乎文章")
        author = re.search(r'"author":"([^"]+)"', html) or re.search(
            r'<a[^>]*class="[^"]*AuthorInfo-name[^"]*"[^>]*>(.*?)</a>', html
        )
        author = _strip_html(author, "未知作者")
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
        title = _strip_html(title, "公众号文章")
        author = re.search(r'<a[^>]*id="js_name"[^>]*>(.*?)</a>', html, re.DOTALL)
        author = _strip_html(author, "未知公众号")
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
        # 在最后一个完整标签处截断，避免在标签中间切断
        truncated = html[:500_000]
        last_gt = truncated.rfind('>')
        html = truncated[:last_gt + 1] if last_gt > 0 else truncated

    text = re.sub(r'<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<nav\b[^<]*(?:(?!</nav>)<[^<]*)*</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer\b[^<]*(?:(?!</footer>)<[^<]*)*</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<header\b[^<]*(?:(?!</header>)<[^<]*)*</header>', '', text, flags=re.DOTALL | re.IGNORECASE)

    article = re.search(r'<article[^>]*>(.*?)</article>', text, re.DOTALL)
    body = article.group(1) if article else text

    title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
    title = _strip_html(title, "无标题")

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
    # 使用 stdlib html.unescape 解码所有 HTML 实体（含 &#...; / &#x...; / 命名实体）
    text = _html.unescape(text)
    text = text.replace('\xa0', ' ')  # nbsp → 普通空格
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
    # 复用单 session 清理逻辑
    before = len(_recent_shares)
    for sid in list(_recent_shares.keys()):
        _cleanup_expired_shares(sid)
    freed_sessions = before - len(_recent_shares)

    # 容量保护：session 数超过 500 时清理最旧的
    if len(_recent_shares) > 500:
        sorted_sessions = sorted(
            _recent_shares.items(),
            key=lambda x: max((s.get("timestamp", 0) for s in x[1]), default=0)
        )
        for sid, _ in sorted_sessions[:len(sorted_sessions) // 2]:
            del _recent_shares[sid]
    # 清理全局 URL 冷却
    expired_urls = [u for u, t in list(_url_fetch_cooldown.items()) if now - t > URL_FETCH_COOLDOWN * 2]
    for u in expired_urls:
        del _url_fetch_cooldown[u]
    if freed_sessions or expired_urls:
        logger.info(f"[分享] 全局清理完成，释放 {freed_sessions} 个 session, {len(expired_urls)} 个 URL 冷却")


# ==================== 消息段处理器（从 extract_and_cache_shares 拆分） ====================

def _clean_url(url: str) -> str:
    """清理 URL 末尾的标点符号。

    中文标点直接剥离；英文括号 )]} 仅在 URL 内部不平衡时才剥离，
    避免截断维基百科等含括号的合法链接。

    >>> _clean_url('https://en.wikipedia.org/wiki/C_(programming_language)。')
    'https://en.wikipedia.org/wiki/C_(programming_language)'
    >>> _clean_url('https://example.com/page)')
    'https://example.com/page'
    """
    # 中文标点：几乎不可能出现在合法 URL 末尾
    url = url.rstrip('。，！？；：、\'"”）】》．')
    # 成对标点：仅剥离不平衡的 } ] )
    for close_char, open_char in [(')', '('), (']', '['), ('}', '{')]:
        while url.endswith(close_char) and url.count(open_char) < url.count(close_char):
            url = url[:-1]
    # 其他常见聊天中的尾随标点
    url = url.rstrip('\'";')
    return url


async def _handle_text_segment(seg, seen_urls: set) -> List[Dict[str, Any]]:
    """处理文本消息段，提取 URL 并抓取内容。"""
    results = []
    text = seg.data.get("text", "")
    urls = re.findall(r'https?://\S+', text)
    for url in urls:
        url = _clean_url(url)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        article = await fetch_url_content(url)
        if _is_valid_share(article):
            display = f"{article.get('title', '无标题')} - {article.get('author', '未知')}"
            results.append({
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
    return results


async def _handle_sticker_segment(seg) -> Dict[str, Any]:
    """处理表情包图片段（sub_type=1 或 QQ 动画表情）。"""
    img_url = seg.data.get("url", "") or seg.data.get("file", "")
    summary = seg.data.get("summary", "")
    emoji_desc = summary.replace("[动画表情]", "").strip()
    if not emoji_desc or emoji_desc == "[图片]":
        emoji_desc = ""

    if emoji_desc:
        return {"type": "表情", "source": f"用户发了表情[{emoji_desc}]",
                "summary": f"[用户发送了QQ表情：{emoji_desc}]", "image_url": img_url,
                "time": datetime.now().timestamp()}

    # 尝试视觉识别表情情绪
    emotion = await recognize_sticker(img_url) if img_url else None
    if emotion:
        return {"type": "表情", "source": f"用户发了一个表情[{emotion}]",
                "summary": f"[用户发送了QQ表情：{emotion}]", "image_url": img_url,
                "time": datetime.now().timestamp()}
    return {"type": "表情", "source": "用户发了一个表情",
            "summary": "[用户发送了一个QQ表情图片，无法确定具体内容]", "image_url": img_url,
            "time": datetime.now().timestamp()}


async def _handle_photo_segment(seg, user_text: str) -> Dict[str, Any]:
    """处理普通图片段（含三层降级识别 + 重试机制）。"""
    img_url = seg.data.get("url") or seg.data.get("file", "未知图片")

    # 动态选择识别提示词
    img_prompt = _select_image_prompt(user_text)

    img_desc = "[图片内容暂无法直接识别]"
    vision_success = False
    try:
        if img_url and img_url != "未知图片":
            for retry in range(3):
                vision_result = await analyze_image(img_url, img_prompt)
                vision_text = extract_vision_text(vision_result)
                if vision_text:
                    img_desc = vision_result  # 保留完整格式（含 OCR 前缀）
                    vision_success = True
                    break
                elif retry < 2:
                    logger.info(f"[图片识别] 重试 {retry + 1}/2: {img_url[:50]}")
                    await asyncio.sleep(1)
    except Exception as e:
        logger.warning(f"[图片识别] 异常: {e}")

    if not vision_success:
        logger.warning(f"[图片识别] 最终失败: {img_url[:50]}")

    # 图片分类（使用安全的 extract_vision_text）
    vision_text = extract_vision_text(img_desc)
    image_type = classify_image(vision_text, user_text)
    logger.info(f"[图片分类] {image_type} | {vision_text[:50] if vision_text else '(无描述)'}...")

    return {
        "type": "图片",
        "source": img_url,
        "summary": img_desc,
        "image_type": image_type,
        "vision_text": vision_text,
        "time": datetime.now().timestamp()
    }


def _select_image_prompt(user_text: str) -> str:
    """根据用户消息上下文选择合适的图片识别提示词。"""
    if user_text and any(kw in user_text for kw in ["截图", "聊天记录", "对话", "屏幕"]):
        return "这是一张截图，请重点识别其中的文字内容和界面元素，用中文简洁描述，2-3句话"
    if user_text and any(kw in user_text for kw in ["表情包", "表情", "搞笑", "斗图"]):
        return "这是一个表情包，请描述其中的人物表情、动作和文字，用中文简洁描述，2句话"
    if user_text and any(kw in user_text for kw in ["这是什么", "看看", "帮我", "识别", "什么"]):
        return "请详细描述这张图片的内容，包括主要物体、场景、文字等，用中文回答，3-4句话"
    return "请用中文简洁描述这张图片的主要内容，2-3句话，如果有文字请提取出来。特别注意：如果图片中有人物、动物、食物、风景、代码、聊天记录、文档等，请明确指出类型。"


def _handle_face_segment(seg) -> Dict[str, Any]:
    """处理 QQ 表情消息段。"""
    face_id = str(seg.data.get("id", seg.data.get("faceIndex", "")))
    face_text = seg.data.get("text", "") or seg.data.get("faceText", "")
    if not face_text:
        raw = seg.data.get("raw", {})
        if isinstance(raw, dict):
            face_text = raw.get("faceText", "")
    if face_text:
        face_text = face_text.strip().lstrip("/")
    if not face_text:
        face_text = _QQ_FACE_MAP.get(face_id, "")
    if not face_text:
        face_text = "表情"
    return {"type": "表情", "source": f"用户发了QQ表情[{face_text}]",
            "summary": f"[用户发送了QQ表情：{face_text}]", "time": datetime.now().timestamp()}


async def _handle_json_card(seg, seen_urls: set) -> List[Dict[str, Any]]:
    """处理 JSON 分享卡片消息段。"""
    results = []
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
                    if _is_valid_share(article):
                        results.append({
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
                        results.append({
                            "type": "分享卡片", "source": title,
                            "summary": f"{desc} {card_url}".strip()[:500],
                            "time": datetime.now().timestamp()
                        })
                        card_added = True
                        break
                else:
                    results.append({
                        "type": "分享卡片", "source": title,
                        "summary": desc[:500], "time": datetime.now().timestamp()
                    })
                    card_added = True
                    break
        if not card_added and "prompt" in card:
            results.append({
                "type": "分享卡片",
                "source": card.get("prompt", "分享"),
                "summary": str(card)[:500],
                "time": datetime.now().timestamp()
            })
    except Exception as e:
        logger.warning(f"[分享] JSON卡片解析失败: {e}")
    return results


# ==================== 主入口 ====================

async def extract_and_cache_shares(event, session_id: str) -> bool:
    """提取消息中的分享内容并缓存。

    按消息段类型分发处理：
    - text → URL 抓取
    - image → 贴纸/图片识别
    - face/mface → QQ 表情映射
    - json → 分享卡片解析
    - xml → 原始 XML
    - file → 文件元信息
    """
    msg = event.get_message()
    user_text = msg.extract_plain_text().strip() if msg else ""
    shares = []
    seen_urls = set()

    for seg in msg:
        if seg.type == "text":
            shares.extend(await _handle_text_segment(seg, seen_urls))

        elif seg.type == "image":
            sub_type = seg.data.get("sub_type", 0)
            summary = seg.data.get("summary", "")
            if sub_type == 1 or "动画表情" in summary or sub_type == 13:
                shares.append(await _handle_sticker_segment(seg))
            else:
                shares.append(await _handle_photo_segment(seg, user_text))

        elif seg.type == "face":
            shares.append(_handle_face_segment(seg))

        elif seg.type == "mface":
            ed = seg.data.get("summary", "") or seg.data.get("desc", "") or "表情"
            shares.append({"type": "表情", "source": f"用户发了商城表情[{ed}]",
                          "summary": f"[用户发送了QQ商城表情：{ed}]",
                          "time": datetime.now().timestamp()})

        elif seg.type == "json":
            shares.extend(await _handle_json_card(seg, seen_urls))

        elif seg.type == "xml":
            raw = seg.data.get("data", "")
            shares.append({
                "type": "分享消息", "source": "XML消息",
                "summary": raw[:300], "time": datetime.now().timestamp()
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
