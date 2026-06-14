"""Agent 注册模块 — 定义所有 Agent 及其 trigger 条件。

每个 agent 通过 AgentMeta 注册到全局 router。
Trigger 矩阵（文档参考）：

消息类型              security  voice  phone  context  music  dialog
普通文字聊天           ✅        ❌     ❌      ✅       ❌     ✅
语音消息               ✅        ✅     ❌      ✅       ❌     ✅
"帮我截图"             ✅        ❌     ✅      ❌       ❌     ❌  (phone _SKIP)
"放首歌"               ✅        ❌     ❌      ✅       ✅     ❌  (music _SKIP)
触发安全过滤的消息      ✅        ❌     ❌      ❌       ❌     ❌  (security _SKIP)

当前状态：骨架阶段（P0-1）。
- Router 已注册但所有 agent 为 placeholder
- 后续 Phase 逐个实现 agent 的 execute 函数并替换 placeholder
"""
from ..agent_base import AgentMeta, AgentRouter

# 全局 Router 实例
router = AgentRouter()

# ============================================================
# Agent 注册（骨架 — P0-1 完成后逐个实现）
# ============================================================

# --- agent_security (priority=10): 安全过滤，所有消息必经 ---
# router.register(AgentMeta(
#     name="security",
#     priority=10,
#     trigger=lambda ctx: True,
#     execute=_agent_security,
#     parallel_ok=False,
# ))

# --- agent_voice (priority=40): 语音消息处理 ---
# router.register(AgentMeta(
#     name="voice",
#     priority=40,
#     trigger=lambda ctx: getattr(ctx, 'has_voice', False),
#     execute=_agent_voice,
#     parallel_ok=False,
# ))

# --- agent_phone (priority=35): 手机控制直连 ---
# router.register(AgentMeta(
#     name="phone_direct",
#     priority=35,
#     trigger=lambda ctx: getattr(ctx, 'phone_intent_detected', False),
#     execute=_agent_phone,
#     parallel_ok=False,
# ))

# --- agent_context (priority=50): 上下文分析 + 记忆检索 ---
# router.register(AgentMeta(
#     name="context",
#     priority=50,
#     trigger=lambda ctx: not getattr(ctx, 'sec_blocked', False),
#     execute=_agent_context,
#     parallel_ok=False,
# ))

# --- agent_music (priority=60): 音乐点播 ---
# router.register(AgentMeta(
#     name="music",
#     priority=60,
#     trigger=lambda ctx: getattr(ctx, 'music_intent_detected', False),
#     execute=_agent_music,
#     parallel_ok=False,
# ))

# --- agent_dialog (priority=90): LLM 对话（兜底） ---
# router.register(AgentMeta(
#     name="dialog",
#     priority=90,
#     trigger=lambda ctx: (
#         not getattr(ctx, 'sec_blocked', False)
#         and not getattr(ctx, 'dlg_skip_llm', False)
#     ),
#     execute=_agent_dialog,
#     parallel_ok=False,
# ))
