"""P2-7: 验证 Pipeline 阶段注册顺序。"""
import pytest

pytestmark = [pytest.mark.unit]


def test_pipeline_order():
    """验证 19 个阶段按正确顺序注册（A3: security/music/phone_direct 移至 AgentRouter）。"""
    import plugins.deepseek.handler  # noqa: F401 — 触发所有 stage 注册
    from plugins.deepseek.pipeline import _PIPELINE

    expected = [
        "private_whitelist", "session_recovery", "voice_recognition",
        "voice_call", "rate_limit", "share_extract", "share_only_reply",
        "group_filter", "xiaohaihe", "affection", "context_analysis",
        "schedule_interrupt", "reminder",
        "llm_call", "mcp_execute", "image_gen", "plugins",
        "humanize", "post_process"
    ]
    actual = [name for name, _ in _PIPELINE]
    assert actual == expected, f"Order mismatch: {actual}"
    print("\nPipeline order verified (19 stages):")
    for i, name in enumerate(actual, 1):
        print(f"  {i:2d}. {name}")
