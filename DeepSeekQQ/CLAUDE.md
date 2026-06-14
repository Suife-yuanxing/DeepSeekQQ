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
python -m pytest tests/ -v    # 739 个测试，应全部通过
```

## 架构要点

- **Pipeline 架构**: handler.py 定义 22 个有序阶段，每阶段可短路（返回 `_SKIP`）
- **API 二层降级**: DeepSeek 远程 → 友好错误提示（Ollama 本地降级待实现）
- **记忆系统**: 置信度评分（0.5起步，引用+0.1，每日-0.02），低于 0.15 自动清理
- **情绪引擎**: VA 模型（效价+唤醒度），情绪惯性系数 0.65

## 已知问题

- **天气 API**: 已从和风天气切换到 Open-Meteo（免费无需 Key），2026-06-09 部署
- **重启循环 (已修复)**: 2026-06-09 修复 3 个 crash bug（_http_session UnboundLocalError / sqlite3.Row.get / CancelledError）+ systemd RestartSec 10→60s + StartLimitBurst=5

## 部署路径

```
/home/ubuntu/DeepSeekQQ/
├── .venv/                    # Python 虚拟环境
├── bot.py                    # 入口
├── plugins/deepseek/         # 核心插件（28 个文件）
├── data/
│   ├── chat_memory.db        # SQLite 数据库
│   ├── stickers/             # 表情包（含 downloaded/ 子目录）
│   └── voice/                # 语音缓存
└── tests/                    # 测试套件
```
