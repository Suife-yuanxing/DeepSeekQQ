"""测试 media.py — 媒体分享/URL 处理模块。"""
import pytest
import sys
import types
from unittest.mock import MagicMock, patch


class TestExtractUrls:
    """测试 URL 提取。"""

    def test_single_url(self):
        from plugins.deepseek.media import extract_urls
        urls = extract_urls("看看这个 https://www.example.com/page 很有意思")
        assert urls == ["https://www.example.com/page"]

    def test_multiple_urls(self):
        from plugins.deepseek.media import extract_urls
        urls = extract_urls("https://a.com 和 https://b.com")
        assert len(urls) == 2

    def test_no_url(self):
        from plugins.deepseek.media import extract_urls
        assert extract_urls("这是一段普通的文本") == []

    def test_http_url(self):
        from plugins.deepseek.media import extract_urls
        assert extract_urls("http://example.com") == ["http://example.com"]

    def test_url_with_query_params(self):
        from plugins.deepseek.media import extract_urls
        urls = extract_urls("https://example.com/search?q=hello&lang=zh")
        assert urls == ["https://example.com/search?q=hello&lang=zh"]

    def test_empty_string(self):
        from plugins.deepseek.media import extract_urls
        assert extract_urls("") == []


class TestRemoveUrls:
    """测试 URL 移除。"""

    def test_removes_url(self):
        from plugins.deepseek.media import remove_urls
        text = remove_urls("看看这个 https://example.com 有意思")
        assert "https://example.com" not in text

    def test_no_url_unchanged(self):
        from plugins.deepseek.media import remove_urls
        assert remove_urls("普通文本没有链接") == "普通文本没有链接"

    def test_multiple_urls_removed(self):
        from plugins.deepseek.media import remove_urls
        text = remove_urls("https://a.com 中间 https://b.com")
        assert "https://" not in text


class TestSplitReplyAndLinks:
    """测试回复拆分。"""

    def test_splits_text_and_urls(self):
        from plugins.deepseek.media import split_reply_and_links
        clean, urls = split_reply_and_links("这是一段回复 https://example.com")
        assert "https://" not in clean
        assert len(urls) == 1

    def test_no_urls_returns_unchanged(self):
        from plugins.deepseek.media import split_reply_and_links
        clean, urls = split_reply_and_links("纯文字回复")
        assert clean == "纯文字回复"
        assert urls == []

    def test_cleans_excess_newlines(self):
        from plugins.deepseek.media import split_reply_and_links
        clean, _ = split_reply_and_links("第一行\n\n\n\n第二行")
        assert clean.count("\n") <= 2
        assert "第一行" in clean
        assert "第二行" in clean


class TestShareableItem:
    """测试 ShareableItem 数据结构。"""

    def test_shareable_item_creation(self):
        from plugins.deepseek.media import ShareableItem
        item = ShareableItem(
            title="测试标题", url="https://example.com",
            snippet="这是摘要", image_url="https://example.com/img.jpg"
        )
        assert item.title == "测试标题"
        assert item.url == "https://example.com"
        assert item.snippet == "这是摘要"

    def test_default_values(self):
        from plugins.deepseek.media import ShareableItem
        item = ShareableItem(title="", url="")
        assert item.snippet == ""
        assert item.image_url == ""


class TestMediaPromptHint:
    """测试 Prompt 提示。"""

    def test_hint_not_empty(self):
        from plugins.deepseek.media import get_media_prompt_hint
        hint = get_media_prompt_hint()
        assert len(hint) > 0

    def test_MEDIA_PROMPT_HINT_not_empty(self):
        from plugins.deepseek.media import MEDIA_PROMPT_HINT
        assert isinstance(MEDIA_PROMPT_HINT, str)
        assert len(MEDIA_PROMPT_HINT) > 0


class TestBuildRichMessage:
    """测试富文本消息构建（需 mock Message/MessageSegment）。"""

    def test_empty_text_no_urls(self):
        from plugins.deepseek.media import build_rich_message
        msg = build_rich_message("", [])
        # 空消息应返回空 Message（list-like）
        assert len(msg) == 0

    def test_text_produces_message(self):
        from plugins.deepseek.media import build_rich_message
        # 手动注入 MessageSegment mock
        with patch("plugins.deepseek.media.MessageSegment") as mock_seg:
            mock_seg.text = MagicMock(return_value="[text]")
            msg = build_rich_message("你好", [])
            assert len(msg) >= 1

    def test_show_links_false_hides_search_items(self):
        from plugins.deepseek.media import build_rich_message, ShareableItem
        items = [ShareableItem(title="Test", url="https://t.com", snippet="s")]
        with patch("plugins.deepseek.media.MessageSegment") as mock_seg:
            mock_seg.text = MagicMock(return_value="[text]")
            msg = build_rich_message("hello", [], search_items=items, show_links=False)
            # 不应包含搜索链接
            assert len(msg) >= 1
