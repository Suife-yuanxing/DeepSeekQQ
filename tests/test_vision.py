"""测试 vision.py — 图片视觉识别模块（纯逻辑/工具函数）。"""
import pytest
import sys
import types
from collections import OrderedDict
from unittest.mock import AsyncMock, patch, MagicMock
pytestmark = [pytest.mark.unit]


# ---- Mock 第三方依赖（不影响项目模块）----
if "aiohttp" not in sys.modules:
    mock_aiohttp = types.ModuleType("aiohttp")
    mock_aiohttp.ClientTimeout = lambda total: total
    sys.modules["aiohttp"] = mock_aiohttp


class TestExtractVisionText:
    """测试 extract_vision_text 安全提取函数（纯逻辑，无需 mock 视觉 API）。"""

    def test_normal_description(self):
        from plugins.deepseek.vision import extract_vision_text
        result = "[图片内容: 这是一只橘猫趴在窗台上晒太阳]"
        assert extract_vision_text(result) == "这是一只橘猫趴在窗台上晒太阳"

    def test_ocr_result(self):
        from plugins.deepseek.vision import extract_vision_text
        text = "今天天气真好\n一起去公园吧"
        result = f"[图片中的文字内容]: {text}"
        assert extract_vision_text(result) == text

    def test_placeholder(self):
        from plugins.deepseek.vision import extract_vision_text
        assert extract_vision_text("[图片内容暂无法识别]") == ""

    def test_file_missing(self):
        from plugins.deepseek.vision import extract_vision_text
        assert extract_vision_text("[图片文件不存在]") == ""

    def test_empty_input(self):
        from plugins.deepseek.vision import extract_vision_text
        assert extract_vision_text("") == ""

    def test_none_returns_empty(self):
        from plugins.deepseek.vision import extract_vision_text
        assert extract_vision_text(None) == ""

    def test_unformatted_text_passthrough(self):
        from plugins.deepseek.vision import extract_vision_text
        assert extract_vision_text("random text") == "random text"

    def test_partial_prefix_no_match(self):
        from plugins.deepseek.vision import extract_vision_text
        # 前缀不完整，不匹配
        text = "[图片内容 缺少冒号]"
        assert extract_vision_text(text) == text


class TestRecognizeSticker:
    """测试 recognize_sticker（mock analyze_image）。"""

    @pytest.mark.asyncio
    async def test_valid_emotion(self):
        from plugins.deepseek.vision import recognize_sticker
        with patch("plugins.deepseek.vision.analyze_image",
                   AsyncMock(return_value="[图片内容: happy]")):
            result = await recognize_sticker("http://example.com/s.jpg")
            assert result == "happy"

    @pytest.mark.asyncio
    async def test_invalid_emotion_returns_none(self):
        from plugins.deepseek.vision import recognize_sticker
        with patch("plugins.deepseek.vision.analyze_image",
                   AsyncMock(return_value="[图片内容: confused]")):
            result = await recognize_sticker("http://example.com/s.jpg")
            assert result is None

    @pytest.mark.asyncio
    async def test_placeholder_returns_none(self):
        from plugins.deepseek.vision import recognize_sticker
        with patch("plugins.deepseek.vision.analyze_image",
                   AsyncMock(return_value="[图片内容暂无法识别]")):
            result = await recognize_sticker("http://example.com/s.jpg")
            assert result is None

    @pytest.mark.asyncio
    async def test_ocr_result_returns_none(self):
        from plugins.deepseek.vision import recognize_sticker
        with patch("plugins.deepseek.vision.analyze_image",
                   AsyncMock(return_value="[图片中的文字内容]: 一些文字")):
            result = await recognize_sticker("http://example.com/s.jpg")
            assert result is None

    @pytest.mark.asyncio
    async def test_file_missing_returns_none(self):
        from plugins.deepseek.vision import recognize_sticker
        with patch("plugins.deepseek.vision.analyze_image",
                   AsyncMock(return_value="[图片文件不存在]")):
            result = await recognize_sticker("http://example.com/s.jpg")
            assert result is None


class TestImageCache:
    """测试 LRU 图片缓存。"""

    def _make_cache(self):
        from collections import OrderedDict
        class _Cache(OrderedDict):
            MAX_SIZE = 200
            def __setitem__(self, key, value):
                if key in self:
                    self.move_to_end(key)
                else:
                    if len(self) >= self.MAX_SIZE:
                        oldest = next(iter(self))
                        del self[oldest]
                super().__setitem__(key, value)
        return _Cache()

    def test_cache_store_and_retrieve(self):
        cache = self._make_cache()
        cache["url1"] = "data1"
        assert cache["url1"] == "data1"

    def test_cache_miss_returns_none(self):
        cache = self._make_cache()
        assert cache.get("nonexistent") is None

    def test_cache_lru_eviction(self):
        cache = self._make_cache()
        cache.MAX_SIZE = 3
        cache["a"] = "1"
        cache["b"] = "2"
        cache["c"] = "3"
        cache["d"] = "4"  # evicts oldest
        assert "a" not in cache
        assert "b" in cache
        assert "d" in cache

    def test_cache_setitem_moves_existing_to_end(self):
        """覆盖已存在的 key 会将其移到末尾（最新）。"""
        cache = self._make_cache()
        cache.MAX_SIZE = 3
        cache["a"] = "1"
        cache["b"] = "2"
        cache["c"] = "3"
        # 覆盖 "a" → 移到末尾
        cache["a"] = "1_updated"
        cache["d"] = "4"  # 驱逐 "b"（现在是最旧的）
        assert "a" in cache
        assert "b" not in cache
        assert "c" in cache
        assert "d" in cache
