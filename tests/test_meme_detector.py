"""热梗自动检测单元测试。"""
import pytest
from plugins.deepseek.meme_detector import (
    filter_meme_candidates,
    merge_into_lexicon,
    clean_stale_memes,
    _parse_llm_response,
)


class TestFilterMemeCandidates:
    def test_valid_meme_passes(self):
        candidates = [
            {"word": "绝绝子", "meaning": "太绝了", "mood": "开心", "keywords": ["好", "棒"]},
        ]
        result = filter_meme_candidates(candidates)
        assert len(result) == 1
        assert result[0]["word"] == "绝绝子"

    def test_too_short_rejected(self):
        candidates = [{"word": "啊", "meaning": "一个感叹词"}]
        result = filter_meme_candidates(candidates)
        assert len(result) == 0

    def test_too_long_rejected(self):
        candidates = [{"word": "这是一个非常非常长的词条不符合网络梗特征" * 3, "meaning": "太长"}]
        result = filter_meme_candidates(candidates)
        assert len(result) == 0

    def test_pure_number_rejected(self):
        candidates = [{"word": "12345", "meaning": "数字"}]
        result = filter_meme_candidates(candidates)
        assert len(result) == 0

    def test_brand_name_rejected(self):
        candidates = [{"word": "华为发布会", "meaning": "华为开发布会"}]
        result = filter_meme_candidates(candidates)
        assert len(result) == 0

    def test_city_name_rejected(self):
        candidates = [{"word": "北京暴雨", "meaning": "北京下暴雨"}]
        result = filter_meme_candidates(candidates)
        assert len(result) == 0

    def test_empty_meaning_rejected(self):
        candidates = [{"word": "测试梗", "meaning": ""}]
        result = filter_meme_candidates(candidates)
        assert len(result) == 0

    def test_mixed_valid_invalid(self):
        candidates = [
            {"word": "绝绝子", "meaning": "太绝了"},
            {"word": "北京", "meaning": "城市名"},
            {"word": "1234", "meaning": "数字"},
            {"word": "CPU干烧了", "meaning": "脑子转不过来"},
        ]
        result = filter_meme_candidates(candidates)
        assert len(result) == 2
        words = [r["word"] for r in result]
        assert "绝绝子" in words
        assert "CPU干烧了" in words


class TestMergeLexicon:
    def test_merge_new(self):
        existing = []
        new = [{"word": "新梗", "meaning": "一个新梗", "mood": "开心", "keywords": ["测试"]}]
        merged = merge_into_lexicon(new, existing, max_count=10)
        assert len(merged) == 1
        assert merged[0]["word"] == "新梗"
        assert merged[0].get("_dynamic") is True

    def test_merge_dedup_same_word(self):
        existing = [{"word": "旧梗", "meaning": "一个旧梗", "_dynamic": True}]
        new = [{"word": "旧梗", "meaning": "相同词条"}]
        merged = merge_into_lexicon(new, existing)
        assert len(merged) == 1  # 不重复添加

    def test_merge_max_count(self):
        existing = [{"word": f"梗{i}", "meaning": f"含义{i}", "_dynamic": True} for i in range(9)]
        new = [
            {"word": "新梗A", "meaning": "含义A"},
            {"word": "新梗B", "meaning": "含义B"},
            {"word": "新梗C", "meaning": "含义C"},
        ]
        merged = merge_into_lexicon(new, existing, max_count=10)
        assert len(merged) <= 10


class TestCleanStale:
    def test_fresh_kept(self):
        import time
        memes = [
            {"word": "新鲜梗", "meaning": "含义", "_dynamic": True, "_added_at": time.time() - 3600},
        ]
        result = clean_stale_memes(memes, ttl_hours=72)
        assert len(result) == 1

    def test_stale_removed(self):
        import time
        memes = [
            {"word": "过期梗", "meaning": "含义", "_dynamic": True, "_added_at": time.time() - 100 * 3600},
        ]
        result = clean_stale_memes(memes, ttl_hours=72)
        assert len(result) == 0

    def test_static_always_kept(self):
        memes = [
            {"word": "静态梗", "meaning": "含义"},  # 没有 _dynamic 标记
        ]
        result = clean_stale_memes(memes, ttl_hours=72)
        assert len(result) == 1


class TestParseLLMResponse:
    def test_json_array(self):
        result = _parse_llm_response('[{"word": "梗A", "meaning": "含义A"}]')
        assert result is not None
        assert len(result) == 1
        assert result[0]["word"] == "梗A"

    def test_markdown_block(self):
        result = _parse_llm_response(
            '```json\n[{"word": "梗A", "meaning": "含义A"}]\n```'
        )
        assert result is not None
        assert len(result) == 1

    def test_empty_list(self):
        result = _parse_llm_response('[]')
        assert result == []

    def test_invalid_json(self):
        result = _parse_llm_response('这不是JSON')
        assert result is None

    def test_none_input(self):
        result = _parse_llm_response(None)
        assert result is None
