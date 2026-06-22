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

### 用户侧 API（8766，Phase 1）

FastAPI 用户侧 API 独立进程，物理隔离于 8082 bot：

```bash
# ✅ 正确管理方式
systemctl restart deepseek-api
journalctl -u deepseek-api -f --output=cat
curl http://127.0.0.1:8766/api/v1/health

# ❌ 同样不要 nohup 抢端口 8766
```

- 端口: 8766（FastAPI，JWT 认证，对接安卓控制面板 App）
- 密钥: `.api.env`（JWT_SECRET + AES_KEY，`chmod 600`，不可提交 git）
- 部署指南: `deploy/README-api.md`
- 与 8082 共享 `data/chat_memory.db`（SQLite WAL 模式）

## 测试

```bash
python -m pytest tests/ -v    # 1303 个测试（69 个测试文件）
```

## 架构要点

- **Pipeline 架构**: handler.py（99行）定义 22 个有序阶段，每阶段可短路（返回 `_SKIP`）
- **API 二层降级**: DeepSeek 远程 → Ollama 本地模型（含熔断器保护，local_llm.py）
- **记忆系统**: 置信度评分（0.5起步，引用+0.1，每日-0.02），低于 0.15 自动清理
- **情绪引擎**: VA 模型（效价+唤醒度），情绪惯性系数 0.65，情绪表达变体含 express_style 标记
- **Token 追踪**: token_tracker.py 记录 API 调用消耗与费用统计
- **错误上报**: error_reporter.py 统一异常收集与上报
- **因果上下文**: causal_context.py 会话级共享状态，统一时间源 + 因果链追踪
- **缺席事件**: absence_events.py 6 种真人缺席模型（上课/游戏/午睡/做饭/没电/通勤）
- **全局状态**: global_state.py 跨模块状态注册/快照/恢复，支持命名空间隔离
- **私有热度**: private_heat.py 私聊热度计算（原 heat_engine.py 重命名）
- **早晚安模板**: 35条早安 + 20条晚安模板，90% 场景走模板省 LLM 调用；双轨运行（事件驱动 + cron fallback）
- **活动感知**: can_interrupt 字段接入 pipeline，不可中断活动时回复缩短/跳过
- **非语言信号**: nonverbal_signals.py 6维信号检测（间隔/长度/表情/反问/语气/撤回）+ 情绪反馈链路（audit-2-2）
- **情绪累积**: emotion_accumulator.py 累积触发替代关键词匹配，消除双重计算（audit-2-1）+ 语义顺序保留（audit-2-3）
- **情绪隐藏**: should_express_emotion() 分层隐藏概率（高0%/中40%/低80%）+ 微表达泄露
- **事件驱动早晚安**: schedule/sleeping→waking 触发早安 + 对话收尾触发晚安（audit-1-4）
- **微事件**: 20 模板池 + 冷却期（30天同事件同用户不重复）+ DB 持久化 + LLM 动态生成
- **疲劳基线**: 用户回复风格 EMA 基线学习（≥20 样本）→ 偏离基线判定；区分「忙」和「烦」
- **承诺提取**: 正则 + LLM 混合提取（低置信度时 LLM 辅助）；改进正则跨词承诺匹配
- **承诺渐进遗忘**: 4 阶段遗忘概率（10%/30%/60%/80%）+ 道歉窗口 7 天
- **行为优先级**: 7 层优先级链（天气>季节>节日>刷屏>热搜>微事件>随机），概率性单选替代合并
- **情绪残留系统**: 恢复后残留淡出（指数衰减）+ rekindle 复发机制（基础概率 8%，24h 内翻倍）
- **VA→LLM 混合模型**: emotion_to_prompt_hint() 产出自然语言氛围描述，替代 14 种离散标签
- **口头禅双向影响**: bot 口头禅→用户画像→prompt 注入，形成互相影响闭环
- **好感度统一源**: get_affection() 唯一数据源 + 2s 短时缓存确保跨模块一致性
- **参数调优**: 10 个可配置参数（情绪累积阈值/隐藏概率/残留衰减率/缺席概率/复发概率等），通过 config.py 或 .env 覆盖
- **集成测试**: test_humanize_integration.py（54 tests）跨模块因果链验证

## 真人化改造进度（5 阶段计划，目标 2026-07-25）

| 阶段 | 内容 | 状态 |
|------|------|------|
| Phase 0 | 快速收益（6项） | ✅ 完成 2026-06-19 |
| Phase 1 | P0 架构基础（CausalContext + 缺席事件 + 全局状态迁移）| ✅ 完成 2026-06-19 |
| Phase 2 | P1 高感知缺陷（情绪累积/活动联动/早晚安事件驱动/非语言信号/情绪隐藏）| ✅ 完成 2026-06-19 |
| Phase 3 | P2 中等缺陷（微事件/疲劳基线/承诺LLM/行为优先级/承诺遗忘渐进）| ✅ 完成 2026-06-19 |
| Phase 4 | P3 低感知优化（VA→LLM/情绪残留/人设演化事件/口头禅双向/好感度统一）| ✅ 完成 2026-06-19 |
| Phase 5 | 集成测试 + 调优 + 文档 | ✅ 完成 2026-06-19 |

详见：[真人化最终实施计划](真人化最终实施计划-2026-06-19.md)

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
│   ├── handler.py            # Pipeline 主处理器（99行，阶段委托至 stages/）
│   ├── stages/               # 22 个 Pipeline 阶段（每阶段独立文件）
│   ├── db_*.py               # 数据库模块（14个：记忆/缓存/群组/情绪/好感度等）
│   └── ...
├── data/
│   ├── chat_memory.db        # SQLite 数据库
│   ├── stickers/             # 表情包（含 downloaded/ 子目录）
│   └── voice/                # 语音缓存
│   ├── tests/                    # 测试套件（69 文件 / 1303 测试）
└── tools/
    └── tokenlens/            # Token 用量可视化面板
