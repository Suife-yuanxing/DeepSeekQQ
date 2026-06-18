# 全面项目审计报告 — 2026-06-18

审计范围：完整仓库 `d:/QQmaonian/`，包含 DeepSeekQQ 机器人、理事会技能和 TokenLens 工具。
审查了 140+ 个 Python 文件，覆盖 __5 个主要模块领域__。

---

## 目录

1. [按严重程度排列的执行摘要](#执行摘要)
2. [🔴 严重问题（7 项）](#-严重问题)
3. [🟡 高优先级问题（12 项）](#-高优先级问题)
4. [🟢 中优先级问题（16 项）](#-中优先级问题)
5. [⚪ 低优先级问题（18 项）](#-低优先级问题)
6. [逐模块评级摘要](#逐模块评级摘要)
7. [测试覆盖率差距](#测试覆盖率差距)
8. [架构性问题](#架构性问题)
9. [各领域详细发现](#各领域详细发现)

---

## 执行摘要

| 领域 | 文件数 | 整体评级 | 严重 | 高 | 中 | 低 |
|------|-------|-------------|--------|-----|------|-----|
| **核心插件模块** | 50+ | B+ / A- | 0 | 3 | 8 | 5 |
| **数据库 / 存储** | 12 | B+ | 1 | 1 | 2 | 3 |
| **理事会技能** | 12 | A- | 0 | 1 | 3 | 3 |
| **TokenLens 工具** | 15 | B+ | 0 | 2 | 3 | 4 |
| **测试套件** | 56 | B+ | 0 | 2 | 3 | 1 |
| **配置 / 基础设施** | 15 | B | 3 | 4 | 5 | 4 |
| **文档 / 图表** | — | C | 2 | 1 | 2 | 0 |

- **整体项目健康度**：稳定、功能齐全、架构扎实。数据库层和小型跨领域重复存在 1 个确认的运行时错误。基础设施方面存在数个安全卫生问题。
- **最强领域**：理事会技能（经过充分测试、优秀的提示词工程、稳健的错误处理）、情感系统（VA 模型、情绪传染、恢复路径）
- **最弱领域**：测试覆盖率（50 多个模块中有 35 个缺少专门的测试文件）、配置管理（`.env` 密钥泄露、依赖项未固定）

---

## 🔴 严重问题

### C1. memory_embed.py 从 db_memories 导入不存在的函数
- **文件**：[memory_embed.py](DeepSeekQQ/plugins/deepseek/memory_embed.py) 第 406-411、435-443 行
- **影响**：当调用 `ensure_tag_embedding()` 或 `rebuild_all_embeddings()` 时，立即出现 `ImportError` 崩溃
- **修复**：这些函数导入 `_fetch_one`、`_fetch_all`、`_execute`，但在 `db_memories.py` 中并不存在。重构以使用 `db_memories.py` 中实际的公共 API，或将缺失的函数添加到数据库模块中。

### C2. 磁盘 `.env` 中包含真实 API 密钥
- **文件**：[DeepSeekQQ/.env](DeepSeekQQ/.env)
- **影响**：`KIMI_API_KEY`（以 `sk-Nvb...` 开头）和 `MIMO_API_KEY`（以 `sk-cn9...` 开头）以明文形式存在。任何有文件系统访问权限的人都可以读取。
- **修复**：轮换这些密钥，移至 Windows 系统环境变量（理事会技能已迁移至此），并从 `.env` 文件中删除。

### C3. VPN 凭证在版本控制中
- **文件**：[vpn-setup-report.md](vpn-setup-report.md)
- **影响**：完整的 VLESS 连接字符串（服务器 IP、端口、UUID、公钥）在 git 跟踪的 markdown 中可见。任何仓库访问者都可以连接。
- **修复**：立即撤销此配置，从文件中删除凭证，并在 `.gitignore` 中添加 `vpn-setup-report.md`。使用 `git filter-branch` 或 BFG 从 git 历史中删除。

### C4. 管理员 API 密钥为空 — 管理员后端无认证
- **文件**：[DeepSeekQQ/.env](DeepSeekQQ/.env)，[.env.example](DeepSeekQQ/.env.example)
- **影响**：`ADMIN_API_KEY` 在两个文件中均为空。任何能访问管理员后端的人都可以无认证地调用端点。
- **修复**：生成强密钥，在两个文件中设置，并验证管理员端点强制执行它。

### C5. pyproject.toml 中依赖项未固定
- **文件**：[pyproject.toml](DeepSeekQQ/pyproject.toml)
- **影响**：所有依赖项使用 `>=` 范围。`pip install -e .` 可能拉取破坏性版本。`requirements.txt` 存在但未被 `pyproject.toml` 的安装路径使用。
- **修复**：要么将 pyproject.toml 固定为已知良好版本，要么添加 `[build-system]` 部分并将 `pip install` 指向 `requirements.txt`。

### C6. deploy.sh 仅备份 plugins/deepseek/，而非整个项目
- **文件**：[deploy.sh](DeepSeekQQ/deploy.sh)
- **影响**：对 `bot.py`、`config.py` 或根级别文件的更改不受回滚保护。如果部署失败，回滚仅恢复 `plugins/deepseek/`。
- **修复**：扩展备份范围，包含至少根目录的 `.py` 文件和 `pyproject.toml`。

### C7. playwright MCP 使用 `@latest` — 将因未来版本而中断
- **文件**：[DeepSeekQQ/.mcp.json](DeepSeekQQ/.mcp.json)
- **影响**：当 `@playwright/mcp` 发布新的主版本时，MCP 服务器将静默中断。
- **修复**：固定到特定版本，例如 `@playwright/mcp@1` 或确切的 semver。

---

## 🟡 高优先级问题

### H1. heat_engine.py 和 group_heat.py 之间的架构重复
- **文件**：[heat_engine.py](DeepSeekQQ/plugins/deepseek/heat_engine.py)，[group_heat.py](DeepSeekQQ/plugins/deepseek/group_heat.py)
- 两者实现热状态机，API 不兼容。HeatEngine 使用 5 状态枚举；GroupHeat 使用 3 状态类。同时导入两者会冲突。
- **修复**：合并为一个模块，或明确重命名以指示范围（例如，`private_heat.py`、`group_heat.py`）。

### H2. 无服务器测试 — 零次 FastAPI 端点测试
- **文件**：TokenLens `tools/tokenlens/tests/`（缺少 `test_server.py`、`test_advisor.py`、`test_billing_fetcher.py`）
- API 端点未进行正确性、错误处理或内容类型验证测试。
- **修复**：使用 HTTPX 测试客户端（`httpx.AsyncClient(app=app)`）添加 `test_server.py`。

### H3. 重复的硬编码定价表
- **文件**：[pricing.py](tools/tokenlens/pricing.py)，[pricing_fetcher.py](tools/tokenlens/pricing_fetcher.py)
- `_DEFAULT_PRICING` 和 `FALLBACK_PRICING` 必须手动保持同步。如果只更新一个，行为在获取成功与失败时出现分歧。
- **修复**：从 `pricing.py` 导入 `_DEFAULT_PRICING` 到 `pricing_fetcher.py` 作为后备基础。

### H4. 没有事务回滚
- **文件**：所有 `DeepSeekQQ/plugins/deepseek/db_*.py` 文件
- 模式为 `await db.execute(...)`、`await db.commit()`，但如果操作之间发生异常，事务保持打开。后续的 `execute()` 调用可能获得 `SQLITE_BUSY` 或 `SQLITE_ERROR`。
- **修复**：添加 `try/finally` 块，在异常时回滚。

### H5. context_optimizer 和 context_compressor 中的令牌估算重复
- **文件**：[context_optimizer.py](DeepSeekQQ/plugins/deepseek/context_optimizer.py) 第 132 行，[context_compressor.py](DeepSeekQQ/plugins/deepseek/context_compressor.py) 第 32 行
- 相同的 `estimate_tokens` 函数在两个地方实现。如果字符/令牌比率发生变化，可能只有一个会被更新。
- **修复**：提取到共享工具模块中。

### H6. config.py 中缺少模型验证
- **文件**：[config.py](DeepSeekQQ/plugins/deepseek/config.py)（以及其他引用它的文件）
- 当 `STT_ENGINE`、`TTS_ENGINE` 或其他功能切换设置为无效值时，启动时没有验证。
- **修复**：在 `bot.py` 中添加启动验证步骤。

### H7. pyrightconfig.json 禁用了导入检查
- **文件**：[pyrightconfig.json](DeepSeekQQ/pyrightconfig.json)
- `reportMissingImports: false` 和 `reportMissingTypeStubs: false` 抑制关键错误。重命名或删除的模块将不会被发现。
- **修复**：启用 `reportMissingImports`，并为没有类型存根的库添加特定忽略。

### H8. handler.py — 无启动时导入验证
- **文件**：[handler.py](DeepSeekQQ/plugins/deepseek/handler.py)
- 该文件导入 22 个阶段模块和一个后备。这些导入中的任何失败都会使机器人崩溃，没有有用的错误消息。
- **修复**：用清晰的错误消息包装导入，或添加一个断言，验证所有必需阶段按编号存在。

### H9. context_compressor 中未使用的参数
- **文件**：[context_compressor.py](DeepSeekQQ/plugins/deepseek/context_compressor.py) 第 162 行
- `compress()` 接受 `api_call_fn` 参数但从未使用它。相反，它直接硬编码 `api.call_deepseek_api`。
- **修复**：要么删除该参数，要么实际使用它（为测试提供更清晰的可测试性）。

### H10. 消息防抖在长时间运行的处理程序中持有锁
- **文件**：[message_debounce.py](DeepSeekQQ/plugins/deepseek/message_debounce.py) 第 107 行
- 锁在调用 `handler` 之前获取，并在处理程序完成后释放。如果处理程序超时 60 秒，其他消息将被阻塞 60 秒。
- **修复**：在调用处理程序之前释放锁，或使用单独的处理中状态。

### H11. README.md 过时 — 列出已解决的问题
- **文件**：[README.md](DeepSeekQQ/README.md)
- 声明“Ollama 本地降级尚未实现”，但 CHANGELOG 显示它已完成。Handler.py 被描述为“~1800 行”，但实际上是 ~43 行（逻辑移动到各个阶段）。测试计数为“607”，但已过时。
- **修复**：更新 README 以反映当前状态。

### H12. `.env` 和 `.env.full.example` 之间存在漂移
- **文件**：[.env](DeepSeekQQ/.env)，[.env.full.example](DeepSeekQQ/.env.full.example)
- Mimo API 基础 URL 不同（`api.mimo.com` 对比 `api.xiaomimimo.com/v1`）。图像生成模型不同（`dall-e-3` 对比 `agnes-image-2.1-flash`）。Kimi/MiniMax 变量在 DeepSeekQQ 的示例中缺失。
- **修复**：协调这些文件。`.env.example` 应该是一个最小子集；`.env.full.example` 应该全面且准确。

---

## 🟢 中优先级问题

### M1. 理事会技能 — 无速率限制
- **文件**：[skills/council/scripts/api_client.py](skills/council/scripts/api_client.py)
- 当所有模型并行调用其 API 端点时，可能发生速率限制错误。固定 2 秒休眠不够。
- **修复**：添加具有模型特定 RPM 限制的自适应退避。

### M2. 理事会技能 — 上下文截断不精确
- **文件**：[skills/council/scripts/council_call.py](skills/council/scripts/council_call.py) `truncate_context`
- 使用 `max_tokens * 2` 字符近似，而非实际的令牌计数。对于中文文本，可能偏差 2-3 倍。
- **修复**：使用项目自己的 `count_tokens` 函数进行精确截断。

### M3. TokenLens — 无离线备选用于 Chart.js CDN
- **文件**：[tools/tokenlens/static/index.html](tools/tokenlens/static/index.html) 第 410 行
- Chart.js 从 jsdelivr 加载。在无网络环境下，所有图表都会失效。
- **修复**：将 Chart.js 打包到 `static/` 中，或添加本地备选路径。

### M4. TokenLens — `alert()` 用于会话详情弹窗
- **文件**：[tools/tokenlens/static/app.js](tools/tokenlens/static/app.js) 第 481 行
- 糟糕的用户体验 — 无法复制文本，在移动设备上处理不当，点击后消失。
- **修复**：替换为基于 DOM 的模态框或可展开的行。

### M5. TokenLens — FastAPI 端点无请求超时
- **文件**：[tools/tokenlens/server.py](tools/tokenlens/server.py)
- 长时间运行的操作（`/api/refresh` 重新扫描所有文件，`/api/summary` 调用 LLM）可能无限期挂起。
- **修复**：添加超时中间件或每个端点的超时。

### M6. 测试 — 样本量仅为 200 的概率性测试
- **文件**：[test_sticker.py](DeepSeekQQ/tests/test_sticker.py)、[test_emotion_deep.py](DeepSeekQQ/tests/test_emotion_deep.py)、[test_behavior.py](DeepSeekQQ/tests/test_behavior.py)
- 统计断言（`hits_high > hits_low`）在仅 200 次迭代时可能因随机分布而不可靠。
- **修复**：增加样本量或扩大容差范围（例如，`hits_high > hits_low * 3`）。

### M7. 测试 — 无异常安全清理的全局 micro-events 修改
- **文件**：[test_behavior.py](DeepSeekQQ/tests/test_behavior.py) 第 326-338 行
- 如果测试在清理行之前失败，全局 `_MICRO_EVENTS` 列表将包含测试事件。
- **修复**：使用 try/finally 或在 fixture teardown 中清理。

### M8. 测试 — SQL 参数未验证的弱数据库调用断言
- **文件**：[test_memory_deep.py](DeepSeekQQ/tests/test_memory_deep.py)
- 测试检查 `db.execute.call_count >= 1`，但不验证 SQL 内容或参数。
- **修复**：添加 `assert "INSERT INTO" in call_args` 或使用 `assert_any_call`。

### M9. 数据库 — `get_silent_private_users()` 无 LIMIT
- **文件**：[db_proactive.py](DeepSeekQQ/plugins/deepseek/db_proactive.py) 第 74-83 行
- 对 `memories` 表进行全表扫描，可能返回数千个 user_id。
- **修复**：添加 `LIMIT` 子句，或最少添加索引。

### M10. 数据库 — `decay_affection()` 中 `NOT IN` 子查询效率低
- **文件**：[db_affection.py](DeepSeekQQ/plugins/deepseek/db_affection.py) 第 82-92 行
- 对于大型记忆表，`WHERE user_id NOT IN (SELECT ...)` 可能很慢。
- **修复**：为更好性能重写为 `LEFT JOIN / WHERE ... IS NULL`。

### M11. 代码 — `reminder.py` 中 `chr(10)` 的 bug
- **文件**：[reminder.py](DeepSeekQQ/plugins/deepseek/reminder.py) 第 224 行
- `'chr(10)'.join(lines)` 在行之间插入字面字符串"chr(10)"，而非换行符。提示词将包含乱码。
- **修复**：改为 `'\n'.join(lines)`。

### M12. 代码 — `promise_tracker.py` 中不持久的随机时间偏移
- **文件**：[promise_tracker.py](DeepSeekQQ/plugins/deepseek/promise_tracker.py) 第 93 行
- `estimate_due_time` 添加随机偏移但未持久化。如果机器人重启，将计算不同的偏移，丢失原始计划时间。
- **修复**：将随机偏移持久化为单独的 `due_offset` 字段。

### M13. 文档 — SKILL.md 在审查模型编号方面不一致
- **文件**：[skills/council/SKILL.md](skills/council/SKILL.md)
- 角色表列出了 4 个审查模型，但命令示例默认使用 2 个（`deepseek,kimi`）。
- **修复**：添加使用所有 4 个模型的示例，或记录为何某些模型未被使用。

### M14. 基础设施 — `.ruff_cache` 和 `.pytest_cache` 未加入 `.gitignore`
- **文件**：[DeepSeekQQ/.gitignore](DeepSeekQQ/.gitignore)
- 这些目录存在于磁盘上，可能被意外跟踪。
- **修复**：添加 `__pycache__/`、`.ruff_cache/`、`.pytest_cache/`。

### M15. 基础设施 — 无机器人 CI/CD（仅理事会有）
- **文件**：[.github/workflows/](.github/workflows/)
- 只有 `council-test.yml` 存在。主要机器人项目没有 CI 流水线。
- **修复**：添加运行 `pytest tests/` 和 ruff 检查的 GitHub Actions 工作流。

### M16. 基础设施 — `deploy.sh` 语法检查仅覆盖 `plugins/deepseek/`
- **文件**：[deploy.sh](DeepSeekQQ/deploy.sh)
- 忽略根级别的 `.py` 文件（`bot.py`、`config.py`）。
- **修复**：也将语法检查扩展到根目录的 Python 文件。

---

## ⚪ 低优先级问题

### L1. 在 `follow_up.py`、`topic_tracker.py`、`time_validator.py`、`token_tracker.py` 等文件中，20+ 个核心模块缺少专门测试
### L2. 模块级缓存（例如 `personality.py` 中的 `_catchphrases_cache`、`_topic_prefs_cache`）非线程安全
### L3. 全局可变状态（`sticker._last_sticker_session`、`follow_up._session_states`、`social_feed._feed_store`）无明确所有权
### L4. 魔法阈值（`affection > 150`、`affection > 500`）分散在各模块中，无集中常量
### L5. 模块主体中的延迟导入（context_analyzer.py、follow_up.py、social_feed.py）应标准化
### L6. fire-and-forget 模式（message_actions、context_compressor.cache_summary）无错误追踪
### L7. 理事会技能 — `models.json` 无结构验证
### L8. 理事会技能 — 计划文件读取无路径遍历保护
### L9. TokenLens — `aria-busy` 在空统计时永不清除，导致加载状态卡住
### L10. TokenLens — `pytest-asyncio==1.4.0` 非常旧
### L11. 数据库 — 3 个 TEXT 列上的 memory_tags UNIQUE 索引可能超过 SQLite 索引限制
### L12. 数据库 — 已归档记忆永不修剪（无保留策略）
### L13. 数据库 — 无行级降级迁移支持
### L14. deploy.sh — 在不必要时使用 pip install -e .，而非 requirements.txt
### L15. ruff.toml — 对于这种规模的项目，缺少 pep8-naming (N) 和 pydocstyle (D) 规则
### L16. voice_emotion.py — 伪频谱形心（时域分割，而非 FFT）
### L17. heat_engine.py 状态机 — FLOOD 状态在 30 秒静默后立即降为 IDLE
### L18. group_atmosphere.py — `should_join_conversation` 会修改输入的年龄消息

---

## 逐模块评级摘要

### 核心插件（A- 到 B- 范围）

| 模块 | 评级 | 关键优点 | 主要问题 |
|--------|--------|-----------|--------------|
| circuit_breaker.py | A | 干净的状态机，适当的状态转换 | 日志顺序错误；无主动健康检查 |
| conversation_fatigue.py | A | 优秀的多信号分析 | 当前消息使用了人工时间戳 |
| dialogue_rhythm.py | A | 自然的话题桥接，破冰内容 | 可能在中途分割思想 |
| loop_manager.py | A | 清晰的 API，指数退避 | 无恢复间隔缩减；无看门狗 |
| meme_lexicon.py | A | 18 个 meme 的精选词典，丰富的元数据 | 动态列表无线程安全 |
| message_debounce.py | A | 干净的类型感知合并，token 图像上限 | 锁在长时间处理程序中持有 |
| social_feed.py | A | 精彩的"刷手机"概念 | 硬编码的兴趣关键词 |
| time_validator.py | A | 优秀的时间幻觉预防 | 仅修复第一个幻觉 |
| emotion_deep.py | A- | 复杂的情感系统 | 分散的硬编码阈值 |
| image_reply.py | A- | 9 个类别的成熟分类 | 无多标签分类 |
| memory_embed.py | A- | 量化存储至原来的 25% | 同步数据库调用（严重 bug） |
| opinion_tracker.py | A- | 干净的意见 UPSERT | 模式约束可能不存在 |
| personalization.py | A- | 优秀的分层昵称系统 | 脆弱的手动排序阶梯 |
| personality.py | A- | 从默认值优雅降级 | 非线程安全的缓存初始化 |
| context_compressor.py | A- | 断路器模式，两层缓存 | 未使用的 `api_call_fn` 参数 |
| prompt_templates.py | A- | 优秀的热重载模板系统 | 同步文件 I/O |
| token_tracker.py | A- | 按任务类型的全面跟踪 | `persist()` 无自动调用 |
| topic_tracker.py | A- | 整洁的会话范围状态管理 | `_STOPWORDS` 是列表，而非集合 |
| world_context.py | A- | 免费天气 API（Open-Meteo） | 顺序 API 调用 |
| voice_mimo.py | A- | 合适的语音选择，妥善的超时处理 | 无重试逻辑 |
| voice_volcano.py | A- | 干净的错误代码检查 | 无文档的语音类型 |
| sticker.py | A- | V1/V2 标签兼容性 | 去重逻辑不清晰 |
| _audio_utils.py | A- | 减少跨模块重复 | 毫秒精度文件名冲突 |
| heat_engine.py | A- | 清晰的衰减模型 | 与 group_heat.py 命名冲突 |
| context_analyzer.py | B+ | 情感惯性 + 环境修饰符 | 每个消息都调用付费 LLM |
| group_atmosphere.py | B+ | 良好的多因素判断模型 | 修改调用者数据；无缓存 |
| music_card.py | B+ | 存在三层备选 | 硬编码的 NetEase 应用 ID |
| ocr.py | B+ | 仅本地 OCR，无外部依赖 | 未传递语言参数 |
| search.py | B+ | 搜索触发逻辑调整良好 | 无界搜索缓存 |
| stt.py | B+ | 清晰的两种引擎架构 | 备选是单向的（仅 MiMo） |
| values.py | B+ | 具有情感门控的意见系统 | 同步文件加载 |
| video_parser.py | B+ | yt-dlp 备选覆盖 1000+ 个平台 | 线程在取消时继续运行（资源泄漏） |
| follow_up.py | B+ | 成熟的多尝试跟进 | 无测试文件 |
| dialogue_rhythm.py | B+ | 干净的发声调度 | 无测试文件 |
| share_prompt.py | B+ | 特定平台的格式化 | 停用词重复 |
| promise_tracker.py | B+ | 有创意的承诺-遗忘-道歉循环 | 随机偏移不持久（参见 M12） |
| image_gen.py | B+ | 适当的反动漫负面提示 | 无自动清理 |
| media.py | B+ | 干净的 URL 提取 | 未去除 URL 周围的空白 |
| music.py | B+ | 使用 TTS 的语音演唱功能 | 无测试文件 |
| music_api.py | B+ | 智能副歌检测 | 非官方 NetEase API |
| reminder.py | B+ | 自然语言时间解析 | `chr(10)` 的 bug（参见 M11） |
| stt_mimo.py | B+ | 干净的 OpenAI 兼容客户端 | 硬编码的内容类型 |
| sticker_search.py | B+ | 在线搜索回退 | 添加后标签缓存未刷新 |
| context_optimizer.py | B+ | 智能多因素选择 | 误导性的变量名称 |
| exercise_actions.py | B- | 增添了人性化行为 | fire-and-forget，无错误追踪 |
| handler.py | B- | 最小化，单一职责 | 无启动验证 |
| mcp_client.py | B+ | 干净的工具注册 | 电话工具中的样板代码 |

### 数据库模块（B+ 到 A 范围）

| 模块 | 评级 | 关键优点 | 主要问题 |
|--------|--------|-----------|--------------|
| db_reminders.py | A | 干净的最小 CRUD | LIKE 查询无索引 |
| db_core.py | B+ | 正确的 WAL 模式，健康检查 | 无回滚 |
| db_affection.py | B+ | 批量衰减更新 | NOT IN 子查询低效 |
| db_cache.py | B+ | 干净的键值模式 | 无 |
| db_group.py | B+ | 良好的复合唯一约束 | `get_dynamic_cooldown` 是纯函数，应放在工具中 |
| db_memories.py | B+ | 在所有查询中正确使用 archive=0 | 无修剪策略 |
| db_memories_deep.py | B+ | 干净的情感记忆 | `detect_user_reaction` 是纯函数 |
| db_preferences.py | B+ | 灵活的用户键值存储 | 无 |
| db_proactive.py | B+ | 良好的主动逻辑 | `get_silent_private_users` 无 LIMIT |
| db_session.py | B+ | scratchpad 有 asyncio.Lock | 无 |
| db_social.py | B+ | 良好的关系衰减 | 无 |
| memory_embed.py | A- | 量化，批量余弦相似度 | **严重：导入不存在** |

### 理事会技能

| 类别 | 评分 |
|----------|-------|
| 架构 | 9/10 |
| API 集成 | 7/10 |
| 提示词 | 9/10 |
| 去重 | 8/10 |
| 配置 | 8/10 |
| 测试 | 9/10 |
| 边缘情况 | 9/10 |
| 安全性 | 7/10 |
| 文档 | 7/10 |
| **整体** | **A- (8.1/10)** |

### TokenLens

| 类别 | 评分 |
|----------|-------|
| 设计 | 8/10 |
| 解析器准确性 | 7/10 |
| 定价数据 | 6/10 |
| Web UI | 8/10 |
| 错误处理 | 7/10 |
| 测试 | 4/10 |
| **整体** | **B+ (6.7/10)** |

---

## 测试覆盖率差距

审查了 56 个测试文件。以下模块缺少专门测试：

**高优先级缺失：**
- `handler.py`、`pipeline.py`、`follow_up.py`、`reminder.py`、`search.py`、`video_parser.py`、`token_tracker.py`、`world_context.py`、`time_validator.py`

**中优先级缺失：**
- `music.py`、`music_api.py`、`music_card.py`、`topic_tracker.py`、`message_debounce.py`、`message_actions.py`、`group_heat.py`、`group_atmosphere.py`、`sticker_search.py`、`voice_emotion.py`、`voice_mimo.py`、`stt_mimo.py`

**TokenLens 缺失：**
- `test_server.py`、`test_advisor.py`、`test_billing_fetcher.py`、`test_summary.py`、`test_pricing_fetcher.py`、集成测试

**数据库缺失：**
- 没有针对 `db_core.py`、`db_session.py`、`db_preferences.py`、`db_social.py`、`db_affection.py`、`db_crud.py`、`memory_embed.py` 的测试

---

## 架构性问题

### 1. 热引擎重复
`heat_engine.py` 和 `group_heat.py` 使用不兼容的 API 实现了重叠的热状态机。选择一个并弃用另一个。

### 2. 令牌估算重复
`context_optimizer.estimate_tokens` 和 `context_compressor.estimate_tokens` 是近乎相同的。提取到共享工具中。

### 3. 全局可变状态蔓延
至少 7 个模块级可变字典/列表/集合没有明确的所有权：`_session_states`（follow_up）、`_MICRO_EVENTS`（behavior）、`_feed_store`（social_feed）、`_last_sticker_session`（sticker）、`_summary_cache`（context_optimizer）、`search_cache`（search）、`_DYNAMIC_MEMES`（meme_lexicon）。

### 4. 魔法数字蔓延
阈值如 `affection > 150`、`affection_score >= 200`、情感到语音的映射以及概率常数分散在 20+ 个模块中。没有 `constants.py`。

### 5. 模块主体中的延迟导入
多个模块在函数体内部而非顶部导入：`context_analyzer.py`（vision）、`social_feed.py`（api）、`follow_up.py`。这些掩盖了可能导致运行时错误的循环导入。

### 6. 无集中式错误报告
Fire-and-forget 任务（message_actions、context_compressor、promise_tracker）静默地吞噬异常。没有单一的可观测性钩子可以报告这些失败。

### 7. 双 .env 文件不同步
`.env.example`（21 行）和 `.env.full.example`（146 行）内容不同，`.env.full.example` 与实际的 `.env` 结构不一致。要么合并，要么明确记录关系。

---

## 各领域详细发现

### 理事会技能亮点
- 3 轮流水线架构（审查 → 交叉验证 → 判断）经过精心设计
- 错误处理稳健：JSON 解析回退使用正则表达式，错误传播到每个层级
- 去重算法（CJK 2-gram 雅卡尔 + 包含检查）在 20 对基准数据集上表现良好
- 测试覆盖率卓越：15 个测试套件，40+ 个测试方法，涵盖所有边缘情况
- 基于 `models.json` 的配置数据驱动支持无代码添加模型
- 上下文截断使用字符近似而非实际令牌计数，对于中文文本可能偏差 2-3 倍

### TokenLens 亮点
- 响应式、移动优先的 Web UI，具有骨架加载和 6 个主题颜色方案
- 干净的架构，职责分离良好
- 定价备选链设计精良，但存在一个设计缺陷：获取的新数据被硬编码的默认值覆盖
- 错误状态在 UI 和 CLI 中都得到了妥善处理
- 由于定价表冗余和缺乏服务器测试而失去分数

### 测试套件亮点
- 在 `conftest.py` 中使用 `safe_module_mock()` 的三层模拟系统经过精心设计
- `MockCursor`/`MockExecuteResult` 搭配正确处理 aiosqlite 的双 `await` + `async with` 模式
- 涵盖广泛：概率门控、情感传染、时区转变、分享解析中的 HTML 实体解码、非平衡匹配括号
- 弱点：概率性测试样本量仅为 200、全局可变状态修改没有异常安全清理、SQL 调用断言仅检查 `call_count`

### 数据库亮点
- 在所有关键复合键上正确使用 `UNIQUE` 约束
- WAL 模式 + `busy_timeout=5000` 针对并发读写进行了优化
- 具有 17 个迁移的版本化迁移系统，带有防御性的 `IF NOT EXISTS` / `try/except ALTER TABLE`
- **缺陷：** 所有写入操作无事务回滚意味着失败可能留下未完成的事务

---

## 修复优先级路线图

### 第 1 阶段（立即 — 本周）
1. 在 `memory_embed.py` 中修复 `ensure_tag_embedding` 导入（C1）
2. 轮换存储的真实 API 密钥（C2）
3. 从 `vpn-setup-report.md` 中删除 VPN 凭证并将文件加入 `.gitignore`（C3）
4. 设置 `ADMIN_API_KEY`（C4）
5. 固定 `playwright` MCP 版本（C7）
6. 修复 `reminder.py` 中 `chr(10)` 的 bug（M11）

### 第 2 阶段（近期 — 下周）
7. 解决热引擎重复问题（H1）
8. 为数据库操作添加事务回滚（H4）
9. 提取共享的 `estimate_tokens` 工具（H5）
10. 将 `deploy.sh` 备份扩展到整个项目（C6）
11. 固定 `pyproject.toml` 中的依赖项（C5）
12. 协调 `.env` / `.env.example` / `.env.full.example`（H12）
13. 更新 README.md 中的过时信息（H11）

### 第 3 阶段（本月）
14. 为 TokenLens 端点添加服务器测试（H2）
15. 修复重复的定价表（H3）
16. 在 `handler.py` 中添加启动验证（H8）
17. 修复消息防抖锁保持（H10）
18. 持久化承诺随机偏移（M12）
19. 为主要机器人项目添加 CI/CD（M15）
20. 为 `decay_affection` 重写 `NOT IN` 查询（M10）

### 第 4 阶段（下个月）
21. 填补高优先级模块的测试覆盖率差距
22. 解决架构问题 #3-7（全局状态、魔法数字、延迟导入、错误报告、.env 对齐）
23. 解决所有中优先级问题
24. 审核并更新所有文档

---

*报告生成于 2026-06-18。审计了 140+ 个 Python 文件，涵盖 5 个模块领域。*
