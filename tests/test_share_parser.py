"""测试 share_parser.py — 分享内容抓取与缓存（纯逻辑函数）。"""
import pytest
import sys
import types
import re
from unittest.mock import AsyncMock, MagicMock, patch
from collections import OrderedDict


# ---- Mock 依赖 ----
mock_aiohttp = types.ModuleType("aiohttp")
mock_aiohttp.ClientTimeout = lambda total: total
sys.modules["aiohttp"] = mock_aiohttp

if "plugins.deepseek.config" not in sys.modules:
    sys.modules["plugins.deepseek.config"] = types.SimpleNamespace(
        SHARE_TTL=1800, URL_FETCH_COOLDOWN=300,
    )
if "plugins.deepseek.api" not in sys.modules:
    mock_api = types.ModuleType("plugins.deepseek.api")
    mock_api.get_http_session = AsyncMock(return_value=AsyncMock())
    sys.modules["plugins.deepseek.api"] = mock_api
if "plugins.deepseek.database" not in sys.modules:
    mock_db = types.ModuleType("plugins.deepseek.database")
    mock_db.get_article_cache = AsyncMock(return_value=None)
    mock_db.save_article_cache = AsyncMock(return_value=None)
    sys.modules["plugins.deepseek.database"] = mock_db
if "plugins.deepseek.vision" not in sys.modules:
    mock_vision = types.ModuleType("plugins.deepseek.vision")
    mock_vision.recognize_sticker = AsyncMock(return_value=None)
    mock_vision.analyze_image = AsyncMock(return_value="[图片内容: 一只猫]")
    mock_vision.extract_vision_text = lambda x: x.replace("[图片内容: ", "").replace("]", "") if x and x.startswith("[图片内容:") else (x or "")
    sys.modules["plugins.deepseek.vision"] = mock_vision
if "plugins.deepseek.image_reply" not in sys.modules:
    mock_reply = types.ModuleType("plugins.deepseek.image_reply")
    mock_reply.classify_image = MagicMock(return_value="photo_pet")
    mock_reply.IMAGE_TYPE_STICKER = "sticker"
    sys.modules["plugins.deepseek.image_reply"] = mock_reply
if "plugins.deepseek.utils" not in sys.modules:
    mock_utils = types.ModuleType("plugins.deepseek.utils")
    class _LRUDict(OrderedDict):
        def __init__(self, max_size=500):
            super().__init__()
            self.max_size = max_size
        def __setitem__(self, key, value):
            if key in self:
                self.move_to_end(key)
            else:
                while len(self) >= self.max_size:
                    oldest = next(iter(self))
                    del self[oldest]
            super().__setitem__(key, value)
    mock_utils.LRUDict = _LRUDict
    sys.modules["plugins.deepseek.utils"] = mock_utils


class TestIsValidShare:
    """测试 _is_valid_share 校验逻辑。"""

    def test_no_summary(self):
        from plugins.deepseek.share_parser import _is_valid_share
        assert _is_valid_share({"summary": ""}) is False

    def test_needs_paste_overrides_length(self):
        from plugins.deepseek.share_parser import _is_valid_share
        assert _is_valid_share({
            "summary": "short", "needs_paste": True, "platform": "小黑盒",
        }) is True

    def test_restricted_overrides_length(self):
        from plugins.deepseek.share_parser import _is_valid_share
        assert _is_valid_share({"summary": "short", "restricted": True}) is True

    def test_short_summary_invalid(self):
        from plugins.deepseek.share_parser import _is_valid_share
        assert _is_valid_share({"summary": "太短了"}) is False

    def test_long_enough_summary_valid(self):
        from plugins.deepseek.share_parser import _is_valid_share
        long_summary = "这是一段足够长的摘要内容，" * 10
        assert _is_valid_share({"summary": long_summary}) is True

    def test_invalid_marker_detected(self):
        from plugins.deepseek.share_parser import _is_valid_share
        long_but_invalid = "页面框架" + "x" * 80
        assert _is_valid_share({"summary": long_but_invalid}) is False

    def test_restricted_short_content_valid(self):
        from plugins.deepseek.share_parser import _is_valid_share
        assert _is_valid_share({
            "summary": "抖音视频标题", "restricted": True, "platform": "douyin",
        }) is True


class TestCleanHtml:
    """测试 _clean_html 清洗函数。"""

    def test_removes_tags(self):
        from plugins.deepseek.share_parser import _clean_html
        result = _clean_html("<p>Hello <b>World</b></p>")
        assert "Hello" in result
        assert "World" in result

    def test_handles_entities(self):
        from plugins.deepseek.share_parser import _clean_html
        result = _clean_html("Hello&nbsp;World &amp; Universe")
        assert "Hello World" in result

    def test_empty_input(self):
        from plugins.deepseek.share_parser import _clean_html
        assert _clean_html("") == ""

    def test_none_input(self):
        from plugins.deepseek.share_parser import _clean_html
        assert _clean_html(None) == ""

    def test_br_to_newline(self):
        from plugins.deepseek.share_parser import _clean_html
        result = _clean_html("Line1<br>Line2</br>Line3")
        assert "Line1" in result
        assert "Line2" in result


class TestStripHtml:
    """测试 _strip_html 辅助函数。"""

    def test_extracts_from_match(self):
        from plugins.deepseek.share_parser import _strip_html
        match = re.search(r'<title>(.*?)</title>', "<title>My Page</title>")
        assert _strip_html(match) == "My Page"

    def test_no_match_returns_fallback(self):
        from plugins.deepseek.share_parser import _strip_html
        assert _strip_html(None, "default") == "default"
        assert _strip_html(None, "") == ""


class TestSelectImagePrompt:
    """测试 _select_image_prompt 动态提示词选择。"""

    def test_screenshot_context(self):
        from plugins.deepseek.share_parser import _select_image_prompt
        prompt = _select_image_prompt("看看这个聊天记录")
        assert "截图" in prompt

    def test_sticker_context(self):
        from plugins.deepseek.share_parser import _select_image_prompt
        prompt = _select_image_prompt("这个表情包好好笑")
        assert "表情包" in prompt

    def test_analyze_context(self):
        from plugins.deepseek.share_parser import _select_image_prompt
        prompt = _select_image_prompt("帮我看看这是什么")
        assert "详细描述" in prompt

    def test_default_context(self):
        from plugins.deepseek.share_parser import _select_image_prompt
        prompt = _select_image_prompt("")
        assert "明确指出类型" in prompt


class TestRecentShares:
    """测试分享缓存管理。"""

    def test_get_recent_shares_empty(self):
        from plugins.deepseek.share_parser import get_recent_shares
        shares = get_recent_shares("nonexistent_session")
        assert shares == []


class TestFaceMap:
    """测试 QQ 表情映射。"""

    def test_face_map_has_common_faces(self):
        from plugins.deepseek.share_parser import _QQ_FACE_MAP
        assert _QQ_FACE_MAP["0"] == "微笑"
        assert _QQ_FACE_MAP["14"] == "惊讶"
        assert _QQ_FACE_MAP["66"] == "西瓜"

    def test_handle_face_segment(self):
        from plugins.deepseek.share_parser import _handle_face_segment
        seg = MagicMock()
        seg.data = {"id": "14"}
        result = _handle_face_segment(seg)
        assert result["type"] == "表情"
        assert "惊讶" in result["summary"]


class TestParseByPlatform:
    """测试 _parse_by_platform 平台识别。"""

    def test_douyin_url(self):
        from plugins.deepseek.share_parser import _parse_by_platform
        html = '<html><head><title>测试抖音</title></head><body></body></html>'
        result = _parse_by_platform(html, "https://www.douyin.com/video/123")
        assert result is not None
        assert result["platform"] == "douyin"

    def test_bilibili_read_url(self):
        from plugins.deepseek.share_parser import _parse_by_platform
        html = '<html><head><title>B站专栏</title></head><body></body></html>'
        result = _parse_by_platform(html, "https://www.bilibili.com/read/cv12345")
        assert result is not None
        assert result["platform"] == "bilibili"

    def test_zhihu_url(self):
        from plugins.deepseek.share_parser import _parse_by_platform
        html = '<html><head><title>知乎问题</title></head><body></body></html>'
        result = _parse_by_platform(html, "https://www.zhihu.com/question/12345")
        assert result is not None
        assert result["platform"] == "zhihu"

    def test_weixin_url(self):
        from plugins.deepseek.share_parser import _parse_by_platform
        html = '<html><head><title>公众号</title></head><body></body></html>'
        result = _parse_by_platform(html, "https://mp.weixin.qq.com/s/abc123")
        assert result is not None
        assert result["platform"] == "weixin"

    def test_xiaoheihe_url(self):
        from plugins.deepseek.share_parser import _parse_by_platform
        html = '<html><head><title>小黑盒</title></head><body></body></html>'
        result = _parse_by_platform(html, "https://www.xiaoheihe.cn/app/bbs/123")
        assert result is not None
        assert result["platform"] == "小黑盒"
        assert result["needs_paste"] is True

    def test_unknown_platform_returns_none(self):
        from plugins.deepseek.share_parser import _parse_by_platform
        result = _parse_by_platform("<html></html>", "https://example.com/page")
        assert result is None
