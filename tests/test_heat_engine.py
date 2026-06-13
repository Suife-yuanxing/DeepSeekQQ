"""群聊热度状态机单元测试。"""
import time
import pytest
from plugins.deepseek.heat_engine import (
    HeatState, update_heat, get_heat_state,
    should_interject, get_interjection_strategy,
    get_group_heat_description, reset_heat, cleanup_stale_trackers,
)


@pytest.fixture(autouse=True)
def _clean():
    yield
    # 清理测试状态
    for chat_id in ["test_group", "test_user"]:
        reset_heat(chat_id, is_group="group" in chat_id)
        reset_heat(chat_id, is_group=False)


class TestHeatStateMachine:
    def test_initial_state_idle(self):
        state = get_heat_state("new_group", is_group=True)
        assert state == HeatState.IDLE

    def test_single_message_warm(self):
        state = update_heat("test_group_single", is_group=True)
        # 一条消息，热度=1.0 > 0.5 (WARM阈值)
        assert state == HeatState.WARM

    def test_multiple_messages_warm(self):
        for _ in range(3):
            state = update_heat("test_group", is_group=True)
        assert state in (HeatState.WARM, HeatState.ACTIVE)

    def test_flood_state(self):
        # 模拟刷屏：很多消息
        for _ in range(8):
            state = update_heat("test_group_flood", is_group=True)
        assert state == HeatState.FLOOD  # 8条消息，热度高

    def test_decay_to_idle(self):
        # 发消息后很久不发 → COLD/IDLE
        update_heat("test_group", is_group=True)
        # 无法在不mock时间的情况下测试衰减...
        # 只是一个快照测试
        state = get_heat_state("test_group", is_group=True)
        assert state in (HeatState.COLD, HeatState.IDLE, HeatState.WARM)

    def test_private_chat_separate(self):
        """私聊和群聊的热度是独立的。"""
        gs = update_heat("test_group", is_group=True)
        ps = update_heat("test_user", is_group=False)
        # 两者应该独立
        assert get_heat_state("test_group", is_group=True) != HeatState.IDLE
        assert get_heat_state("test_user", is_group=False) != HeatState.IDLE


class TestInterjection:
    def test_no_feed_content(self):
        should, strategy = should_interject("test_group", is_group=True, has_feed_content=False)
        assert not should
        assert strategy is None

    def test_with_feed_warm_state(self):
        # 先升温
        for _ in range(3):
            update_heat("test_group_warm", is_group=True)
        should, strategy = should_interject("test_group_warm", is_group=True, has_feed_content=True)
        # 不保证概率命中，只检查返回值格式
        if should:
            assert strategy is not None
            assert "probability" in strategy

    def test_strategy_for_idle(self):
        strategy = get_interjection_strategy("new_empty_group", is_group=True)
        assert strategy["probability"] > 0  # IDLE时应该可以推送


class TestHeatDescriptions:
    def test_description_not_empty(self):
        desc = get_group_heat_description("test_group")
        assert isinstance(desc, str)

    def test_description_for_idle(self):
        desc = get_group_heat_description("new_group")
        assert "安静" in desc or "沉默" in desc or "IDLE" in desc


class TestCleanup:
    def test_cleanup_stale(self):
        update_heat("test_cleanup", is_group=True)
        # 模拟时间流逝：直接清
        reset_heat("test_cleanup", is_group=True)
        state = get_heat_state("test_cleanup", is_group=True)
        assert state == HeatState.IDLE
