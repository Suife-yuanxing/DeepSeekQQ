"""Agent 基类和 ChatContext 字段分区定义。

P0-1: Agent Router 基础设施。
- AgentOutput: 每个 agent 的独立输出空间，避免并行写竞态
- AgentMeta: agent 元数据（优先级、trigger、execute）
- AgentRouter: 路由分发器，支持串行/并行模式

设计原则：
- 字段分区完成前，_parallel_enabled = False（全局串行安全模式）
- 每个 agent 只写自己的 AgentOutput，完成后统一 merge
- trigger predicate 决定消息是否进入该 agent
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from .constants import _SKIP  # 消除循环导入（C-7）


@dataclass
class AgentOutput:
    """每个 Agent 的独立输出，避免并行写竞态。

    并行执行时各 agent 写各自的 AgentOutput，
    全部完成后通过 merge_agent_output() 合并到 ChatContext。
    """
    agent_name: str
    data: dict = field(default_factory=dict)

    def set(self, key: str, value: Any):
        self.data[key] = value

    def get(self, key: str, default=None):
        return self.data.get(key, default)


@dataclass
class AgentMeta:
    """Agent 元数据定义。

    Attributes:
        name: agent 唯一名称
        priority: 优先级（越小越先执行）
        trigger: 决定此 agent 是否参与本次消息处理
        execute: agent 执行函数 (ctx, output) -> None | _SKIP
        parallel_ok: 是否可与其他 agent 并行（需字段分区完成后才可开启）
    """
    name: str
    priority: int
    trigger: Callable[["ChatContext"], bool]
    execute: Callable[["ChatContext", AgentOutput], Coroutine]
    parallel_ok: bool = False  # 默认串行；字段分区完成后才可开启


class AgentRouter:
    """Agent 路由分发器。

    维护已注册 agent 的优先级队列，支持：
    - 串行模式（_parallel_enabled=False）：所有触发的 agent 按优先级串行执行
    - 并行模式（_parallel_enabled=True）：标记 parallel_ok 的 agent 并行执行，其余串行

    Usage:
        router = AgentRouter()
        router.register(AgentMeta(name="security", priority=10, ...))
        router.register(AgentMeta(name="dialog", priority=90, ...))
        result = await router.dispatch(ctx)
    """

    def __init__(self):
        self._agents: list[AgentMeta] = []
        self._parallel_enabled = False  # 全局开关，字段分区完成前保持 False

    @property
    def parallel_enabled(self) -> bool:
        return self._parallel_enabled

    def enable_parallel(self):
        """字段分区完成后，由 P0-1.5 调用此方法启用并行模式。"""
        self._parallel_enabled = True

    def register(self, meta: AgentMeta):
        """注册一个 agent。

        Raises:
            NotImplementedError: 如果在字段分区完成前尝试注册 parallel_ok=True 的 agent
        """
        if meta.parallel_ok and not self._parallel_enabled:
            raise NotImplementedError(
                f"Agent '{meta.name}' 请求并行，但 ChatContext 字段分区尚未完成。"
                "请完成 P0-1.5（Context 字段重构）并将 _parallel_enabled 置为 True 后再启用。"
            )
        self._agents.append(meta)
        self._agents.sort(key=lambda a: a.priority)

    def _get_triggered(self, ctx: "ChatContext") -> list[AgentMeta]:
        """返回所有 trigger 为 True 的 agent。"""
        return [a for a in self._agents if a.trigger(ctx)]

    async def dispatch(self, ctx: "ChatContext") -> bool:
        """分发消息到触发的 agent，返回 True 表示被短路（_SKIP）。

        执行顺序：
        1. 如果并行模式开启，先并行执行所有 parallel_ok=True 的 agent
        2. 再串行执行 parallel_ok=False 的 agent
        3. 遇 _SKIP 立即短路，后续 agent 不再执行
        """
        triggered = self._get_triggered(ctx)

        if self._parallel_enabled:
            parallel_agents = [a for a in triggered if a.parallel_ok]
            serial_agents = [a for a in triggered if not a.parallel_ok]

            # 并行阶段
            if parallel_agents:
                outputs = [AgentOutput(a.name) for a in parallel_agents]
                results = await asyncio.gather(
                    *[a.execute(ctx, o) for a, o in zip(parallel_agents, outputs)],
                    return_exceptions=True,
                )
                # 并行完成后统一合并，无竞态
                for agent, output, result in zip(parallel_agents, outputs, results):
                    if isinstance(result, Exception):
                        import logging
                        logging.getLogger(__name__).error(
                            f"[AgentRouter] {agent.name} 执行异常: {result}"
                        )
                        continue
                    if result is _SKIP:
                        return True
                    self._merge(ctx, output)

            # 串行阶段
            for agent in serial_agents:
                output = AgentOutput(agent.name)
                result = await agent.execute(ctx, output)
                if result is _SKIP:
                    return True
                self._merge(ctx, output)
        else:
            # 过渡期：全部串行
            for agent in triggered:
                output = AgentOutput(agent.name)
                try:
                    result = await agent.execute(ctx, output)
                except Exception:
                    import logging
                    logging.getLogger(__name__).error(
                        f"[AgentRouter] {agent.name} 执行异常，跳过", exc_info=True
                    )
                    continue
                if result is _SKIP:
                    return True
                self._merge(ctx, output)

        return False

    def _merge(self, ctx: "ChatContext", output: AgentOutput):
        """将 agent 输出合并到 ChatContext。

        子类可重写此方法以支持字段分区校验。
        当前默认实现：遍历 output.data 直接 setattr。
        """
        for key, value in output.data.items():
            if hasattr(ctx, key):
                setattr(ctx, key, value)

    @property
    def registered_agents(self) -> list[str]:
        """返回已注册 agent 名称列表（按优先级排序）。"""
        return [a.name for a in self._agents]

    def get_trigger_matrix(self, ctx: "ChatContext") -> dict:
        """返回当前消息的 trigger 矩阵（用于调试）。

        Returns:
            {agent_name: triggered_bool}
        """
        return {a.name: a.trigger(ctx) for a in self._agents}
