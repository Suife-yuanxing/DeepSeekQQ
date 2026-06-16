"""项目级信号常量。

从 agent_base.py / pipeline.py 提取，消除循环导入。
所有模块可安全导入此文件（零内部依赖）。
"""

# Pipeline 短路哨兵 — stage 返回此值表示跳过后续阶段
_SKIP = object()
