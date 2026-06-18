"""Test Video Parser — 视频链接解析与信息格式化。

覆盖：
- _extract_bilibili_id B站URL提取
- _fmt_count 数字格式化
- VideoInfo.format_summary 摘要生成
- VideoInfo.to_share_dict 兼容转换
"""
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════
# _extract_bilibili_id — B站 URL 解析
# ═══════════════════════════════════════════════════════════════

class TestExtractBilibiliId:
    """测试 _extract_bilibili_id 从各格式 B站 URL 提取 ID。"""

    def test_bv_normal_url(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        result = _extract_bilibili_id("https://www.bilibili.com/video/BV1xx411c7mD")
        assert result == "BV1xx411c7mD"

    def test_bv_short_url(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        result = _extract_bilibili_id("https://b23.tv/BV1xx411c7mD")
        assert result == "BV1xx411c7mD"

    def test_bv_without_protocol(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        result = _extract_bilibili_id("www.bilibili.com/video/BV1xx411c7mD")
        assert result == "BV1xx411c7mD"

    def test_bangumi_ep(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        result = _extract_bilibili_id("https://www.bilibili.com/bangumi/play/ep12345")
        assert result == "12345"

    def test_bangumi_ss(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        result = _extract_bilibili_id("https://www.bilibili.com/bangumi/play/ss67890")
        assert result == "67890"

    def test_non_bilibili_url(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        result = _extract_bilibili_id("https://www.youtube.com/watch?v=abc123")
        assert result is None

    def test_empty_url(self):
        from plugins.deepseek.video_parser import _extract_bilibili_id
        assert _extract_bilibili_id("") is None


# ═══════════════════════════════════════════════════════════════
# _fmt_count — 数字格式化
# ═══════════════════════════════════════════════════════════════

class TestFmtCount:
    """测试 _fmt_count 大数字格式化。"""

    def test_small_number(self):
        from plugins.deepseek.video_parser import _fmt_count
        assert _fmt_count(999) == "999"
        assert _fmt_count(0) == "0"

    def test_wan(self):
        from plugins.deepseek.video_parser import _fmt_count
        result = _fmt_count(12345)
        assert "万" in result
        assert result.startswith("1.2")

    def test_yi(self):
        from plugins.deepseek.video_parser import _fmt_count
        result = _fmt_count(150_000_000)
        assert "亿" in result
        assert result.startswith("1.5")


# ═══════════════════════════════════════════════════════════════
# VideoInfo.format_summary — 摘要生成
# ═══════════════════════════════════════════════════════════════

class TestVideoInfoFormatSummary:
    """测试 VideoInfo.format_summary 格式化输出。"""

    def test_basic_bilibili(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(
            title="【教程】Python入门",
            author="Up主",
            duration=120,
            view_count=50000,
            like_count=3000,
            platform="bilibili",
        )
        summary = info.format_summary()
        assert "B站视频" in summary
        assert "Python入门" in summary
        assert "Up主" not in summary  # author 不在默认摘要中（仅 title+description）
        assert "5.0万" in summary  # 50000播放

    def test_duration_under_minute(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(title="短视频", duration=30, platform="douyin")
        summary = info.format_summary()
        assert "30秒" in summary

    def test_duration_over_minute(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(title="长视频", duration=185, platform="youtube")
        summary = info.format_summary()
        assert "3分5秒" in summary

    def test_with_music(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(title="抖音视频", music_title="原声BGM", platform="douyin")
        summary = info.format_summary()
        assert "原声BGM" in summary

    def test_truncation(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(title="A" * 500, description="B" * 500, platform="bilibili")
        summary = info.format_summary(max_len=200)
        assert len(summary) <= 200

    def test_no_duplicate_desc(self):
        """描述与标题相同时不应重复。"""
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(title="Same", description="Same", platform="bilibili")
        summary = info.format_summary()
        # 描述不应被单独添加（因为和标题相同）
        assert summary.count("Same") == 1


# ═══════════════════════════════════════════════════════════════
# VideoInfo.to_share_dict — 兼容转换
# ═══════════════════════════════════════════════════════════════

class TestVideoInfoToShareDict:
    """测试 to_share_dict 转换为 share_parser 兼容格式。"""

    def test_basic_conversion(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(
            title="Test",
            author="Author",
            cover_url="https://img.example.com/cover.jpg",
            platform="bilibili",
        )
        d = info.to_share_dict("https://bilibili.com/video/BV123")
        assert d["title"] == "Test"
        assert d["author"] == "Author"
        assert d["platform"] == "bilibili"
        assert d["url"] == "https://bilibili.com/video/BV123"
        assert d["image_url"] == "https://img.example.com/cover.jpg"
        assert "_video_info" in d

    def test_fallback_title(self):
        from plugins.deepseek.video_parser import VideoInfo
        info = VideoInfo(platform="unknown")
        d = info.to_share_dict("https://example.com")
        assert d["title"] == "unknown视频"


# ═══════════════════════════════════════════════════════════════
# parse_video_url — 主入口（仅边界情况）
# ═══════════════════════════════════════════════════════════════

class TestParseVideoUrl:
    """测试 parse_video_url 主入口的边界情况。"""

    @pytest.mark.asyncio
    async def test_empty_url(self):
        from plugins.deepseek.video_parser import parse_video_url
        result = await parse_video_url("")
        assert result is None

    @pytest.mark.asyncio
    async def test_none_url(self):
        from plugins.deepseek.video_parser import parse_video_url
        result = await parse_video_url(None)
        assert result is None
