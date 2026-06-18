"""主消息处理器 — Pipeline 架构。

借鉴 ECC 的 Hook 系统，将消息处理拆分为有序的 Pipeline 阶段。
每个阶段可短路（返回 SKIP 跳过后续），新增功能只需注册一个阶段。

P2-7: Pipeline 基础设施 → pipeline.py，各 stage → stages/ 目录。
本文件仅负责按正确顺序导入所有 stage 模块（触发 @stage 装饰器注册）。
"""
import importlib

from nonebot import logger

from .handler_helpers import parse_target_lines

# 向后兼容：现有测试引用的内部函数名
_parse_target_lines = parse_target_lines

# ============================================================
# Pipeline 阶段注册（import 顺序 = 执行顺序，请勿随意调整）
# ============================================================

# --- 第0步: 基础设施（已在 pipeline.py 中，无需额外导入） ---

# --- 第1+2批: stages/ 目录中的阶段 ---
from .stages import stage_private_whitelist  # 1:  private_whitelist
# stage_security → AgentRouter (agent_security, p=10)
from .stages import stage_session_recovery   # 3:  session_recovery
from .stages import stage_voice              # 4:  voice_recognition
from .stages import stage_voice_call         # 5:  voice_call
from .stages import stage_rate_limit         # 6:  rate_limit
from .stages import stage_share              # 7:  share_extract
from .stages import stage_share_only         # 8:  share_only_reply
from .stages import stage_group_filter       # 9:  group_filter
from .stages import stage_xiaohaihe          # 10: xiaohaihe
from .stages import stage_affection          # 11: affection
from .stages import stage_context            # 12: context_analysis
from .stages import stage_schedule_interrupt # 13: schedule_interrupt
from .stages import stage_reminder           # 14: reminder
# stage_music → AgentRouter (agent_music, p=35)
# stage_phone_direct → AgentRouter (agent_phone_direct, p=40)
from .stages import stage_llm                # 17: llm_call
from .stages import stage_mcp_execute        # 18: mcp_execute
from .stages import stage_image_gen          # 19: image_gen
from .stages import stage_plugins            # 20: plugins
from .stages import stage_humanize           # 21: humanize
from .stages import stage_post               # 22: post_process

# ============================================================
# H8: 启动时验证所有 Pipeline 阶段模块可导入
# ============================================================

_REQUIRED_STAGES = {
    "stage_private_whitelist",  # 1
    "stage_session_recovery",   # 3
    "stage_voice",              # 4
    "stage_voice_call",         # 5
    "stage_rate_limit",         # 6
    "stage_share",              # 7
    "stage_share_only",         # 8
    "stage_group_filter",       # 9
    "stage_xiaohaihe",          # 10
    "stage_affection",          # 11
    "stage_context",            # 12
    "stage_schedule_interrupt", # 13
    "stage_reminder",           # 14
    "stage_llm",                # 17
    "stage_mcp_execute",        # 18
    "stage_image_gen",          # 19
    "stage_plugins",            # 20
    "stage_humanize",           # 21
    "stage_post",               # 22
}

_LOADED_STAGES = []


def _verify_stages() -> None:
    """H8: 启动时验证所有 pipeline 阶段模块可导入。

    若任一阶段导入失败，抛出明确的 ImportError，
    列出缺失的模块和已成功加载的模块。
    """
    missing = []
    for name in sorted(_REQUIRED_STAGES):
        try:
            importlib.import_module(f".stages.{name}", package=__package__)
            _LOADED_STAGES.append(name)
        except ImportError as e:
            missing.append((name, str(e)))
    if missing:
        raise ImportError(
            f"Pipeline 阶段加载失败: {missing}\n"
            f"已加载: {_LOADED_STAGES}"
        )
    logger.info(f"[Pipeline] 全部 {len(_LOADED_STAGES)} 个阶段加载验证通过")


# 模块加载时执行验证
_verify_stages()
