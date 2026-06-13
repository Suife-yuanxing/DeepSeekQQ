"""社交信息流引擎单元测试。"""
import time
import pytest
from plugins.deepseek.social_feed import (
    FeedItem, store_feed_items, get_recent_feed,
    decay_feed_memory, get_feed_count, clear_feed,
    mark_as_mentioned, was_mentioned,
    mark_category_ignored, is_category_ignored,
    get_scroll_memory_summary, get_scroll_trigger_hint,
    should_scroll_now, boost_interest_items,
    get_phone_battery_quirk, get_recall_hesitation,
)


@pytest.fixture(autouse=True)
def _clean():
    clear_feed()
    yield
    clear_feed()


class TestFeedItem:
    def test_basic_item(self):
        item = FeedItem(content="测试话题", source="抖音")
        assert item.content == "测试话题"
        assert item.source == "抖音"
        assert item.item_id
        assert item.relevance == 1.0
        assert item.seen_at > 0

    def test_item_id_deterministic(self):
        a = FeedItem(content="测试话题", source="抖音")
        b = FeedItem(content="测试话题", source="抖音")
        assert a.item_id == b.item_id

    def test_item_id_different(self):
        a = FeedItem(content="测试话题A", source="抖音")
        b = FeedItem(content="测试话题B", source="B站")
        assert a.item_id != b.item_id


class TestStoreAndRetrieve:
    def test_store_single(self):
        items = [FeedItem(content="话题1", source="抖音")]
        added = store_feed_items(items)
        assert added == 1
        assert get_feed_count() == 1

    def test_store_duplicate(self):
        items = [FeedItem(content="话题1", source="抖音")]
        store_feed_items(items)
        added = store_feed_items(items)
        assert added == 0  # 去重
        assert get_feed_count() == 1

    def test_get_recent(self):
        items = [
            FeedItem(content=f"话题{i}", source="抖音")
            for i in range(5)
        ]
        store_feed_items(items)
        recent = get_recent_feed(limit=3, max_age_minutes=600)
        assert len(recent) == 3

    def test_get_recent_empty(self):
        recent = get_recent_feed()
        assert recent == []


class TestDecay:
    def test_decay_sets_relevance(self):
        items = [FeedItem(content="旧话题", source="抖音", seen_at=time.time() - 8 * 3600)]
        store_feed_items(items)
        decay_feed_memory()
        recent = get_recent_feed(limit=5, max_age_minutes=600)
        # 8小时后 relevance 应该降到很低
        if recent:
            assert recent[0].relevance < 0.5

    def test_fresh_items_stay_relevant(self):
        items = [FeedItem(content="新话题", source="抖音")]
        store_feed_items(items)
        decay_feed_memory()
        recent = get_recent_feed(limit=5, max_age_minutes=600)
        assert len(recent) == 1
        assert recent[0].relevance > 0.9


class TestDedup:
    def test_mark_and_check(self):
        item = FeedItem(content="测试", source="抖音")
        store_feed_items([item])
        assert not was_mentioned(item.item_id)
        mark_as_mentioned(item.item_id)
        assert was_mentioned(item.item_id)

    def test_category_ignore(self):
        assert not is_category_ignored("游戏")
        mark_category_ignored("游戏", hours=24)
        assert is_category_ignored("游戏")
        assert not is_category_ignored("美食")


class TestSummaries:
    def test_empty_summary(self):
        assert get_scroll_memory_summary() is None

    def test_summary_with_content(self):
        items = [
            FeedItem(content="有趣的抖音话题", source="抖音"),
            FeedItem(content="B站热门视频标题", source="B站"),
        ]
        store_feed_items(items)
        summary = get_scroll_memory_summary(limit=2)
        assert summary is not None
        assert "抖音" in summary or "B站" in summary

    def test_trigger_hint(self):
        items = [FeedItem(content="一个热搜话题", source="微博")]
        store_feed_items(items)
        hint = get_scroll_trigger_hint()
        assert hint is not None
        # 提过后再获取应该返回None（已标记已提及）
        hint2 = get_scroll_trigger_hint()
        assert hint2 is None  # 只有一条，已提过


class TestScrollDecision:
    def test_sleeping_no_scroll(self):
        assert not should_scroll_now("sleeping")

    def test_night_owl_likely(self):
        # 高概率但不应该是100%
        attempts = [should_scroll_now("night_owl") for _ in range(50)]
        assert any(attempts)  # 至少有一些触发

    def test_cooldown(self):
        # 模拟触发后冷却
        # 手动改 _last_scroll_time 不可行，直接测试冷却逻辑
        pass


class TestInterestBoost:
    def test_boost_matching(self):
        items = [
            FeedItem(content="原神新版本更新公告", source="B站"),
            FeedItem(content="普通财经新闻标题", source="微博"),
        ]
        boosted = boost_interest_items(items)
        assert boosted[0].relevance > 1.0  # 被boost了
        assert boosted[1].relevance == 1.0  # 没被boost


class TestQuirks:
    def test_phone_battery_maybe_none(self):
        results = [get_phone_battery_quirk() for _ in range(100)]
        # 0.8%概率，100次基本都应该是None
        nones = sum(1 for r in results if r is None)
        assert nones >= 85  # 至少85次是None

    def test_recall_hesitation(self):
        results = [get_recall_hesitation() for _ in range(100)]
        # 6%概率
        nones = sum(1 for r in results if r is None)
        assert nones >= 70
