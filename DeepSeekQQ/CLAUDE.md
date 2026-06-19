# DeepSeekQQ 项目指南

## 项目概述
NoneBot2 + DeepSeek LLM 的 QQ 猫娘机器人，通过 OneBot V11 协议连接 NapCat。

## 服务器管理（重要）

Bot 部署在腾讯云轻量服务器，**通过 systemd 管理**：

```bash
# ✅ 正确的重启方式
systemctl restart deepseek-bot

# ❌ 绝对不要这样做！会和 systemd 抢端口 8082 导致循环崩溃
nohup python bot.py &
```

- 服务器区域: `ap-shanghai`（不是 ap-beijing）
- 实例ID: `lhins-n2eeuw4m`
- Bot 日志: `journalctl -u deepseek-bot -f --output=cat`
- 端口: 8082（uvicorn）

## 测试

```bash
python -m pytest tests/ -v    # 1030 个测试（62 个测试文件）
```

## 架构要点

- **Pipeline 架构**: handler.py 定义 22 个有序阶段，每阶段可短路（返回 `_SKIP`）
- **API 二层降级**: DeepSeek 远程 → Ollama 本地模型（含熔断器保护，local_llm.py）
- **记忆系统**: 置信度评分（0.5起步，引用+0.1，每日-0.02），低于 0.15 自动清理
- **情绪引擎**: VA 模型（效价+唤醒度），情绪惯性系数 0.65
- **Token 追踪**: token_tracker.py 记录 API 调用消耗与费用统计
- **错误上报**: error_reporter.py 统一异常收集与上报
- **全局状态**: global_state.py 跨模块共享状态管理
- **私有热度**: private_heat.py 私聊热度计算（原 heat_engine.py 重命名）

## 已知问题

- **天气 API**: 已从和风天气切换到 Open-Meteo（免费无需 Key），2026-06-09 部署
- **重启循环 (已修复)**: 2026-06-09 修复 3 个 crash bug（_http_session UnboundLocalError / sqlite3.Row.get / CancelledError）+ systemd RestartSec 10→60s + StartLimitBurst=5
- **time_validator 小时修正 Bug (已修复)**: 2026-06-18 修复前缀 `都`/`已经` 导致小时数字重复拼接的问题
- **测试覆盖 (已完成)**: 2026-06-18 新增 9 个测试模块（172 测试），覆盖率达到 51/51 实施项

## 部署路径

```
/home/ubuntu/DeepSeekQQ/
├── .venv/                    # Python 虚拟环境
├── bot.py                    # 入口
├── plugins/deepseek/         # 核心插件（~134 模块，含 stages/ 子目录）
│   ├── handler.py            # Pipeline 主处理器（42行，阶段委托至 stages/）
│   ├── stages/               # 22 个 Pipeline 阶段（每阶段独立文件）
│   ├── db_*.py               # 数据库模块（14个：记忆/缓存/群组/情绪/好感度等）
│   └── ...
├── data/
│   ├── chat_memory.db        # SQLite 数据库
│   ├── stickers/             # 表情包（含 downloaded/ 子目录）
│   └── voice/                # 语音缓存
├── tests/                    # 测试套件（~62 文件 / ~1030 测试）
└── tools/
    └── tokenlens/            # Token 用量可视化面板
