"""测试 share_parser.py — 分享内容抓取与缓存（纯逻辑函数）。

⚠️ 本文件所有 sys.modules mock 均通过 autouse fixture 管理，
   测试结束后自动恢复，不会污染其他测试文件。
"""
import pytest
import sys
import types
import re
from unittest.mock import AsyncMock, MagicMock, patch
from collections import OrderedDict
pytestmark = [pytest.mark.unit]



def _safe_module_mock(name: str, **attrs):
    """创建安全的模块 mock：任何未显式设置的属性自动返回 MagicMock。

    避免因 mock 属性不全导致其他模块（如 handler.py → context_analyzer.py → database）
    导入失败。"""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)

    def _fallback_getattr(attr_name):
        if attr_name.startswith("_"):
            raise AttributeError(attr_name)
        return MagicMock()

    mod.__getattr__ = _fallback_getattr
    return mod


# aiohttp 是第三方包，mock 不会污染项目模块
if "aiohttp" not in sys.modules:
    mock_aiohttp = types.ModuleType("aiohttp")
    mock_aiohttp.ClientTimeout = lambda total: total
    sys.modules["aiohttp"] = mock_aiohttp

# 保存原始模块引用，teardown 时恢复
_SAVED_MODULES = {}


def _setup_mocks():
    """设置 share_parser 导入所需的模块 mock。"""
    mocks = {
        "plugins.deepseek.config": _safe_module_mock(
            "plugins.deepseek.config",
            SHARE_TTL=1800, URL_FETCH_COOLDOWN=300,
        ),
        "plugins.deepseek.api": _safe_module_mock(
            "plugins.deepseek.api",
            get_http_session=AsyncMock(return_value=AsyncMock()),
        ),
        "plugins.deepseek.database": _safe_module_mock(
            "plugins.deepseek.database",
            get_article_cache=AsyncMock(return_value=None),
            save_article_cache=AsyncMock(return_value=None),
        ),
        "plugins.deepseek.vision": _safe_module_mock(
            "plugins.deepseek.vision",
            recognize_sticker=AsyncMock(return_value=None),
            analyze_image=AsyncMock(return_value="[图片内容: 一只猫]"),
            extract_vision_text=lambda x: (
                x.replace("[图片内容: ", "").replace("]", "")
                if x and isinstance(x, str) and x.startswith("[图片内容:")
                else (x or "")
            ),
        ),
        "plugins.deepseek.image_reply": _safe_module_mock(
            "plugins.deepseek.image_reply",
            classify_image=MagicMock(return_value="photo_pet"),
            IMAGE_TYPE_STICKER="sticker",
        ),
        "plugins.deepseek.utils": None,  # 特殊处理：需要 LRUDict 类
    }

    # 为 utils 构建特殊的 mock（含 LRUDict）
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

    mocks["plugins.deepseek.utils"] = _safe_module_mock(
        "plugins.deepseek.utils", LRUDict=_LRUDict,
    )

    for name, mock in mocks.items():
        if name not in sys.modules:
            sys.modules[name] = mock
            _SAVED_MODULES[name] = None  # 标记为需要删除
        else:
            _SAVED_MODULES[name] = sys.modules[name]  # 保存原始值（但不应发生）
            sys.modules[name] = mock


def _teardown_mocks():
    """恢复被 mock 替换的模块。"""
    mocked = [
        "plugins.deepseek.config",
        "plugins.deepseek.api",
        "plugins.deepseek.database",
        "plugins.deepseek.vision",
        "plugins.deepseek.image_reply",
        "plugins.deepseek.utils",
    ]
    for name in mocked:
        saved = _SAVED_MODULES.get(name)
        if saved is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = saved
    _SAVED_MODULES.clear()


@pytest.fixture(autouse=True, scope="class")
def _mock_share_parser_deps():
    """为每个测试类设置/清理模块 mock，防止污染其他测试文件。"""
    _setup_mocks()
    yield
    _teardown_mocks()


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


class TestExtractDouyinRenderData:
    """测试 _extract_douyin_render_data — RENDER_DATA 提取。"""

    def test_extracts_from_render_data_script(self):
        """从经典 RENDER_DATA script 标签中提取视频信息。"""
        from plugins.deepseek.share_parser import _extract_douyin_render_data
        import json
        from urllib.parse import quote

        # 模拟抖音 RENDER_DATA 结构
        aweme_data = {
            "aweme": {
                "detail": {
                    "aweme": {
                        "desc": "这只猫太可爱了！",
                        "create_time": 1700000000,
                        "author": {
                            "nickname": "萌宠达人",
                            "avatar_thumb": {
                                "url_list": ["https://example.com/avatar.jpg"]
                            }
                        },
                        "video": {
                            "duration": 15000,
                            "cover": {
                                "url_list": ["https://example.com/cover.jpg"]
                            }
                        },
                        "statistics": {
                            "comment_count": 520,
                            "digg_count": 13000
                        },
                        "music": {
                            "title": "可爱BGM"
                        }
                    }
                }
            }
        }
        encoded = quote(json.dumps(aweme_data, ensure_ascii=False))
        html = f'<script id="RENDER_DATA" type="application/json">{encoded}</script>'

        result = _extract_douyin_render_data(html)
        assert result is not None
        assert result["desc"] == "这只猫太可爱了！"
        assert result["nickname"] == "萌宠达人"
        assert result["duration"] == 15000
        assert result["cover_url"] == "https://example.com/cover.jpg"
        assert result["comment_count"] == 520
        assert result["digg_count"] == 13000
        assert result["music_title"] == "可爱BGM"

    def test_extracts_from_next_data_script(self):
        """从 __NEXT_DATA__ script 标签中提取。"""
        from plugins.deepseek.share_parser import _extract_douyin_render_data
        import json

        aweme_data = {
            "common": {
                "aweme": {
                    "detail": {
                        "aweme": {
                            "desc": "Next.js渲染的抖音页面",
                            "author": {"nickname": "测试用户"},
                            "video": {"duration": 30, "cover": {"url_list": ["https://img.com/cover.jpg"]}},
                            "statistics": {"comment_count": 10, "digg_count": 100},
                        }
                    }
                }
            }
        }
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(aweme_data, ensure_ascii=False)}</script>'

        result = _extract_douyin_render_data(html)
        assert result is not None
        assert result["desc"] == "Next.js渲染的抖音页面"
        assert result["nickname"] == "测试用户"

    def test_no_render_data_returns_none(self):
        """无 RENDER_DATA 时返回 None。"""
        from plugins.deepseek.share_parser import _extract_douyin_render_data
        result = _extract_douyin_render_data("<html><body>普通页面</body></html>")
        assert result is None

    def test_empty_render_data_returns_none(self):
        """RENDER_DATA 中无有效视频数据时返回 None。"""
        from plugins.deepseek.share_parser import _extract_douyin_render_data
        import json
        from urllib.parse import quote

        # JSON 中不包含 aweme 信息
        encoded = quote(json.dumps({"unrelated": "data"}))
        html = f'<script id="RENDER_DATA" type="application/json">{encoded}</script>'

        result = _extract_douyin_render_data(html)
        assert result is None

    def test_douyin_parse_with_render_data(self):
        """完整流程：有 RENDER_DATA 的抖音页面解析。"""
        from plugins.deepseek.share_parser import _parse_by_platform
        import json
        from urllib.parse import quote

        aweme_data = {
            "aweme": {
                "detail": {
                    "aweme": {
                        "desc": "春天的第一场雨，好美啊🌧️",
                        "author": {"nickname": "摄影师小王"},
                        "video": {"duration": 45, "cover": {"url_list": ["https://img.com/cover.jpg"]}},
                        "statistics": {"comment_count": 88, "digg_count": 2400},
                        "music": {"title": "Rain Sounds"},
                    }
                }
            }
        }
        encoded = quote(json.dumps(aweme_data, ensure_ascii=False))
        html = f'<script id="RENDER_DATA" type="application/json">{encoded}</script>'

        result = _parse_by_platform(html, "https://www.douyin.com/video/12345")
        assert result is not None
        assert result["platform"] == "douyin"
        assert "春天的第一场雨" in result["summary"]
        assert result["author"] == "摄影师小王"
        assert "2400点赞" in result["summary"]
        assert "88评论" in result["summary"]
        assert "Rain Sounds" in result["summary"]
        assert result.get("fetch_failed") is None  # 不应标记为失败
        assert result["restricted"] is True

    def test_douyin_parse_render_data_fallback_to_meta(self):
        """无 RENDER_DATA 时回退到 meta 标签提取。"""
        from plugins.deepseek.share_parser import _parse_by_platform

        html = (
            '<html><head>'
            '<meta property="og:title" content="抖音视频标题">'
            '<meta property="og:description" content="这是视频描述内容足够长">'
            '<title>抖音</title>'
            '</head><body></body></html>'
        )
        result = _parse_by_platform(html, "https://v.douyin.com/abc123/")
        assert result is not None
        assert result["platform"] == "douyin"
        assert result["title"] == "抖音视频标题"
        assert result.get("fetch_failed") is None

    def test_douyin_parse_complete_failure(self):
        """完全无法提取内容时返回 fetch_failed。"""
        from plugins.deepseek.share_parser import _parse_by_platform

        html = '<html><head><title>抖音</title></head><body></body></html>'
        result = _parse_by_platform(html, "https://www.douyin.com/video/999")
        assert result is not None
        assert result["platform"] == "douyin"
        assert result["fetch_failed"] is True
        assert "内容无法读取" in result["summary"]


class TestIsValidShareFetchFailed:
    """测试 _is_valid_share 对 fetch_failed 的处理。"""

    def test_fetch_failed_still_valid_for_bot_response(self):
        """fetch_failed 的分享仍应通过 _is_valid_share（bot 需告知用户打不开）。

        但不会被缓存到 DB（由 fetch_url_content 控制）。
        """
        from plugins.deepseek.share_parser import _is_valid_share
        assert _is_valid_share({
            "summary": "[抖音视频链接，内容无法读取]",
            "restricted": True,
            "fetch_failed": True,
            "platform": "douyin",
        }) is True

    def test_fetch_failed_with_long_summary_still_valid(self):
        """fetch_failed 但有足够长 summary 的分享仍有效（非 restricted 场景的回退）。"""
        from plugins.deepseek.share_parser import _is_valid_share
        # summary 足够长 → 有效（length > 80）
        assert _is_valid_share({
            "summary": "这是一段足够长的摘要内容，" * 10,
            "fetch_failed": True,
        }) is True
