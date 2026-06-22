# DeepSeekQQ 项目状态报告

> 生成时间：2026-06-06 21:10　｜　最后更新：2026-06-21

---

## 零、UI 原型进度（2026-06-21 更新）

安卓控制面板 UI 原型位于 [`安卓控制面板UI原型/`](../安卓控制面板UI原型/)，**21 个页面**，路由覆盖 18/19。

| 阶段 | 日期 | 进展 |
|------|------|------|
| v5 初始 | 06-20 | 7 页核心原型（索引/品牌色/Bot创建/首页/聊天/API Key/数据面板） |
| v5 补充 | 06-20 | +5 页（启动/登录/注册/Bot设置/我的Bot）+ 品牌色修正 #E85D75 + 手机框 360×800 |
| v5 子页面 | 06-20 | +3 页（修改密码/数据权限/黑名单）+ Apple Push 过渡动画 + 统一圆角 |
| v6 审计 | 06-21 | +3 页（编辑资料/通知/品牌色预览合并）+ 主题同步 localStorage + 去 emoji |
| v6 扩展 | 06-21 | +3 法律文档（用户协议/隐私政策/开源许可）+ 字号弹窗 + 通知铃声(含自定义导入) + QQ/微信官方品牌 SVG + 设置页独立圆角卡片 + 首页 Bot 卡片直链多 Bot 管理 |
| v7 猫娘CSS v4 | 06-21 | anime-cat 全量 CSS v3→v4 重构（8 页）：Claymorphism × Macaron Pastel 设计系统、新增光环(::before)、眼睛放大+双层高光、瓷白肌肤渐变、软粉薰衣草耳、扩散马卡龙腮红、柔和玫瑰嘴线、同步 6 种人格变体色值、修复启动页 h1{h1{ CSS 语法错误、零 HTML 变更(纯 CSS 实现) |
| v8 共享资源重构 | 06-21 | **全部 21 页 UI 审计 + 修复 + 共享资源提取**。消除 ~4,000 行代码重复：提取 `shared/` 目录含 6 个 CSS（tokens/base/components/anime-cat/dark-mode/effects）+ 1 个 JS（app.js：涟漪/主题同步/导航弹簧/Toast）；21 页全部链接共享资源，removed 重复代码；暗色模式全面补全（6→21 页，100% 覆盖）；组件标准化（.btn-primary / .input-field / .card-glass / .bottom-nav / .toggle-switch）；补注册页 anime-cat、Bot设置页 anime-cat；导航改为 `<a href>` 语义化；总行数 ~6,500（共享 ~1,400 + 页面 ~5,100） |
| v9 第四版设计系统 | 06-21 | **Ollama 视觉模型部署 + tokens.css v3（马卡龙四版色系）**。安装 Ollama v0.30.9 + 拉取 moondream:1.8b 视觉模型（1.7GB）；用 moondream 分析 4 张 Agnes 参考图（gen_01-04）确认马卡龙/LINE贴纸/浅灰背景设计方向；tokens.css v2→v3 核心色值全面升级：主色 `#E85D75`→`#F472B6`（软粉）、辅色 `#7C5CBF`→`#ADD8E6`（婴儿蓝）、强调色 `#FFB347`→`#C4B5FD`（薰衣草）、成功色 `#4CAF50`→`#98FF98`（薄荷绿）；body 渐变从粉色 `#FFE0E8→#FFD4DC` 改为浅灰中性 `#F0F0F0→#E8E8E8`；页面底色 `#FFF5F7`→`#F5F5F5`；阴影从粉色调改为中性灰调；输入框聚焦环从粉改为婴儿蓝；完整 v3 token 体系含 14 组渐变/5 级圆角/6 级阴影/6 级间距；Ollama 模型路径 `~/.ollama/`，按需启动（非 Windows 服务） |
| v10 导航重构+个人中心 | 06-21 | **底部导航重构 + 个人中心上线**。导航栏从"首页/聊天/我的Bot/数据"改为 **首页/聊天/Bot管理/我的**；新增 `我的.html` 合并原设置页全部内容+账户卡片+快捷入口；首页仪表盘移除设置齿轮；"我的Bot"全站更名为"Bot管理"；"我的"导航图标重绘为 ID 卡片风格（圆角矩形含人物剪影）；快捷入口去图标纯文字；7 个主导航页面移除左上角返回箭头；8 个子页面返回链接从设置.html→我的.html |
| v11 聊天输入+同步+细节打磨 | 06-21 | **聊天输入 ChatGPT 风格改造**：输入栏改为统一白色胶囊（border-radius 26px）、无边框透明 textarea、暗色圆形发送按钮（上箭头图标）、双层微阴影、浮动 margin；**背景同步全局化**：背景色同步从 4 页面补全至 shared/app.js 实现全站 25 页覆盖 + storage 事件跨标签实时同步；**通知页圆角统一**：flush 拼接列表项 → 独立圆角卡片（border-radius 16px + margin-bottom 8px）；**编辑资料性别自定义**：第三选项"保密"→"自定义"+ 条件输入框 + 模板标签（沃尔玛购物袋、武装直升机）；**API Key 管理三修**：表单透明度 0.8→0.97+blur(24px) 消除穿模、权限复选框垂直对齐+中文解释（聊天对话/图像生成/语音合成/记忆存储）、新增 API Key 输入框供用户粘贴自有 Key |

---

## 一、项目概况

基于 NoneBot2 的 QQ 猫娘聊天机器人，使用 DeepSeek API 作为 LLM 后端，运行在腾讯云轻量应用服务器（上海），通过 NapCat 连接 QQ。

| 项目 | 值 |
|------|-----|
| 服务器 | 腾讯云轻量 (4C4G, 上海) |
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
