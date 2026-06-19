# DeepSeekQQ — 林念念

基于 [NoneBot2](https://nonebot.dev/) + DeepSeek LLM 的 QQ 猫娘机器人，通过 OneBot V11 协议连接 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)。

> 🤖 **林念念**是她的名字。21岁，设计专业大学生，养了一只叫团团的猫，住在上海。

## 功能概览

| 模块 | 说明 |
|------|------|
| 💬 **智能聊天** | 22阶段 Pipeline 架构，消息分级（简单/普通/复杂），支持情绪驱动回复 |
| 🎭 **情绪系统** | VA 模型（效价+唤醒度），情绪惯性 0.65，影响回复温度/长度/表情包概率 |
| 🧠 **记忆系统** | 置信度评分机制（0.5起步，引用+0.1，日衰减-0.02），低于0.15自动清理 |
| 🎤 **语音** | 百度TTS / MiMo TTS / 火山引擎TTS，支持语音通话模式 |
| 🔊 **语音识别** | MiMo STT，收到语音消息自动转文字 |
| 🎵 **音乐** | 点歌/推荐/歌词/XML卡片 |
| 🖼️ **图像识别** | GLM-4V-Flash 视觉识别 + 分类 + OCR |
| 🎨 **图片生成** | Agnes AI 图片生成 |
| 😂 **表情包** | 四维分类管理 + 语义搜索 + 上下文匹配 |
| 📎 **链接解析** | 抖音/B站/微博/知乎/小红书/小黑盒等平台分享内容抓取 |
| 🌤️ **天气** | Open-Meteo 免费天气查询 |
| 🔍 **联网搜索** | Tavily 搜索，复杂问题自动检索 |
| ⏰ **提醒** | 定时提醒创建/查询/取消 |
| 📱 **手机控制** | MobileRun Portal 桥接，远程控制手机（截图/打开应用/滑动等） |
| 💝 **好感度** | 7级好感度系统，影响回复语气和行为 |
| 🌅 **早安晚安** | 定时早晚安 + 节日问候 + 睡眠催促 |
| 🎲 **随机行为** | 微事件/热点推送/主动搭话，让 bot 更像真人 |
| 🛡️ **安全防护** | 提示词注入检测（10+模式）+ 速率限制 + 滥用检测 |
| 🌐 **Web管理** | 内置管理后台 |

## 快速开始

### 环境要求
- Python 3.11+
- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)（OneBot V11 反向 WebSocket）
- DeepSeek API Key

### 安装

```bash
git clone https://github.com/Suife-yuanxing/DeepSeekQQ.git
cd DeepSeekQQ
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .
```

### 配置

复制 `.env.example` 为 `.env`，填入必要配置：

```bash
cp .env.example .env
```

**必填项**：
- `deepseek_api_key` — DeepSeek API 密钥
- `my_qq` — 主人 QQ 号（bot 只回复此人私聊）

其他按需配置（语音/图片生成/天气/搜索等）。

### 运行

```bash
python bot.py
```

## 测试

```bash
python -m pytest tests/ -v
```

共 ~1030 个测试（62 个测试文件），包含单元测试和集成测试。

测试分类标签：
- `unit` — 纯逻辑，无 I/O
- `integration` — 需要真实服务（如 SQLite :memory:）
- `slow` — 含 sleep
- `needs_db` / `needs_llm` / `needs_network` — 需要对应资源

## 架构

```
消息输入 → Pipeline（22阶段）→ 回复输出
              │
              ├─ security          # 安全扫描
              ├─ voice_recognition # 语音识别
              ├─ share_extract     # 链接解析
              ├─ context_analysis  # 上下文/情绪分析
              ├─ llm_call          # LLM 调用
              ├─ mcp_execute       # 工具调用
              ├─ humanize          # 真人化处理
              └─ post_process      # 发送回复

API 降级：DeepSeek 远程 → Ollama 本地模型（含熔断器保护）
数据库：SQLite (WAL 模式) + 14版本迁移
Pipeline 阶段：handler.py（42行入口） + stages/ 目录（22个独立阶段文件）
辅助模块：error_reporter（错误上报）/ global_state（全局状态）/ token_tracker（Token统计）
```

## 部署

项目部署在腾讯云轻量服务器（上海），通过 systemd 管理：

```bash
systemctl restart deepseek-bot   # 重启
journalctl -u deepseek-bot -f    # 查看日志
```

注意：不要用 `nohup python bot.py &`，会和 systemd 抢端口。

## 项目结构

```
DeepSeekQQ/
├── bot.py                    # 入口
├── plugins/deepseek/         # 核心插件（~134模块）
│   ├── handler.py            # Pipeline 主处理器
│   ├── stages/               # 22 个 Pipeline 阶段
│   ├── api.py                # LLM API 调用层
│   ├── prompt.py             # 系统提示词构建
│   ├── memory.py             # 记忆系统
│   ├── memory_embed.py       # 语义向量化检索
│   ├── voice.py              # TTS 语音合成
│   ├── share_parser.py       # 分享链接解析
│   ├── time_validator.py     # 时间合理性校验
│   ├── token_tracker.py      # API Token 用量追踪
│   ├── error_reporter.py     # 错误收集上报
│   ├── global_state.py       # 全局状态管理
│   └── ...
├── tests/                    # 测试套件（~62文件/~1030测试）
├── data/                     # 运行时数据
│   ├── stickers/              # 表情包
│   └── persona/              # 人设文件
├── scripts/                  # 工具脚本
└── tools/
    └── tokenlens/            # Token 用量可视化面板
```

## 已知限制

- Web 管理后台为基础版本
- 部分边缘场景仍需完善测试覆盖

## 更新日志

- **2026-06-18**: 完成实施计划全部 51 项，新增 9 个测试模块（172 测试），修复 time_validator 小时修正 bug + CI working-directory
- **2026-06-09**: 天气 API 迁移 Open-Meteo，修复重启循环问题，handler.py 拆分至 stages/

## License

MIT
