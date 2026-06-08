"""测试 ocr.py — OCR 文字提取模块。

Mock 管理：通过类级 fixture 注册/清理，不再污染其他测试文件。
"""
import pytest
import sys
import types
from unittest.mock import MagicMock

pytestmark = [pytest.mark.unit]

# 保存原始模块（如果有的话）
_SAVED_RAPIDOCR = sys.modules.get("rapidocr_onnxruntime")


@pytest.fixture(autouse=True, scope="class")
def _mock_ocr_deps():
    """在测试类运行期间 mock rapidocr_onnxruntime，结束后恢复。"""
    mock_instance = MagicMock()
    mock_rapidocr = MagicMock(return_value=mock_instance)

    sys.modules["rapidocr_onnxruntime"] = types.SimpleNamespace(
        RapidOCR=mock_rapidocr,
    )

    # 把 mock 对象挂到 fixture 上，供测试方法使用
    _mock_ocr_deps.mock_rapidocr = mock_rapidocr
    _mock_ocr_deps.mock_instance = mock_instance

    yield

    # 恢复原始模块
    if _SAVED_RAPIDOCR is None:
        sys.modules.pop("rapidocr_onnxruntime", None)
    else:
        sys.modules["rapidocr_onnxruntime"] = _SAVED_RAPIDOCR


class TestOcrEngineLazy:
    """测试 OCR 引擎懒加载。"""

    def test_get_engine_available(self):
        import plugins.deepseek.ocr as ocr_mod
        assert hasattr(ocr_mod, "_get_engine"), "ocr module should have _get_engine"

    def test_get_engine_initializes(self):
        import plugins.deepseek.ocr as ocr_mod
        ocr_mod._ocr_engine = None
        _mock_ocr_deps.mock_rapidocr.reset_mock()
        engine = ocr_mod._get_engine()
        assert engine is not None

    def test_get_engine_cached_after_first_call(self):
        import plugins.deepseek.ocr as ocr_mod
        ocr_mod._ocr_engine = None
        _mock_ocr_deps.mock_rapidocr.reset_mock()
        first = ocr_mod._get_engine()
        second = ocr_mod._get_engine()
        assert first is second


class TestExtractTextBasic:
    """测试 extract_text 基本行为。"""

    def test_file_not_exists_returns_empty(self):
        from plugins.deepseek.ocr import extract_text_from_image
        result = extract_text_from_image("/nonexistent/path.jpg")
        assert result == ""

    def test_returns_string_type(self):
        from plugins.deepseek.ocr import extract_text_from_image
        result = extract_text_from_image("nonexistent.jpg")
        assert isinstance(result, str)


class TestOcrAsync:
    """测试异步版本。"""

    @pytest.mark.asyncio
    async def test_async_returns_string(self):
        from plugins.deepseek.ocr import extract_text_from_image_async
        result = await extract_text_from_image_async("/nonexistent/path.jpg")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_async_file_not_exists(self):
        from plugins.deepseek.ocr import extract_text_from_image_async
        result = await extract_text_from_image_async("/nonexistent/path.jpg")
        assert result == ""
