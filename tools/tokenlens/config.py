"""配置管理 — 路径、模型定价、API Keys + 启动验证"""

import os
from pathlib import Path


class Config:
    """全局配置单例"""

    # 数据目录（默认 ~/.claude/projects）
    data_dir: Path = Path.home() / ".claude" / "projects"

    # 时区偏移（默认 UTC+8）
    tz_offset: int = 8

    # LLM 功能开关（摘要 + AI 建议）
    llm_enabled: bool = True

    # LLM 调用超时（秒）
    llm_timeout: int = 10

    # LLM 最大重试次数
    llm_max_retries: int = 2

    @classmethod
    def init(cls, data_dir: str | None = None) -> "Config":
        """根据 CLI 参数覆盖默认配置"""
        if data_dir:
            cls.data_dir = Path(data_dir)
        # 环境变量覆盖
        if os.getenv("TOKENLENS_LLM_ENABLED", "").lower() in ("false", "0", "no"):
            cls.llm_enabled = False
        return cls

    @classmethod
    def validate(cls) -> list[str]:
        """启动时调用，返回警告列表（空列表 = 一切正常）"""
        warnings: list[str] = []

        if not cls.data_dir.exists():
            raise FileNotFoundError(f"数据目录不存在: {cls.data_dir}")
        if not cls.data_dir.is_dir():
            raise NotADirectoryError(f"不是目录: {cls.data_dir}")
        if not os.access(cls.data_dir, os.R_OK):
            raise PermissionError(f"无读权限: {cls.data_dir}")

        # 检测 JSONL 文件数量
        jsonl_count = sum(1 for _ in cls.data_dir.rglob("*.jsonl"))
        if jsonl_count == 0:
            warnings.append("未发现任何 .jsonl 文件，看板将为空")

        if cls.llm_enabled and not os.getenv("DEEPSEEK_API_KEY"):
            warnings.append("DEEPSEEK_API_KEY 未设置，AI 建议/摘要功能将不可用")

        return warnings
