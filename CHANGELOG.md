# DeepSeekQQ 项目状态报告

> 生成时间：2026-06-06 21:10

---

## 一、项目概况

基于 NoneBot2 的 QQ 猫娘聊天机器人，使用 DeepSeek API 作为 LLM 后端，运行在腾讯云轻量应用服务器（上海），通过 NapCat 连接 QQ。

| 项目 | 值 |
|------|-----|
| 服务器 | lhins-n2eeuw4m (4C4G, 上海) |
| 公网 IP | 127.211.7.67 |
| QQ 账号 | 3033578949（喵喵） |
| 主人 QQ | 2938897660 |
| GitHub | https://github.com/Suife-yuanxing/DeepSeekQQ.git |
| 分支 | main |
| 最新提交 | 5013f55 |

---

## 二、本次重构内容

### 🔴 严重 Bug 修复

| # | 问题 | 文件 | 状态 |
|---|------|------|------|
| 1 | `_should_quote` 缩进错误导致群聊引用回复完全失效 | handler.py:254-268 | ✅ 已修复 |

### 🟡 中等问题修复

| # | 问题 | 文件 | 状态 |
|---|------|------|------|
| 2 | `proactive_log` 表 schema 缺少 `scene` 列 | database.py:139 | ✅ 已修复 |
| 3 | `call_lighthouse.py` 硬编码 sleep 改动态读取 | call_lighthouse.py | ✅ 已修复 |
| 4 | `memory.py` 清理未使用的 `AnalysisResult` 导入 | memory.py:30 | ✅ 已修复 |
| 5 | `context_analyzer.py` 去除 `__import__` hack | context_analyzer.py:276 | ✅ 已修复 |
| 6 | `sticker.py` 全局缓存字典添加上限保护 | sticker.py:226 | ✅ 已修复 |

### 🟢 轻微优化

| # | 问题 | 文件 | 状态 |
|---|------|------|------|
| 7 | `call_lighthouse.js` 改为事件驱动 | call_lighthouse.js | ✅ 已修复 |
| 8 | `config.py` MY_QQ 默认值不再硬编码 | config.py:44 | ✅ 已修复 |
| 9 | 好感度衰减 SQL 加 `private_` 前缀过滤 | database.py:1062 | ✅ 已修复 |

### 架构重构

#### 熔断器

| 文件 | 说明 |
|------|------|
| `circuit_breaker.py` | 统一 API 熔断器，支持 closed/open/half_open 三态 |

#### database.py 拆分（1228 行 → 9 个子模块 + 1 个 facade）

| 文件 | 职责 | 行数 |
|------|------|------|
| `db_core.py` | 连接池 + WAL | ~40 |
| `db_memories.py` | 对话记忆 CRUD | ~100 |
| `db_affection.py` | 好感度 + 里程碑 + 衰减 | ~180 |
| `db_mood.py` | bot/user/catgirl 情绪 | ~110 |
| `db_tags.py` | 记忆标签 + 置信度 | ~105 |
| `db_session.py` | 会话状态 + 用户画像 + 披露 | ~200 |
| `db_reminders.py` | 提醒 CRUD | ~80 |
| `db_preferences.py` | 偏好 + 质量评估 | ~105 |
| `db_proactive.py` | 主动消息日志 | ~75 |
| `db_cache.py` | 文章缓存 | ~30 |
| `database.py` | Facade（所有外部 import 不变）| ~200 |

#### handler.py 拆分（980 行 → 3 个模块）

| 文件 | 职责 | 行数 |
|------|------|------|
| `handler_helpers.py` | 引用决策 + 问候检测 + 消息分析 | ~165 |
| `handler_humanize.py` | 拟人化（错别字/犹豫/不确定） | ~60 |
| `handler.py` | Pipeline 主体 + 入口 | ~580 |

---

## 三、测试结果

```
228 passed, 2 failed (pre-existing), 2 warnings
```

### 新增测试

| 测试文件 | 用例数 | 状态 |
|----------|--------|------|
| `test_handler_helpers.py` | 27 | ✅ 全部通过 |
| `test_handler_humanize.py` | 8 | ✅ 全部通过 |
| `test_circuit_breaker.py` | 8 | ✅ 全部通过 |
| `test_sticker_v2.py` | 8 | ✅ 全部通过 |
| `test_stt.py` | 11 | ✅ 全部通过 |

### 已有测试（无回归）

| 测试文件 | 状态 |
|----------|------|
| `test_security.py` | ✅ 通过 |
| `test_emotion_params.py` | ✅ 通过 |
| `test_memory.py` | ✅ 通过 |
| `test_migrations.py` | ✅ 通过 |
| `test_loop_manager.py` | ✅ 通过 |
| `test_image_gen.py` | ✅ 通过 |
| `test_phone_control.py` | ✅ 通过 |
| `test_prompt.py` | ✅ 通过 |
| `test_plugin_manager.py` | ✅ 通过 |
| `test_api_override.py` | ✅ 通过 |
| `test_proactive_p1.py` | ⚠️ 1 个已有失败 |
| `test_user_prefs.py` | ⚠️ 1 个已有失败 |

---

## 四、服务器状态

### 运行状态

| 组件 | 状态 | PID |
|------|------|-----|
| NoneBot (bot.py) | ✅ 运行中 | 2723645 |
| QQ + NapCat | ✅ 已连接 | 2728126 |
| ffmpeg | ✅ 已安装 | 6.1.1 |
| WebSocket | ✅ 已连接 | Bot 3033578949 |

### 日志关键行

```
21:09:48 [INFO] uvicorn | WebSocket /onebot/v11/ws [accepted]
21:09:48 [INFO] nonebot | OneBot V11 | Bot 3033578949 connected
21:09:48 [INFO] websockets | connection open
```

### 后台任务

| 任务 | 间隔 | 状态 |
|------|------|------|
| 主动消息注册 | 86400s | ✅ |
| 分享缓存清理 | 3600s | ✅ |
| 表情包缓存清理 | 86400s | ✅ |
| WAL checkpoint | 7200s | ✅ |
| 提醒检查 | 30s | ✅ |
| 记忆维护 | 86400s | ✅ |
| 好感度衰减 | 86400s | ✅ |
| 图片缓存清理 | 3600s | ✅ |

---

## 五、功能配置

| 功能 | 状态 | 备注 |
|------|------|------|
| DeepSeek API | ✅ | 主力 LLM |
| Ollama 本地降级 | ✅ | 离线时自动切换 |
| 语音发送 (TTS) | ✅ | MiMo 引擎 |
| 语音识别 (STT) | ✅ | 百度 API + ffmpeg |
| 联网搜索 | ✅ | Tavily API |
| 天气查询 | ✅ | 和风天气 API |
| 图片生成 | ✅ | Agnes AI |
| 表情包 | ✅ | 本地+联网检索 |
| 备忘录/提醒 | ✅ | 自然语言创建 |
| 主动消息 | ✅ | 早安/晚安/沉默/节日/催睡 |
| 手机控制 | ✅ | ADB + ScreenMCP |
| 安全防护 | ✅ | 注入检测+频率限制 |

---

## 六、文件结构

```
DeepSeekQQ/
├── bot.py                    # NoneBot 入口
├── call_lighthouse.py        # MCP 工具调用（Python）
├── call_lighthouse.js        # MCP 工具调用（Node）
├── CHANGELOG.md              # 本文件
├── plugins/deepseek/
│   ├── __init__.py           # 插件入口
│   ├── handler.py            # Pipeline 主体
│   ├── handler_helpers.py    # 引用决策/问候检测
│   ├── handler_humanize.py   # 拟人化
│   ├── config.py             # 统一配置
│   ├── api.py                # DeepSeek API + 三级降级
│   ├── database.py           # DB Facade
│   ├── db_core.py            # 连接池
│   ├── db_memories.py        # 对话记忆
│   ├── db_affection.py       # 好感度
│   ├── db_mood.py            # 情绪系统
│   ├── db_tags.py            # 记忆标签
│   ├── db_session.py         # 会话状态
│   ├── db_reminders.py       # 提醒
│   ├── db_preferences.py     # 偏好
│   ├── db_proactive.py       # 主动消息日志
│   ├── db_cache.py           # 文章缓存
│   ├── memory.py             # 记忆系统
│   ├── context_analyzer.py   # 上下文+情绪分析
│   ├── prompt.py             # Prompt 构建
│   ├── security.py           # 安全模块
│   ├── circuit_breaker.py    # 熔断器
│   ├── voice.py              # TTS
│   ├── voice_mimo.py         # MiMo TTS
│   ├── stt.py                # STT 语音识别
│   ├── search.py             # 联网搜索
│   ├── sticker.py            # 表情包
│   ├── sticker_search.py     # 表情包联网
│   ├── reminder.py           # 提醒模块
│   ├── proactive.py          # 主动消息
│   ├── hot_topics.py         # 热搜推送
│   ├── world_context.py      # 天气
│   ├── image_gen.py          # 图片生成
│   ├── media.py              # 媒体处理
│   ├── share_parser.py       # 分享解析
│   ├── share_prompt.py       # 分享 Prompt
│   ├── vision.py             # 视觉识别
│   ├── phone_adb.py          # ADB 手机控制
│   ├── phone_control.py      # ScreenMCP 控制
│   ├── screenmcp_worker.py   # ScreenMCP Worker
│   ├── plugin_manager.py     # 插件管理
│   ├── loop_manager.py       # 循环任务管理
│   ├── startup.py            # 启动/关闭钩子
│   ├── migrations.py         # 数据库迁移
│   ├── utils.py              # 工具函数
│   └── meme_lexicon.py       # 网络梗词典
├── tests/
│   ├── conftest.py           # 测试 Mock
│   ├── test_handler_helpers.py
│   ├── test_handler_humanize.py
│   ├── test_circuit_breaker.py
│   ├── test_sticker_v2.py
│   ├── test_stt.py
│   └── ... (17 个测试文件)
├── scripts/
│   └── watchdog.sh           # 进程守护
└── data/
    ├── stickers/             # 表情包
    └── voice/                # 语音文件
```
