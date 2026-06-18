# TokenLens — 自建 Token 用量看板（v11 · 十一审定价修正版）

> **状态**: ✅ 全部实施完成 · 79+7+11+5 项审计修正 · 61 个测试通过 · 定价改用 DeepSeek 官方人民币 / 硬编码保护逻辑 / LiteLLM 三源合并 已修复并验证 · 8 项 GitHub 借鉴待实施

## 背景

用户通过 **ccswitch** 调用第三方 LLM API（DeepSeek / MoonShot / Kimi），Claude Code 作为前端客户端。Token 用量记录在 Claude Code 的 JSONL 会话日志中，与具体后端无关——`message.model` 字段直接反映第三方 API 返回的模型名。

四个核心需求：
1. **多模型用量对比** — 同时查看 deepseek-v4-pro / mimo-v2.5-pro / kimi-k2.6 等模型
2. **缓存命中率 + AI 建议** — 直接展示命中率并给出优化建议
3. **时间分类** — 1天 / 1周 / 1月 / 3月 / 1年
   - `day` = 今天 00:00 ~ now（UTC+8 时区对齐）
   - `week` = 过去 7×24h（滚动窗口，非自然周）
   - `month` = 过去 30×24h（滚动窗口）
   - `3month` = 过去 90×24h
   - `year` = 过去 365×24h
4. **移动端可用** — 手机浏览器同样体验良好

### 实际数据画像（已验证）

| 项目 | 文件数 | 大小 | 子代理大小 | 主要模型 |
|------|--------|------|-----------|---------|
| d--QQmaonian | 103 | 138.3 MB | 53.2 MB (209 文件) | deepseek-v4-pro (77%), mimo-v2.5-pro (22%), kimi-k2.6 |
| d--QQmaonian-DeepSeekQQ | 38 | 33.1 MB | 5.8 MB | deepseek-v4-pro, mimo-v2.5-pro |
| d--ZhuoChong | 2 | 2.4 MB | 0.4 MB | mimo-v2.5-pro |
| d--SUIFENG-Documents-PPT | 1 | 0.6 MB | — | mimo-v2.5-pro |
| C--Users-se-jng-k-s | 2 | 0.0 MB | — | deepseek-v4-pro, mimo-v2.5-pro |

- **总计**: 402 文件, 174.4 MB + 子代理 59.4 MB, 5 项目, **30,539 条记录（去重后）**
- **日期范围**: 2026-06-02 ~ 2026-06-17（持续增长中）
- **缓存命中率**: deepseek-v4-pro 98.0%, mimo-v2.5-pro 98.6%, kimi-k2.6 69.5%（Kimi 样本少）
- **子代理模型**: deepseek-v4-flash (67%), mimo-v2.5-pro (18%), deepseek-v4-pro (10%), kimi-k2.6 (5%)
- **子代理占总费用**: 16.6%（RMB 122.95 / RMB 740.47），**不可忽略**
- **cache_creation_input_tokens**: 第三方 API 不报告，恒为 0
- **`<synthetic>` 模型**: Claude Code 内部合成消息，usage 全 0，需过滤

---

## 技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| **后端** | Python + FastAPI | 项目已有 FastAPI（NoneBot2 依赖），无需新增框架 |
| **前端** | 纯 HTML + Pico CSS v2 **标准版** | 30KB gzipped ~6KB，零构建，自带 `.grid` 响应式布局，暗色模式，移动端友好 |
| **数据读取** | Python `json` 流式逐行解析 | JSONL 天然支持，174MB+59MB 全量扫描 ~1.5s（实测），无需数据库 |
| **缓存分析** | 规则引擎 + LLM 增强 | 先做规则检测，再调 DeepSeek 生成建议文案 |
| **工作摘要** | LLM 调用 | 调用 DeepSeek API，提取 session 中 user 消息生成摘要 |

---

## 项目结构

```
tools/tokenlens/                    # 仓库根目录（独立于 DeepSeekQQ/）
├── __main__.py                     # 入口: python -m tools.tokenlens
├── __init__.py
├── server.py                       # FastAPI app + 静态文件服务
├── parser.py                       # JSONL 流式解析器 + 聚合引擎（含递归子代理扫描）
├── advisor.py                      # 缓存命中率规则检测 + LLM 建议（含降级）
├── summary.py                      # LLM 工作摘要生成（opt-in + 消息截断）
├── config.py                       # 配置管理（路径、模型定价、API Keys）+ 启动验证
├── pricing.py                      # 模型定价表（RMB 统一存储，多源缓存，环境变量可覆盖）
├── pricing_fetcher.py              # 定价自动获取（DeepSeek/OpenRouter 多源抓取 + 24h 缓存降级）
├── billing_fetcher.py              # 官方账单获取（余额变化追踪：DeepSeek/Moonshot 余额 API）
├── format_utils.py                 # 数字格式化（完整+缩写）、时区处理
├── static/
│   ├── index.html                  # 单页看板（viewport + 响应式 + 主题切换 + 分享卡片）
│   ├── app.js                      # 香草 JS（Tab 切换 + API 调用 + Chart.js + KPI 动画 + 热力图）
│   ├── pico.min.css                # Pico CSS v2.0.6 标准版（本地化）
│   └── manifest.json               # PWA 清单（移动端"添加到主屏幕"支持）
├── requirements.txt                # fastapi, uvicorn, httpx
└── tests/
    ├── test_parser.py              # parser 单元测试
    ├── test_pricing.py             # 定价表 + 汇率 + 未知模型回退
    └── test_format_utils.py        # 数字格式化 + 时区偏移
```

---

## 核心模块设计

### 1. parser.py — JSONL 解析 + 聚合引擎

**功能：**
- **递归遍历** `~/.claude/projects/<project>/` 下所有 `*.jsonl`
  - 顶层目录：主会话 JSONL + session 同名子目录（`0582ab26-.../` 与 `0582ab26-....jsonl` 并存）
  - **`<session_uuid>/subagents/`** 子目录：Workflow 子代理会话，含 209 个文件 53MB（实测），占总费用 16.6%
  - 跳过 `tool-results/` 目录（`.txt` 文件，无需解析）
  - 使用 `os.walk()` 替代 `glob('*.jsonl')`，确保全覆盖
- 逐行解析，提取 `type: "assistant"` 事件的 `message.usage`
- **过滤 `model == "<synthetic>"`** — 内部合成消息，usage 全 0，无意义
- **去重用 `uuid`** — 每条 JSONL 记录有全局唯一 `uuid`，比 `(sessionId, timestamp)` 可靠
- 按 **项目 × 模型 × 日期 × 来源** 四维聚合（project 来源为 `~/.claude/projects/<name>/` 的 `<name>`，非 `record["cwd"]`）
- 缓存结果（内存 dict + 文件 mtime 增量刷新 + **文件列表变更检测**）
- 显式 `encoding='utf-8', errors='replace'` 防止 GBK 解码崩溃

**关键字段提取：**
```python
# 注意：project 来自扫描路径 ~/.claude/projects/<project>/，
# 而非 record["cwd"]。实测 cwd 是子目录级别且大小写不一致，
# 若直接使用会导致项目维度碎片化（一个项目散成 7-8 个"伪项目"）。
# cwd 保留用于 UI 展示。

# 解析防御（详见下方"JSONL 解析防御"）
if line.startswith('#') or not line.strip():
    continue
try:
    record = json.loads(line)
except json.JSONDecodeError:
    skipped_bad_lines += 1
    continue

if record.get("type") != "assistant":
    continue
if record["message"]["model"] == "<synthetic>":
    continue  # 内部合成消息，无实际用量

usage = record["message"]["usage"]
entry = {
    "uuid":               record["uuid"],            # 去重键（非全局唯一，见下文）
    "input_tokens":       usage["input_tokens"] or 0,
    "cache_read_tokens":  usage["cache_read_input_tokens"] or 0,
    "cache_create_tokens": usage["cache_creation_input_tokens"] or 0,  # 第三方 API 恒为 0
    "output_tokens":      usage["output_tokens"] or 0,
    "model":              record["message"]["model"],
    "timestamp":          record["timestamp"],        # UTC ISO 8601
    "project":            project_name,               # 来自扫描路径，非 cwd！
    "cwd":                (record.get("cwd") or "").lower(),  # 保留展示用，统一小写
    "session_id":         record["sessionId"],
    "source":             source,                     # "main" | "subagent"（新增维度）
}
```

**去重实现（v3 修订版）：**

实测发现 UUID 在 27,704 条 assistant 记录中有 3,166 个重复（11.4%）。
绝大多数（3,163）是完全相同的记录被写入两次——`set(uuid)` 可正确去重。
但有 2 个案例：同一 UUID 出现两次，第一次 usage 全为 0（幽灵记录），第二次有真实消耗。
若按首次出现保留，会丢失这 2 条数据。

**修正策略：按 usage 总和降序排序后去重，确保保留有 token 消耗的版本：**
```python
def deduplicate(records: list[dict]) -> list[dict]:
    """按 uuid 去重，优先保留 usage 总和更大的记录"""
    # 按 (uuid, usage_sum DESC) 排序，非零版本排在前面
    records.sort(key=lambda r: (
        r["uuid"],
        -(r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"])
    ))
    seen = set()
    result = []
    for r in records:
        if r["uuid"] not in seen:
            seen.add(r["uuid"])
            result.append(r)
    return result
```

**JSONL 解析防御：**
```python
# 1. 跳过 # 元数据注释行（部分 Claude Code 变体包含）
# 2. 跳过空行
# 3. 捕获 JSONDecodeError 防止截断行导致扫描崩溃
# 4. 跳过非 .jsonl 文件（如 .md、.meta.json、.txt）
if not filepath.endswith('.jsonl'):
    continue
if line.startswith('#') or not line.strip():
    continue
try:
    record = json.loads(line)
except json.JSONDecodeError:
    skipped_bad_lines += 1
    continue
```

**幽灵记录检测（新增）：**
```python
# 幽灵记录：同一 UUID 存在"usage 全 0"和"usage > 0"两个版本
# 实测 2 条案例（UUID c4d185b2, 6f403274），真实版本均有完全相同的 111,308 tokens
# 模式疑似 Claude Code 内部 bug：先写入空记录再写入真实记录
# 处理策略已覆盖（按 usage_sum DESC 去重），此处额外记录日志供排查：
if uuid_has_ghost:
    log_warning(f"Ghost record detected: uuid={uid} session={sid} "
                f"ghost_tokens=0 real_tokens={sum_val} — suspected Claude Code bug")
```

**聚合维度：**
- `DailyStats`: model, date, input, output, cache_read, cache_create, sessions, cost
- `ModelStats`: model, total_input, total_output, total_cache_read, cache_hit_rate, total_cost, message_count, **source (main/subagent)**
- `ProjectStats`: project, model_stats_list, total_cost

**增量刷新：**
- 首次加载：全量扫描所有 JSONL（174MB 主数据 + 59MB 子代理 ~1.5s 实测）
- 后续刷新：每次 API 请求时**同时检查**：
  1. 已知文件的 mtime 变化 → 增量重解析
  2. **文件列表变化**（`os.listdir()` 轻量对比）→ 发现新文件自动加入追踪
- 手动刷新：`GET /api/refresh` 强制全量重新扫描
- 数据缓存在内存 `Aggregator` 单例（`--workers 1` 模式，多 worker 需应用级单例）
- 增量优化的主要收益在于避免重复解析大文件，而非性能必需

**JSONL 格式兼容：**
- Parser 使用宽松的 `.get()` 访问，未知字段静默忽略
- 跳过 `#` 开头的元数据注释行（部分 Claude Code 变体包含）
- 捕获 `json.JSONDecodeError` 防止截断行导致扫描崩溃
- 若关键字段（model、usage）缺失，跳过该条记录并计数
- API 错误事件（`isApiErrorMessage: true`）不包含 usage，自动跳过
- `<synthetic>` 模型的 message 结构异常（含 `container`/`context_management` 等额外字段），model 检查需放在字段访问之前
- 在日志中报告 `skipped_details: {bad_lines, no_model, no_usage, synthetic, api_error, non_jsonl}` 供排查
- 目录结构：跳过 `tool-results/` 子目录（`.txt` 文件），递归进入 `subagents/` 子目录扫描 `.jsonl`

### 2. pricing.py — 模型定价（含自动获取）

**定价来源优先级：**
1. 环境变量 `TOKENLENS_PRICING_JSON`（手动覆盖）
2. 本地缓存 `~/.tokenlens/pricing_cache.json`（24h TTL，自动获取）
3. 硬编码默认值（定期手动更新，始终反映最新官方价格）

```python
# 所有价格以 RMB (元/百万token) 为统一存储单位
# 可通过环境变量 TOKENLENS_PRICING_JSON 覆盖整个定价表
# 可通过环境变量 TOKENLENS_USD_TO_RMB 调整汇率（默认 7.25）

# 硬编码默认值 — DeepSeek 使用官方人民币定价，非 USD 汇率折算
# 原因: DeepSeek 的官方 ¥ 价格独立于美元价格，内部汇率约 7.14
#       直接用官方 ¥ 定价更准确（如 V4 Pro ¥3.00 而非 $0.435×7.25=¥3.15）
_DEFAULT_PRICING = {
    # DeepSeek 官方人民币定价 (来源: api-docs.deepseek.com)
    # V4 Pro: ¥3.00 输入 / ¥0.025 缓存命中 / ¥6.00 输出
    #   (USD 参考: $0.435 / $0.003625 / $0.87)
    # V4 Flash: ¥1.00 输入 / ¥0.02 缓存命中 / ¥2.00 输出
    #   (USD 参考: $0.14 / $0.0028 / $0.28)
    #   ⚠️ 缓存命中于 2026-04-26 下调至 $0.0028（此前 $0.0048）
    "deepseek-v4-pro":   {"input": 3.00, "cache_read": 0.025, "output": 6.00},
    "deepseek-v4-flash": {"input": 1.00, "cache_read": 0.02,  "output": 2.00},

    # 小米 MiMo (对标 DeepSeek 官方人民币定价)
    "mimo-v2.5-pro":     {"input": 3.00, "cache_read": 0.025, "output": 6.00},
    "mimo-v2.5":         {"input": 1.00, "cache_read": 0.02,  "output": 2.00},

    # Kimi/Moonshot 官方 (USD→RMB @7.25)
    # $0.95 / $0.16 / $4.00 → ¥6.89 / ¥1.16 / ¥29.00
    "kimi-k2.6":         {"input": 6.89, "cache_read": 1.16, "output": 29.0},

    # Claude 家族 (Anthropic 官方, USD→RMB @7.25)
    # Sonnet 4: $3 / $0.30 / $15, Opus 4: $15 / $1.50 / $75
    "claude-sonnet-4-20250514": {"input": 21.75, "cache_read": 2.18, "output": 108.75},
    "claude-opus-4-20250514":   {"input": 108.75, "cache_read": 10.88, "output": 543.75},
}
```

**定价修正历史：**

| 日期 | 审计 | 修正内容 | 费用影响 |
|------|------|---------|---------|
| 2026-06-17 | 六审 | cache_read 从 input/10 修正为官方定价（¥0.025~¥1.16） | -40%（cache_read 高估 5-10×） |
| 2026-06-17 | 七审 | 全量定价更新为最新官方美元价 → 人民币折算 | +2%（input/output 略涨） |
| 2026-06-17 | **十一审** | **改用 DeepSeek 官方人民币定价**，修复三个 bug（见十一审） | -1.7%（详见下方） |

**十一审费用变化（周视图）：**

| 模型 | 修复前 | 修复后 | 变化 | 根因 |
|------|--------|--------|------|------|
| deepseek-v4-pro | ¥192.69 | ¥185.39 | -3.8% | 官方 ¥3.00 而非 $0.435×7.25=¥3.15 |
| deepseek-v4-flash | ¥42.79 | ¥44.42 | +3.8% | cache_read 从 0.009→0.02（修复 $0.0012 错值） |
| kimi-k2.6 | ¥102.60 | ¥102.60 | 无 | Moonshot 无官方 ¥ 定价，USD 折算正确 |
| mimo-v2.5-pro | ¥5.00 | ¥4.77 | -4.6% | 随 DeepSeek V4 Pro 同步调整 |
| **总计** | **¥343.09** | **¥337.18** | **-1.7%** | |

**定价表设计原则：**
- 统一存储为 RMB，`currency` 字段仅用于 UI 展示时标注原始币种
- **硬编码值绝对优先**：上游来源（LiteLLM/OpenRouter/web scraping）的数据**不能覆盖**硬编码的 input/output/cache_read。上游数据仅用于**添加新模型**和**补充缺失的 cache_read**
- 环境变量 `TOKENLENS_PRICING_JSON` 最高优先级，可覆盖所有值（包括硬编码）
- 对 DeepSeek 等有官方人民币定价的模型，使用官方 ¥ 定价而非 USD→RMB 汇率折算
- 未匹配到定价的模型：UI 显示"未知价格"，费用列显示 `—`

### 2b. pricing_fetcher.py — 定价自动获取

**多源抓取，24h 缓存降级，硬编码值优先：**

| 来源 | 方式 | 覆盖字段 | 说明 |
|------|------|---------|------|
| **LiteLLM 社区** | JSON `BerriAI/litellm` GitHub 仓库 | input, output, cache_read | 1300+ 模型，ccusage 同款数据源。**⚠️ 暂未收录 DeepSeek V4 Pro/Flash**，deepseek-chat 条目仍为旧 V3 定价 ($0.28/M)，因此不映射该条目到 V4 模型 |
| **DeepSeek 官网** | HTML 解析 `api-docs.deepseek.com/zh-cn/quick_start/pricing` | input, output, cache_read | 正则提取 ¥ 价格行（脆弱，依赖于页面结构） |
| **OpenRouter API** | JSON `openrouter.ai/api/v1/models` | input, output | USD→RMB 转换，**不提供 cache_read**（仅估算值 input/50） |

**合并策略（十一审修订）：**
- **硬编码默认值（FALLBACK_PRICING）绝对优先**：上游来源返回的数据永远不会覆盖已有硬编码模型的 input/output/cache_read
- **上游数据仅用于**：① 添加硬编码表中没有的新模型 ② 补充缺失的 cache_read
- **原因**：OpenRouter 的 `deepseek-v4-flash` 定价为错误估算（输入 ¥0.65 vs 正确 ¥1.00）；LiteLLM 的 `deepseek-chat` 为旧 V3 价格（$0.28/M vs V4 Flash $0.14/M）；网页 HTML 解析可能失效
- **环境变量 `TOKENLENS_PRICING_JSON` 具有最高优先级**，可覆盖所有来源的数据

**缓存策略：**
- 本地缓存 `~/.tokenlens/pricing_cache.json`，24h TTL
- 获取失败 → 降级到过期缓存 → 降级到硬编码默认值
- `pricing.py:_load_pricing()` 同样保护硬编码值不被缓存覆盖（缓存仅添加新模型 + 补充 cache_read）

**CLI 使用：**
```bash
python -m tools.tokenlens --fetch-pricing    # 强制更新定价缓存
python -m tools.tokenlens --show-pricing     # 显示当前定价表
```

**API 端点：**
```
GET  /api/pricing          # 返回当前定价表 + 元信息（来源、获取时间）
POST /api/pricing/refresh  # 强制刷新定价缓存
```

### 2c. billing_fetcher.py — 官方账单（余额变化追踪）

**原理：各平台只提供余额查询 API，无公开用量明细。但可通过"余额变化"反推实际花费。**

```
实际花费 = 上次余额 - 当前余额 （余额减少时）
累计花费 = 所有历史余额减少量之和（充值增加不计入）
```

**支持的平台：**

| 平台 | 余额 API | 环境变量 |
|------|---------|---------|
| DeepSeek | `GET https://api.deepseek.com/user/balance` | `DEEPSEEK_API_KEY` |
| Moonshot/Kimi | `GET https://api.moonshot.cn/v1/users/me/balance` | `MOONSHOT_API_KEY` 或 `KIMI_API_KEY` |
| MiMo | 暂无（计费系统尚未正式上线） | — |

**数据结构：**
```python
@dataclass
class PlatformBilling:
    platform: str
    current_balance: float | None    # 当前余额
    previous_balance: float | None   # 上次余额
    spent_since_last: float          # 上次到现在的花费
    total_spent_tracked: float       # 历史追踪的总花费

@dataclass
class CombinedBilling:
    platforms: dict[str, PlatformBilling]
    total_official_spend: float      # 官方实际花费（余额变化）
    total_balance: float             # 总余额
    local_estimate: float            # TokenLens 本地估算
    is_first_run: bool               # 首次运行（无历史，无法算花费）
```

**余额历史：** 快照保存在 `~/.tokenlens/balance_history.json`，最多保留 365 条/平台。

**对比逻辑：**
- `discrepancy_pct = (official_spend - local_estimate) / local_estimate`
- 偏差 < 20%：✅ 定价准确
- 偏差 ≥ 20%：⚠️ 定价可能需要更新

**CLI 使用：**
```bash
python -m tools.tokenlens --billing              # 查询余额 + 花费追踪
python -m tools.tokenlens --billing --billing-days 30  # 指定天数（余额模式忽略）
```

**API 端点：**
```
GET /api/billing → {
    platforms: { deepseek: {...}, moonshot: {...} },
    total_official_spend, total_balance,
    local_estimate, discrepancy_pct,
    is_first_run, note
}
```

**注意：** 首次运行仅记录当前余额快照（花费 = ¥0）。需要多次运行（间隔一段时间）才能累积花费数据。两次查询之间余额的减少量 = 实际 API 花费，100% 准确，不依赖任何定价表。

### 3. advisor.py — 缓存 AI 建议

**规则层（无需 LLM，即时输出）：**

| 条件 | 严重度 | 建议 |
|------|--------|------|
| cache_hit_rate < 60% | 🔴 异常 | 缓存命中率异常偏低——检查是否频繁切换项目、会话是否过长超过 5min TTL |
| cache_hit_rate 60-80% | 🟡 偏低 | 缓存利用率有提升空间，考虑减少并行项目数以维持缓存热度 |
| cache_hit_rate 80-95% | 🟢 正常 | 缓存策略工作良好 |
| cache_hit_rate > 95% | 💎 极佳 | 超高效缓存利用，长上下文稳定性优秀 |
| hit_rate 连续 3 个 session 下降 >20% | 🟠 趋势告警 | 缓存命中率持续下滑，检查是否最近开始频繁切换项目或引入新工具 |
| cache_read > 10× input | ℹ️ 提示 | 单次会话超高缓存复用，可考虑进一步延长会话 |

> **已删除的规则**：`cache_create > 0 且 cache_read = 0` — 第三方 API 不报告 `cache_creation`，恒为 0。

**阈值校准依据**：实际数据显示 deepseek-v4-pro 命中率 98.1%、mimo-v2.5-pro 98.6%、kimi-k2.6 94.2%。原始方案中 20%/50%/80% 三档过宽，从不触发 🔴🟡。

**LLM 增强（可选，默认关闭）：**
- 将统计数据和规则结果发给 DeepSeek
- LLM 生成个性化的优化建议（侧重趋势分析和异常检测，而非基础建议）
- 仅在数值变化 >10% 时重新生成
- **受 `TOKENLENS_LLM_ENABLED` 环境变量控制**
- **降级策略**：LLM 调用失败时（超时/API 不可用），规则层建议仍正常输出，UI 标注"AI 增强建议暂不可用"
- **超时与重试**：10s 超时，最多 2 次重试（间隔 1s/3s 指数退避）
- **隐私提示**：UI 上标注"AI 建议由 DeepSeek API 生成，会将统计数据发送到第三方"

### 4. summary.py — 工作摘要

**策略：**
- **默认关闭**，仅在用户点击"生成摘要"按钮后手动触发（opt-in）
- UI 按钮旁标注："摘要生成会将用户消息发送到 DeepSeek API"
- 提取最近 N 个 session 中 `type: "user"` 且 `message.role: "user"` 的消息（跳过 attachment、queue-operation）
- **消息截断**：每条 user 消息取前 500 字符，总长度限制在 8K 字符以内，防止超出 DeepSeek 上下文窗口
- 发送给 DeepSeek 生成简短摘要（3-5 句话）
- 按 session 缓存，生成后持久化到内存直到手动刷新
- 受 `TOKENLENS_LLM_ENABLED` 环境变量控制（`false` 时禁用 LLM 调用，UI 隐藏生成按钮）
- LLM 调用失败时（超时/API 不可用）：UI 显示"摘要生成失败，请稍后重试"，不阻塞其他功能

**Prompt 模板：**
```
以下是最近 {N} 个 AI 编程会话的用户提问摘要。请用 3-5 句话总结主要工作内容：
[截断后的 user messages，每条 ≤500 字符，总计 ≤8K 字符]
```

**超时与重试：**
- 请求超时：10 秒
- 最大重试：2 次（间隔 1s/3s 指数退避）
- 全部失败后返回 `{"error": "summary_unavailable"}`，前端展示降级提示

### 5. server.py — FastAPI 服务

**API 端点：**
```
GET  /api/health                                      # 健康检查（服务器监控用）
GET  /api/refresh                                     # 强制刷新数据（重新扫描 JSONL）
GET  /api/stats?period=week&tz=+8&project=xxx         # 核心统计（period: day/week/month/3month/year）
GET  /api/stats/compare?period=week&tz=+8&project=xxx # 周期对比（当前 vs 上一周期，含变化率 delta）
GET  /api/models?period=week&tz=+8&project=xxx&source=all  # 各模型用量 + 缓存命中率（source: main/subagent/all）
GET  /api/cache-advice?model=xxx&period=week          # 缓存 AI 建议（规则 + LLM），可选 ?model= 按模型过滤
GET  /api/sessions?limit=20&period=week&project=xxx   # 最近会话列表
GET  /api/summary?session=xxx                         # 指定 session 摘要（手动触发）
GET  /api/export?format=csv&period=week&project=xxx   # 数据导出（CSV/JSON）
GET  /api/pricing                                     # 当前定价表 + 元信息（来源、获取时间）
POST /api/pricing/refresh                             # 强制刷新定价缓存
GET  /api/billing                                     # 官方余额 + 实际花费追踪（余额变化法）
GET  /api/trend?period=week&project=xxx               # 每日 Token 趋势数据（供 Chart.js 堆叠柱状图）
GET  /api/hourly?period=week&project=xxx              # 按小时聚合的 Token 用量（供热力图使用）
GET  /api/tools?period=week&project=xxx               # 工具调用统计（从 JSONL content block 提取 tool_use）
GET  /api/network                                     # 服务器网络信息（LAN IP，供移动端 QR 码生成）
GET  /static/<path>                                   # 静态文件
```

**`?project=` 参数（新增）：**
- 所有聚合端点支持可选 `?project=d--QQmaonian` 按项目过滤
- 不传 `?project=` 时返回所有项目汇总
- `/api/health` 返回 `{"projects": ["d--QQmaonian", ...]}` 供前端填充项目下拉框

**`/api/export` 端点（新增）：**
- 支持 `?format=csv` 和 `?format=json` 两种导出格式
- CSV 列：date, model, project, source, input_tokens, cache_read_tokens, output_tokens, cost_rmb
- 受 `period` 和 `project` 参数过滤

**安全约束：**
- 默认绑定 `127.0.0.1:8090`（仅本机访问）
- `--host 0.0.0.0` 输出显眼的 WARNING：
  ```
  ⚠️ TokenLens 将监听所有网络接口！
  JSONL 数据包含你的完整对话内容，请确保网络安全。
  ```
- 无鉴权（纯本地工具），不提供删除/修改 JSONL 的 API

**端口：** `8090`（避开 8082 Bot、8081 备用）

**Server 启动方式：**
```python
# 使用 --workers 1（避免多进程各自解析数据）
# uvicorn tools.tokenlens.server:app --host 127.0.0.1 --port 8090
```

### 6. format_utils.py — 数字格式化 + 时区

```python
def format_tokens(n: int) -> str:
    """完整数字格式化（桌面端）"""
    return f"{n:,}"

def format_tokens_short(n: int) -> str:
    """缩写（移动端）：2,001,359,360 → 2.00B"""
    if n >= 1_000_000_000:
        return f"{n/1_000_000_000:.2f}B"
    elif n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)

def format_cost(n: float) -> str:
    """费用：桌面 ¥127.34 / 移动 ¥127"""
    return f"¥{n:,.2f}"

# 时区：timestamp 是 UTC，用户在中国 +8
# period=day 默认以 UTC+8 计算"今天"的边界
# 可通过 ?tz=+8 参数调整
```

### 7. config.py — 配置管理 + 启动验证

```python
class Config:
    data_dir: Path
    tz_offset: int = 8
    llm_enabled: bool = True
    llm_timeout: int = 10       # 秒
    llm_max_retries: int = 2

    @classmethod
    def validate(cls) -> list[str]:
        """启动时调用，返回警告列表（空列表 = 一切正常）"""
        warnings = []
        if not cls.data_dir.exists():
            raise FileNotFoundError(f"数据目录不存在: {cls.data_dir}")
        if not cls.data_dir.is_dir():
            raise NotADirectoryError(f"不是目录: {cls.data_dir}")
        # 检查是否有读权限
        if not os.access(cls.data_dir, os.R_OK):
            raise PermissionError(f"无读权限: {cls.data_dir}")
        # 检测 JSONL 文件数量
        jsonl_count = sum(1 for _ in cls.data_dir.rglob("*.jsonl"))
        if jsonl_count == 0:
            warnings.append("未发现任何 .jsonl 文件，看板将为空")
        if cls.llm_enabled and not os.getenv("DEEPSEEK_API_KEY"):
            warnings.append("DEEPSEEK_API_KEY 未设置，AI 建议/摘要功能将不可用")
        return warnings
```

启动日志示例：
```
TokenLens v1.0
数据目录: /root/tokenlens-data（5 个项目, 355 个 JSONL 文件）
LLM 功能: 已禁用（TOKENLENS_LLM_ENABLED=false）
缓存建议: 规则引擎模式
服务器: http://127.0.0.1:8090
```

### 8. static/index.html — 移动端友好 + 功能丰富的看板

**布局（从上到下）：**
1. **顶部栏** — 标题 `TokenLens` + 主题选择器（6 套配色）+ 数据刷新时间 + 刷新按钮
2. **工具栏** — 项目选择 + 来源筛选 + 官方 vs 本地费用对比
3. **时间 Tab 栏** — `1天 | 1周 | 1月 | 3月 | 1年`（横向滚动，不换行）
4. **KPI 卡片行**（桌面 4 列 → 平板 2 列 → 手机 1 列，带 count-up 动画）：
   - 总 Token 数 / 缓存命中率 % / 估算费用 ¥ / 会话数
5. **图表区**（2 列 → 手机 1 列）：
   - 📈 每日 Token 趋势（Chart.js 堆叠柱状图：输入/缓存/输出）
   - 🍩 模型用量分布（Chart.js 环形图，Top 5 + 其他）
6. **热力图**（24 小时时段 Token 用量，渐变色彩）
7. **工具调用 + 费用趋势**（2 列 → 手机 1 列）：
   - 🛠️ 工具调用分布（横向柱状图，从 JSONL content block 提取）
   - 💰 每日费用趋势（折线图：每日费用 + 累计费用，双 Y 轴）
8. **模型对比表**（`overflow-x: auto` 包裹，移动端横向滑动）
   - 模型名 | 输入 | 输出 | 缓存读 | 命中率 | 费用 | 消息数 | 来源
   - <300 条消息的模型标注"样本不足"，命中率用灰色 `≈` 前缀（低置信度）
9. **AI 建议卡片**（缓存优化建议 + 隐私标注 + LLM 增强标注）
10. **最近会话列表** — 时间 | 项目 | 模型 | Token | 费用
11. **分享卡片** — 用量摘要卡片，支持 PNG 下载 + 文本复制
12. **移动端访问引导** — QR 码 + URL 显示（qrcodejs CDN）

**技术实现：**
- **Chart.js 4.4.8**（CDN: jsdelivr）— 6 种图表类型（堆叠柱状/环形/横向柱状/折线+面积/双 Y 轴）
- **qrcodejs 1.0.0**（CDN: jsdelivr）— 移动端 QR 码
- **PWA 支持** — `manifest.json` + apple-mobile-web-app meta 标签 + 主题色
- **主题系统** — 6 套配色（蓝/绿/紫/橙/青/粉），`data-theme-tokens` 属性 + CSS 变量，localStorage 持久化
- **KPI 动画** — count-up 数字动画，easeOutCubic 缓动，800ms
- **分享卡片** — Canvas 2D 绘制截图 PNG 下载 + 文本复制到剪贴板
- **错误处理** — `safeCreateChart()` 包装器，图表加载失败显示可见错误提示（非静默失败）

**移动端关键实现：**

```html
<!-- viewport 必须 -->
<meta name="viewport" content="width=device-width, initial-scale=1.0">
```

```css
/* 核心响应式样式 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1rem;
}
@media (max-width: 768px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 480px) {
  .stats-grid { grid-template-columns: 1fr; }
}

/* 表格横向滚动（移动端关键） */
.table-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

/* Tab 栏横向滚动 */
.tab-bar {
  display: flex;
  overflow-x: auto;
  white-space: nowrap;
  gap: 0.25rem;
}
.tab-bar button {
  min-height: 44px;          /* iOS HIG 触控最小尺寸 */
  min-width: 44px;
  padding: 0.5rem 1rem;
  white-space: nowrap;
}

/* 所有可点击元素触控友好 */
button, a, [role="button"] {
  min-height: 44px;
  min-width: 44px;
}
```

**视觉风格：**
- Pico CSS v2 默认暗色主题（`data-theme="dark"` 或自动跟随系统）
- 强调色覆盖：`--pico-primary: #58a6ff`（GitHub 风格蓝）
- 统计数字：等宽字体（JetBrains Mono / Cascadia Code / monospace fallback）
- 缓存命中率环形指示：简化为 `<progress>` 元素或大号百分比数字 + 颜色语义
- **移动端数字显示缩写**（`2.00B`），桌面端显示完整值（`title` attribute 显示精确值）
- 加载状态：API 请求中显示 skeleton（Pico 的 `aria-busy="true"`）

**空状态处理：**
- 数据为空：显示"暂无数据"占位，不显示空白卡片
- 某时间段无数据：卡片显示 `—`
- 某模型无数据：不在对比表中出现
- 某模型 <300 条消息：命中率列显示为灰色 `~94.2%`，hover 显示"样本较少，仅供参考"

### 9. static/app.js — 香草 JS（全功能）

```javascript
// 核心功能（v1.1 新增 6 项）：
// 1. Tab 切换 → 更新 period + project 参数，重新 fetch 所有 API（Period 正确过滤 ✓）
// 2. API 调用 → fetch 8 个端点 Promise.all 并行（stats/models/advice/sessions/trend/billing/hourly/tools）
// 3. Chart.js 渲染 → safeCreateChart() 包装器 + try/catch + 错误可视化
// 4. KPI 动画 → animateValue() count-up 动画（easeOutCubic, 800ms）
// 5. 主题管理 → switchTheme() 6 套配色 + localStorage 持久化 + 图表联动重绘
// 6. 热力图 → renderHeatmapChart() 按小时 Token 分布（渐变色彩）
// 7. 工具分析 → renderToolsChart() 横向柱状图（从 /api/tools 获取 JSONL tool_use 统计）
// 8. 费用趋势 → renderCostTrendChart() 双 Y 轴折线图（每日 + 累计费用）
// 9. 分享卡片 → downloadShareCard() Canvas PNG 下载 + copyShareText() 剪贴板
// 10. 移动端引导 → setupMobileGuide() QR 码生成 + LAN IP 检测
// 11. PWA 注册 → serviceWorker 支持
// 12. 错误处理 → Chart.js 加载失败时可见错误提示（非静默失败）

const isMobile = window.matchMedia('(max-width: 768px)').matches;

function safeCreateChart(key, canvasId, config) {
    safeDestroyChart(key);
    try {
        if (!hasChartJS()) throw new Error('Chart.js 未加载');
        chartInstances[key] = new Chart(document.getElementById(canvasId).getContext('2d'), config);
        return chartInstances[key];
    } catch (e) {
        // 显示可见错误提示
        document.getElementById(canvasId + '-error').style.display = 'flex';
        document.getElementById(canvasId + '-error').textContent = '⚠️ ' + e.message;
        return null;
    }
}
```

---

## CLI 命令设计

```bash
# 启动 Web 服务器（默认 127.0.0.1:8090）
python -m tools.tokenlens
python -m tools.tokenlens --port 8090 --host 127.0.0.1

# ⚠️ 局域网/公网访问（显示安全警告）
python -m tools.tokenlens --host 0.0.0.0

# 纯 CLI 模式（不启动服务器，直接输出到终端）
python -m tools.tokenlens --cli --period week
python -m tools.tokenlens --cli --models
python -m tools.tokenlens --cli --cache

# 定价管理
python -m tools.tokenlens --fetch-pricing      # 从官网获取最新定价并缓存
python -m tools.tokenlens --show-pricing       # 显示当前定价表

# 官方账单（余额变化追踪）
python -m tools.tokenlens --billing            # 从官方 API 获取余额 + 花费追踪
python -m tools.tokenlens --billing --billing-days 30  # 指定天数

# 指定数据目录
python -m tools.tokenlens --data-dir ~/.claude/projects

# 指定时区偏移（默认 +8）
python -m tools.tokenlens --tz +8
```

---

## 关键技术细节

### 缓存命中率计算公式
```
hit_rate = cache_read_input_tokens / (input_tokens + cache_read_input_tokens)
```
- DeepSeek/mimo/kimi 的 `input_tokens` **不包含** `cache_read_input_tokens`
- 因此真实输入 = input + cache_read，命中率 = cache_read / 真实输入
- 代码中加 sanity check：`assert cache_read <= input + cache_read`

### 去重策略
- ~~使用全局唯一 `uuid` 字段~~（v3 修正：实测 11.4% 的 UUID 在 assistant 记录中重复，并非全局唯一）
- 绝大多数重复是完全相同的记录被写入两次（`set(uuid)` 可处理）
- 存在 2 个案例：同一 UUID 有"幽灵记录"（usage 全 0）和真实记录两个版本
- **修正策略：按 `(uuid, -usage_sum)` 排序后 `set(uuid)` 去重**，优先保留有 token 消耗的版本
- `(sessionId, timestamp, model)` 组合键也不唯一（实测 4,999 条重复），不可用

### 项目归属
- **数据源遍历**：`~/.claude/projects/<project_name>/*.jsonl`
- **project 标识来源**：目录名 `<project_name>`（如 `d--QQmaonian`），而非 `record["cwd"]`
- **原因**：实测 `cwd` 是子目录级别（`D:\QQmaonian\DeepSeekQQ\plugins\deepseek`），且大小写不一致（`D:\` vs `d:\`），直接使用会碎片化
- **cwd 保留**：归一化（`.lower()`）后用于 UI 显示，不做聚合维度

### 增量刷新
- 首次加载：全量扫描所有 JSONL 文件（174MB 主数据 + 59MB 子代理，实测 ~1.5s）
- 后续刷新：每次 API 请求时检查文件 mtime + 文件列表，只重新解析变更/新增文件
- 手动刷新：`GET /api/refresh` 强制全量重新扫描
- 服务器场景：配合 Syncthing 自动同步 → mtime 变化 → 增量刷新自动生效
- 数据缓存在内存 `Aggregator` 单例中

### LLM 建议缓存
- 缓存命中率建议：仅在数值变化 >10% 时重新生成
- 工作摘要：按 session 缓存，session 新增消息时增量更新

### `<synthetic>` 模型过滤
- Claude Code 内部合成消息（`end_turn`, `stop_sequence` 等）
- 所有字段为 0，无统计意义
- Parser 层直接 skip

---

## GitHub 同类项目调研（2026-06-17）

### 第一梯队：最值得借鉴

| 项目 | ⭐ | 技术栈 | 亮点 |
|------|-----|--------|------|
| [ccusage](https://github.com/ccusage/ccusage) | 16,300 | Rust + TypeScript | 行业标杆 CLI，`npx ccusage` 零配置，支持 15+ AI 编程工具，LiteLLM 定价，离线模式 |
| [tokscale](https://github.com/junhoyeo/tokscale) | 3,800 | Rust + React | GitHub 风格 3D 贡献热力图、交互式 TUI、排行榜、30+ 客户端支持 |
| [tokencost](https://github.com/AgentOps-AI/tokencost) | 2,000 | Python 库 | 纯价格计算库，传 token 数+模型名→返回 USD 费用，支持 400+ 模型 |
| [ccost](https://github.com/carlosarraes/ccost) | 9 | Rust 单二进制 | 去重策略最讲究（requestId 优先）、多币种、隐私模式 `--hidden`、零依赖 |
| [TokenTelemetry](https://github.com/VasiHemanth/tokentelemetry) | 120 | Python + Web | Session 瀑布流追踪、工具调用分析 |
| [ClaudeCodeUsageDashboard](https://github.com/AgenticSec/ClaudeCodeUsageDashboard) | 11 | TypeScript + Python | **与 TokenLens 最相似**：Web 看板 + SessionEnd Hook 自动采集 + 团队排行 + Skill/MCP 分析 |

### 借鉴实现的功能（12 项，已全部实现）

| 功能 | 来源 | TokenLens 实现 |
|------|------|---------------|
| 6 套主题切换 | Tokdash (10 themes) | `data-theme-tokens` CSS 属性 + localStorage |
| 活动热力图 | Tokscale (3D heatmap) | `/api/hourly` 端点 + Chart.js 渐变柱状图 |
| 动画 KPI 计数器 | AI Usage Tracker | `animateValue()` easeOutCubic |
| 分享卡片 PNG | Tokendashboard (9 cards) | Canvas 2D 绘制 + 文本复制 |
| PWA 支持 | Tokdash | manifest.json + apple-mobile-web-app |
| 工具调用分析 | TokenTelemetry | `/api/tools` 端点（JSONL content block 提取） |
| 每日费用趋势 | TokenLens (mikeymiaoxyz) | 双 Y 轴折线图（每日 + 累计） |
| 本地 vs 官方对比 | 自行设计 | `/api/billing` 余额变化追踪 + 偏差百分比 |
| QR 码移动端访问 | 自行设计 | `/api/network` + qrcodejs CDN |
| **LiteLLM 定价源** | ccusage | `LiteLLMSource` 类，三源优先级（DeepSeek > LiteLLM > OpenRouter） |
| **周期对比增量** | 自行设计 | `/api/stats/compare` + KPI 增量 ↑↓% 显示 |
| **表格排序 + 行内柱状图** | 自行设计 | `makeSortHandler()` + `.inline-bar` CSS + `data-sort` 属性 |

---

## 实施步骤（分 4 阶段）

### Phase 1：核心解析引擎
| # | 任务 | 文件 |
|---|------|------|
| 1.1 | 创建项目骨架 + `requirements.txt` | `tools/tokenlens/` |
| 1.2 | 实现 `config.py`（路径、定价、环境变量 + 启动验证） | `config.py` |
| 1.3 | 实现 `pricing.py`（模型定价表，RMB 统一存储） | `pricing.py` |
| 1.4 | 实现 `format_utils.py`（数字格式化 + 时区）| `format_utils.py` |
| 1.5 | 实现 `parser.py`（递归扫描含 subagents + uuid 去重 + synthetic 过滤 + 幽灵记录日志） | `parser.py` |
| 1.6 | 编写单元测试 | `tests/test_parser.py`, `test_pricing.py`, `test_format_utils.py` |

### Phase 2：后端 API
| # | 任务 | 文件 |
|---|------|------|
| 2.1 | 实现 `advisor.py`（校准后的缓存规则 + LLM 建议 + 隐私标注） | `advisor.py` |
| 2.2 | 实现 `summary.py`（工作摘要 + 隐私标注） | `summary.py` |
| 2.3 | 实现 `server.py`（FastAPI 路由 + 静态文件 + 安全绑定） | `server.py` |
| 2.4 | 实现 `__main__.py`（CLI 入口，argparse + `--host 0.0.0.0` 警告） | `__main__.py` |

### Phase 3：前端看板
| # | 任务 | 文件 |
|---|------|------|
| 3.1 | 下载 Pico CSS v2.0.6 标准版到 static/ | `static/pico.min.css` |
| 3.2 | 实现 `index.html`（viewport + 响应式 grid + 表格横向滚动 + 空状态） | `static/index.html` |
| 3.3 | 实现 `app.js`（API 调用 + 数字缩写 + Tab 切换 + loading + 移动端检测） | `static/app.js` |

### Phase 4：验证
| # | 任务 |
|---|------|
| 4.1 | 启动服务 `python -m tools.tokenlens`，验证 `localhost:8090` + 启动日志 |
| 4.2 | 验证 5 个时间周期数据正确 |
| 4.3 | 验证缓存命中率与 JSONL 一致 |
| 4.4 | 验证 LLM 建议卡片生成 + 隐私标注显示 + 降级提示 |
| 4.5 | **移动端验证**：Chrome DevTools 模拟 iPhone SE / Pixel 5 / iPad |
| 4.6 | 验证 `<synthetic>` 过滤正确（对比原始 JSONL 行数） |
| 4.7 | 验证 project 聚合正确（按项目目录名而非 cwd，无碎片化子目录） |
| 4.8 | **验证子代理数据**：source=subagent 的 token 统计与手动汇总一致 |
| 4.9 | **验证项目过滤**：`?project=d--QQmaonian` 仅返回该项目数据 |
| 4.10 | **验证数据导出**：`/api/export?format=csv` 列完整且数值正确 |

---

## 验证方法

1. 启动服务后打开 `http://127.0.0.1:8090`
2. 观察统计卡片是否显示正确数值
3. 点击各时间 Tab，确认数据切换正确
4. 对比 `npx ccusage daily --json` 输出，验证 token 数字一致
5. 检查缓存命中率建议卡片是否符合预期场景（命中率 ~98% 应显示 💎 极佳）
6. 确认深色/浅色模式自动跟随系统
7. **移动端**：手机浏览器访问（同 WiFi 下 `--host 0.0.0.0` + 注意安全警告）
8. **移动端**：确认表格可横向滑动、卡片网格响应式折叠、Tab 栏可滑动
9. **移动端**：确认数字缩写显示（如 `2.00B`），点击/title 可看完整值

---

## 移动端访问指南

### 同 WiFi 局域网访问

```bash
# 电脑端启动（显示安全警告后确认）
python -m tools.tokenlens --host 0.0.0.0

# 查看电脑 IP（Windows: ipconfig, macOS/Linux: ifconfig）
# 例如 192.168.1.100

# 手机浏览器访问
http://192.168.1.100:8090
```

### 已验证的移动端支持

| 特性 | 实现 |
|------|------|
| Viewport | `<meta name="viewport" content="width=device-width, initial-scale=1.0">` |
| 响应式网格 | 桌面 4 列 → 平板 2 列 → 手机 1 列（CSS grid `@media`） |
| 表格横向滚动 | `overflow-x: auto` + `-webkit-overflow-scrolling: touch` |
| 触控优化 | 所有可点击元素 `min-height: 44px; min-width: 44px`（iOS HIG） |
| Tab 滑动 | `overflow-x: auto` + `white-space: nowrap` |
| 数字缩写 | 移动端显示 `2.00B`，`title` attribute 显示精确值 |
| 自动跟随系统主题 | Pico CSS `data-theme` 跟随系统 dark/light |

### 通过流量（4G/5G）远程访问

TokenLens 默认仅本地访问。若需要在非同一 WiFi 下通过手机流量查看，有三种方案：

#### 🟢 推荐：Tailscale（点对点 VPN）

最安全——数据不经过第三方服务器，端到端加密，手机体验和局域网完全一样。

```bash
# 1. 电脑和手机各安装 Tailscale（免费，https://tailscale.com）
# 2. 电脑启动 TokenLens
python -m tools.tokenlens --host 0.0.0.0

# 3. 手机 Tailscale App 中查看电脑的虚拟 IP（如 100.xxx.xxx.xxx）
# 4. 手机浏览器访问
http://100.xxx.xxx.xxx:8090
```

#### 🟡 备选：Cloudflare Tunnel + 一次性验证

适合没有 Tailscale 的场景，但流量经过 Cloudflare 服务器。

```bash
# 1. 安装 cloudflared
# 2. 创建隧道（绑定本地 8090）
cloudflared tunnel --url http://localhost:8090

# 3. 获得一个 https://xxxx.trycloudflare.com 地址
# 4. （可选）在 Cloudflare Zero Trust 中添加 Application，
#    开启一次性 PIN 码或邮箱验证以增加安全性
```

#### 🔴 不推荐：frp / ngrok 等公共隧道

这些服务的流量经过第三方服务器且通常无端到端加密，不适合承载完整的对话原始数据。

### 安全提醒

- ⚠️ `--host 0.0.0.0` 会使 JSONL 对话数据在同一 WiFi 网络内可访问
- ⚠️ **切勿**直接在路由器上做端口转发暴露到公网——TokenLens 无鉴权
- ✅ 远程访问首选 Tailscale（点对点加密，不经第三方）
- ✅ 仅可信网络使用，使用完毕后 `Ctrl+C` 停止

---

## 服务器部署（7×24 运行）

若需要在 Lighthouse 等云服务器上部署，实现关机也能手机流量访问：

### 前提条件

| 条件 | 说明 |
|------|------|
| 服务器配置 | 1 核 / 1GB 以上即可（实测 Lighthouse 4核4GB 绰绰有余） |
| 服务器 OS | Ubuntu 24.04（Python 3.12 开箱即用） |
| 数据同步 | 电脑和服务器之间需要 JSONL 双向同步 |

### 架构

```
电脑 (Syncthing 发送端)               Lighthouse 服务器
~/.claude/projects/ ────加密同步──▶ ~/tokenlens-data/
                                          │
                                          ▼
                                    TokenLens :8090
                                    (绑 127.0.0.1)
                                          │
                                          ▼
                              ┌─ Tailscale Serve (推荐) ─┐
                              │  HTTPS + Tailscale 加密   │
                              └──────────────────────────┘
                                          │
                                          ▼
                                      手机浏览器
```

**推荐方案：Tailscale Serve（无需域名/Nginx/certbot）**

```bash
# 服务器上一次性配置
tailscale serve --bg --https=8443 http://127.0.0.1:8090
# 自动获取 Let's Encrypt 证书，HTTPS + 端到端加密
# 手机访问 https://<tailscale-node-name>:8443
```

**备选方案：Nginx HTTPS + Basic Auth（需要已注册域名）**

```
TokenLens :8090 → Nginx :8443 (HTTPS + Basic Auth) → Tailscale VPN → 手机
```

两层安全：
1. **HTTPS + Basic Auth** — 防止扫端口直接访问
2. **Tailscale VPN** — 不暴露公网端口，点对点加密

### 部署步骤

#### Step 1：服务器安装依赖

```bash
ssh root@<your-server-ip>
apt install -y python3-pip syncthing
pip install fastapi uvicorn httpx
# Tailscale Serve 方案无需 Nginx/certbot
# 备选 Nginx 方案（需域名）：apt install -y nginx certbot python3-certbot-nginx
```

#### Step 2：Syncthing 同步 JSONL 数据

```bash
# 服务器端
syncthing &
# WebUI: http://127.0.0.1:8384
# 添加文件夹 ~/tokenlens-data/，与电脑共享

# 电脑端同样安装 Syncthing
# 添加文件夹 ~/.claude/projects/
# 与服务器共享（仅发送模式，防止服务器污染本地数据）
```

#### Step 3：部署 TokenLens

```bash
# 克隆仓库（或 rsync tools/tokenlens/ 到服务器）
cd /opt
git clone <repo>
cd /opt/QQmaonian

# 创建 systemd 服务
cat > /etc/systemd/system/tokenlens.service << 'EOF'
[Unit]
Description=TokenLens — Token Usage Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/QQmaonian
ExecStart=/usr/bin/python3 -m tools.tokenlens --data-dir /root/tokenlens-data --host 127.0.0.1 --port 8090
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now tokenlens
```

#### Step 4（推荐）：Tailscale Serve 一键 HTTPS

```bash
# 无需 Nginx、无需域名、无需 certbot
tailscale serve --bg --https=8443 http://127.0.0.1:8090
# 手机 Tailscale App 内直接访问 https://<server-tailscale-name>:8443
```

#### Step 4-备选：Nginx 反代 + HTTPS + Basic Auth

> ⚠️ 此方案需要**已注册/备案的域名**。Let's Encrypt 无法为裸 IP 签发证书。如无域名，请使用 Tailscale Serve 方案。

```bash
# 生成密码文件
htpasswd -c /etc/nginx/.htpasswd tokenlens

# Nginx 配置
cat > /etc/nginx/sites-available/tokenlens << 'EOF'
server {
    listen 8443 ssl;
    server_name your-domain.com;   # 必须是已注册域名！

    ssl_certificate     /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # 安全头
    add_header X-Frame-Options "DENY";
    add_header X-Content-Type-Options "nosniff";

    location / {
        auth_basic "TokenLens";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:8090;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

ln -s /etc/nginx/sites-available/tokenlens /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

#### Step 5：手机访问

```bash
# 手机安装 Tailscale，加入同一网络
# Tailscale Serve 方案（无需密码）：
https://<server-tailscale-name>:8443

# Nginx 备选方案（一次性输入 Basic Auth 密码后浏览器会记住）：
https://<tailscale-ip>:8443
```

### 服务器特有注意事项

- **`--data-dir`** 指向 Syncthing 同步目录（非默认 `~/.claude/projects/`）
- **数据刷新**：每次 API 请求自动检测：① 已知文件 mtime 变化 → 增量重解析 ② 文件列表变化（新 JSONL 出现）→ 自动加入追踪。也可手动 `GET /api/refresh` 强制全量重扫
- **安全方案**：推荐 Tailscale Serve（`tailscale serve --bg --https=8443 http://127.0.0.1:8090`），一键 HTTPS + 端到端加密，无需域名/Nginx/certbot
- **`/api/health`** 返回 `{"status": "ok", "last_scan": "...", "projects": 5, "projects_list": ["d--QQmaonian", ...]}`，用于 Uptime Kuma 等监控 + 前端项目下拉框
- **LLM 调用**：服务器端调用 DeepSeek API 做摘要/建议，需配置 `DEEPSEEK_API_KEY` 环境变量；也可设置 `TOKENLENS_LLM_ENABLED=false` 完全禁用
- **非 JSONL 文件**：项目目录中可能混入 `.md` 等文件（如 `session-*.md`），parser 自动跳过，在 `skipped_details.non_jsonl` 中计数

---

## 审计修正记录

### 八审（表格 + 时间分类 + GitHub 对标修复，2026-06-17）—— 本次

| # | 严重度 | 问题 | 修正 |
|---|--------|------|------|
| 80 | 🔴 严重 | **会话表格只显示单条消息数据**：`api_sessions()` 对每个 session 只保留首条记录，每行 token/cost 仅为一条 assistant 消息的值而非整个 session 总量 | 改为完整聚合：遍历所有 records → 按 session_id 累加 tokens/cost/msg_count → 显示 primary_model + models_used |
| 81 | 🟠 高 | **period 边界不一致**：`day` 对齐本地 00:00，但 `week/month/3month/year` 用 `now_utc - timedelta(days=N)` 不对齐本地时间（纯 UTC 偏移），导致 day 和 week 边界逻辑不对称 | `get_period_boundary()` 统一为所有 period 都对齐本地时间 00:00 再减去天数 |
| 82 | 🟡 中 | **无日期范围显示**：用户点击时间 Tab 后不知道实际查询的日期区间 | 新增 `get_period_label()` 函数返回 `{label, start, end}`，API 响应含 `period_label`；前端在 Tab 栏下方显示 "📅 过去7天（2026-06-10 ~ 2026-06-17）" |
| 83 | 🟡 中 | **cache-advice API 缺少 tz 参数**：前端调用 `/api/cache-advice` 时不传 tz，后端默认 +8 但前端状态可能不同步 | 后端增加 `tz` Query 参数，前端传入 `STATE.tz` |
| 84 | 🟢 低 | **定价源只有 DeepSeek + OpenRouter**：缺少社区维护的综合定价数据源（如 LiteLLM 1300+ 模型库） | 新增 `LiteLLMSource` 定价源（参考 ccusage 方案），从 `BerriAI/litellm` GitHub 获取 `model_prices_and_context_window.json`，三源优先级：DeepSeek 官方 > LiteLLM 社区 > OpenRouter |
| 85 | 🟢 低 | **FALLBACK_PRICING 与 _DEFAULT_PRICING 价格不同步**：`pricing_fetcher.py` 和 `pricing.py` 各自维护硬编码价格，已出现数值偏差 | 同步最新官方定价到两处（DeepSeek V4 Pro ¥3.15/¥0.026/¥6.31） |
| 86 | 🟢 低 | **死代码**：`server.py` 中 `_is_date_in_period()` 函数定义但从未调用；`api_hourly`/`api_tools` 中冗余局部 import `is_in_period` | 删除死代码和冗余 import |

### 八审增补（表格可用性 + 时间分类可用性改造，2026-06-17）

> 用户反馈"根本没有解决"，经排查后台 API 数据正确返回，但前端缺乏交互能力导致体感不可用。
> 以下改造将 TokenLens 从"能看数据"提升到"能用数据做决策"。

| # | 严重度 | 问题 | 修正 |
|---|--------|------|------|
| 87 | 🔴 严重 | **表格不可交互**：模型对比表和会话表无法排序，用户只能看后端默认排序（按费用降序），无法按 Token 数、命中率、消息数等维度重新排名 | 两个表头全部改为可点击排序：`<th data-sort="key">` + `makeSortHandler()` 封装排序状态管理；点击切换升序/降序；表头显示 ▲/▼ 箭头指示器；初始默认按费用降序 |
| 88 | 🔴 严重 | **无行内数据可视化**：表格只有纯数字，无法快速比较各行的相对大小（比如一眼看出哪个模型占用最多 Token） | 每个数字单元格底部添加行内柱状图（`.inline-bar`）：宽度 = 当前值/该列最大值 × 100%，颜色区分（蓝色=输入、绿色=缓存、金色=输出/费用）；hover 时柱状图加高至 5px |
| 89 | 🟠 高 | **无周期对比**：切换时间 Tab 后只能看当前周期数据，无法知道相比上个周期是涨了还是跌了 | 新增 `/api/stats/compare` 端点：自动计算上一同等周期数据（如选"周"则对比上周），返回当前/上周期/变化率三项；KPI 卡片下方显示增量标记（绿色 ↑+69% 或红色 ↓-50%） |
| 90 | 🟠 高 | **费用占比不直观**：模型对比表只显示绝对金额，不知道每个模型占总费用的百分比 | 模型表费用列追加百分比角标（`cost-pct`），如 `¥183.12 54.9%` |
| 91 | 🟡 中 | **会话表缺少详情入口**：只能看会话的时间/项目/模型/Token 数，无法查看具体用了哪些模型、session ID 等完整信息 | 会话表新增"详情"列，每行 📋 按钮点击弹窗显示完整 session 信息（session_id、所有模型列表、消息数、Token、费用） |
| 92 | 🟡 中 | **模型表缺少总 Token 列**：输入/缓存读/输出分开显示，无法一眼比较各模型的"总消耗" | 新增"总 Token"列（= 输入 + 缓存读 + 输出），带行内柱状图 |

### 十一审（定价准确性深度调查 + 三 Bug 修复，2026-06-17）

> 用户反馈费用不准。联网调研 ccusage/LiteLLM/DeepSeek 官方定价后，发现三个独立 bug，修复后周费用从 ¥343.09 → ¥337.18（-1.7%）。同时加固了硬编码保护逻辑，防止上游错误数据覆盖手动验证的定价。

#### 调研过程

参考了 **ccusage** 生态（Node.js/Rust CLI，LiteLLM 社区定价库 1300+ 模型）和 2026 年 LLM 成本追踪最佳实践（Future AGI、IBM Instana、Unsloth Studio）。关键发现：
- ccusage 的定价核心是 LiteLLM 的 `model_prices_and_context_window.json` + 离线缓存
- 业界标准成本公式需追踪 5 种 token 类型（input/cache_read/cache_write/output/reasoning），TokenLens 目前追踪 4 种
- DeepSeek 的官方人民币定价独立于美元定价（内部汇率 ~7.14，非市场汇率 7.25）

实测验证了 LiteLLM JSON 的 DeepSeek 条目：**V4 Pro/Flash 尚未被社区收录**，`deepseek-chat` 条目仍为旧 V3 定价（$0.28/M 输入）。

| # | 严重度 | 问题 | 数据验证 | 修正 |
|---|--------|------|---------|------|
| 104 | 🔴 严重 | **V4 Flash cache_read 定价错 2.2×**：硬编码用 $0.0012（不存在于任何官方文档），实际官方定价为 $0.0028。导致 cache_read 被低估 ¥0.009 vs 正确 ¥0.02 | 来源: api-docs.deepseek.com, cloudzero.com, vercel.com, aicost.tools — 全部确认 $0.0028 | 改为 ¥0.02（官方人民币）或 $0.0028×7.14=¥0.02。`pricing.py` 和 `pricing_fetcher.py` 的 `FALLBACK_PRICING` 同步修正 |
| 105 | 🔴 严重 | **DeepSeek 定价用 USD→RMB 市场汇率而非官方人民币**：官方人民币定价独立于美元价格（V4 Pro ¥3.00 而非 $0.435×7.25=¥3.15），市场汇率导致 ~5% 偏差 | DeepSeek API 文档标注 ¥3/¥0.025/¥6，内部汇率约 7.14 | 所有 DeepSeek 模型改用官方人民币定价。注释中保留 USD 参考价 |
| 106 | 🔴 严重 | **上游定价源覆盖硬编码默认值**：`pricing_fetcher.fetch_all()` 无条件用 OpenRouter/LiteLLM 数据覆盖硬编码 input/output。实测 OpenRouter 的 deepseek-v4-flash 价格为 ¥0.65（错误估算，正确 ¥1.00），kimi-k2.6 为 ¥4.93（正确 ¥6.89） | 运行 `--fetch-pricing` 后检查缓存文件：deepseek-v4-flash input=0.6525（正确 1.00），output=1.305（正确 2.00） | 修改合并策略：**硬编码值绝对优先**，上游数据仅用于添加新模型。`pricing_fetcher.fetch_all()` 和 `pricing._load_pricing()` 两处同步加固 |
| 107 | 🟠 高 | **LiteLLM `_MODEL_MAP` 映射了错误模型**：`deepseek/deepseek-chat` → `deepseek-v4-pro`。LiteLLM 中该条目为旧 V3 定价（$0.28/M），映射到 V4 Pro 会低估输入价格 35% | 实测 LiteLLM JSON 中无任何 deepseek-v4-pro/flash 条目 | 移除所有 DeepSeek V4 的 LiteLLM 映射，添加注释说明待 LiteLLM 社区收录后启用 |
| 108 | 🟡 中 | **`FALLBACK_PRICING` 与 `_DEFAULT_PRICING` 数值不一致**：十审 #102 已指出结构性问题，但两处注释中的 USD→RMB 换算细节不同步 | — | 同步修正两处所有 7 个模型的定价值 + 注释 |

#### 修复的有效性验证

| 验证项 | 方法 | 结果 |
|--------|------|------|
| 缓存值保护 | 删除缓存 → `--fetch-pricing` → 检查缓存 JSON | 硬编码 7 个模型值全保护，上游数据未覆盖 |
| 官方人民币定价 | `calc_cost("deepseek-v4-pro", 1M, 500K, 100K)` | ¥3.6125（input 3.00 + cache 0.0125 + output 0.60） |
| 测试一致性 | `python -m pytest tools/tokenlens/tests/ -v` | 61/61 通过 |
| API 费用输出 | `GET /api/stats?period=week` | ¥337.18（vs 修复前 ¥343.09） |
| 加载路径 | 启动日志 `定价已从缓存更新...已保护 7 个模型` | 确认硬编码保护生效 |

#### 未修复的已知局限

- **LiteLLM 暂未收录 DeepSeek V4**：待社区提交 PR 后取消注释 `_MODEL_MAP` 中的映射条目
- **cache_create 未计入费用**：DeepSeek 不收取缓存写费用（与 Anthropic 不同），但未来若支持 Claude 模型需添加
- **DeepSeekSource HTML 解析脆弱**：`api-docs.deepseek.com` 页面结构变化可能导致正则匹配失败，已有降级路径（LiteLLM → OpenRouter → 硬编码）

### 九审（GitHub 竞品深度调研 + 可借鉴方向，2026-06-17）

### 九审（GitHub 竞品深度调研 + 可借鉴方向，2026-06-17）

#### 调研范围

在 GitHub 上用 `token usage dashboard`、`LLM cost tracking`、`Claude Code monitor` 等关键词搜索，筛选出 **8 个最相关的开源项目**，逐一分析了它们的架构、定价策略、数据采集方式和 UI 设计。

#### 五大核心竞品

| 项目 | ⭐ | 技术栈 | 一句话描述 |
|------|-----|--------|-----------|
| **ccusage** | 16,300 | Rust + TypeScript | 行业标杆 CLI，`npx ccusage` 零配置出报告，支持 15+ AI 编程工具 |
| **tokscale** | 3,800 | Rust + React | 功能最花哨，TUI 终端界面 + Web 3D 贡献热力图 + 全球排行榜 |
| **tokencost** | 2,000 | Python 库 | 纯粹的价格计算库，传 token 数 + 模型名 → 返回 USD 费用，支持 400+ 模型 |
| **ccost** | 9 | Rust 单二进制 | 小而美，去重策略最讲究（requestId 优先），多币种 + 隐私模式 |
| **ClaudeCodeUsageDashboard** | 11 | TypeScript + Python | **跟 TokenLens 最像**：Web 看板 + SessionEnd Hook 自动采集 + 团队排行 |

#### 横向对比：TokenLens vs 五大竞品

| 能力维度 | ccusage | tokscale | ccost | CCDashboard | **TokenLens 现状** | 差距 |
|---------|---------|----------|-------|-------------|-------------------|------|
| Web 看板 | ❌ CLI only | ✅ TUI+Web | ❌ CLI only | ✅ React SSR | ✅ FastAPI + Vanilla JS | **领先** |
| 定价来源 | LiteLLM | LiteLLM+OpenRouter | LiteLLM | 硬编码 | **三源+LiteLLM+硬编码绝对优先** | **领先** |
| 自动采集 | ❌ | ❌ | ❌ | ✅ SessionEnd Hook | ❌ 手动扫描 | **落后** |
| 团队模式 | ❌ | ✅ 排行榜 | ❌ | ✅ 多人汇总 | ❌ 单机版 | **落后** |
| 去重策略 | UUID | UUID | ✅ **requestId 优先** | UUID | UUID + usage_sum 排序 | **可抄** |
| 隐私模式 | ❌ | ❌ | ✅ `--hidden` 匿名化 | ❌ | ❌ | **可抄** |
| 多币种 | USD only | USD only | ✅ 6 种货币 | USD only | RMB only | **可抄** |
| 分组维度 | 按 source | ✅ **6 种策略** | 按 project | 按 user | 按 project/source | **可抄** |
| 定价覆盖 | ❌ | ✅ custom-pricing.json | ✅ config.toml | ❌ | ✅ 环境变量覆盖 | 持平 |
| 表格排序 | ❌ | ✅ TUI 内 | ❌ | ❌ | ✅ 刚加 | 持平 |
| 周期对比 | ❌ | ❌ | ❌ | ❌ | ✅ 刚加 | **领先** |
| 移动端 | ❌ | ❌ | ❌ | ❌ | ✅ PWA + QR | **领先** |

#### TokenLens 独有的优势（不可替代）

1. **三源定价 + 硬编码绝对优先** — LiteLLM 1300+ 模型 JSON + OpenRouter API + DeepSeek 官方 HTML 三源抓取，但硬编码默认值（手动验证官方定价）永远不被上游错误数据覆盖。ccusage/tokscale/ccost 都直接信任 LiteLLM 单一源，无验证层。对于 DeepSeek 等有独立人民币定价的模型，直接使用官方 ¥ 定价，避免 USD→RMB 市场汇率偏差
2. **周期对比增量** — `/api/stats/compare` 自动对比上下周期，KPI 卡片显示 ↑↓ 百分比。五个竞品全都只展示当前周期数据，没有对比功能
3. **行内柱状图** — 表格数字格内嵌比例条，一眼看出各模型的相对消耗。五个竞品都只展示纯数字
4. **官方余额追踪** — `/api/billing` 通过 DeepSeek/Moonshot 官方 API 获取真实余额，用余额变化反推实际花费，对比本地估算偏差。竞品全靠 pricing × tokens 估算，无官方数据校验
5. **部署方案完整** — Tailscale Serve 一键 HTTPS + Syncthing 数据同步 + systemd 进程守护，文档覆盖本地/局域网/云服务器三种场景

#### 可借鉴的 8 项功能（按优先级排序）

| 优先级 | 功能 | 来源 | 实现思路 | 预估工时 |
|--------|------|------|---------|---------|
| 🔴 P0 | **自动采集** | ClaudeCodeDashboard | 注册 SessionEnd Hook → `hooks/session-uploader.py` → 每次会话结束自动 POST 到 TokenLens API → 自动触发增量刷新 | 4h |
| 🔴 P0 | **去重策略升级** | ccost | 当前用 uuid + usage_sum 排序去重（覆盖率 ~88%）。ccost 用 `requestId` 优先 + `sessionId` 兜底，号称去重率 ~18%。检查 JSONL 中是否有 `requestId` 字段，有则升级去重逻辑 | 2h |
| 🟠 P1 | **隐私模式** | ccost | `--hidden` 开关 + UI 按钮：开启后项目名替换为 `项目-01`/`项目-02`，方便截图分享到社区/社交媒体 | 1h |
| 🟠 P1 | **多币种切换** | ccost | 定价统一存 RMB，前端加币种选择器（CNY/USD/EUR/JPY），用固定汇率表转换显示。不做实时汇率以避免依赖外部 API | 2h |
| 🟠 P1 | **分组维度扩展** | tokscale | `/api/models` 增加 `?group_by=model|project+model|session+model` 参数，允许按不同粒度聚合。当前只有 project+model 二维 | 3h |
| 🟡 P2 | **定价覆盖机制** | tokscale | `~/.config/tokenlens/custom-pricing.json` 用户自定义定价文件，优先级高于缓存和硬编码。方便用户在 LiteLLM 未收录新模型时手动补充 | 1.5h |
| 🟡 P2 | **数据导出增强** | ccusage | `/api/export` 增加 `?format=ccusage` 格式（兼容 ccusage JSON schema），方便用户用 ccusage 和 TokenLens 交叉验证 | 1h |
| 🟢 P3 | **终端 CLI 报告** | ccusage | `python -m tools.tokenlens --cli --compact` 输出精简终端表格（类似 `npx ccusage daily`），适合 SSH 环境快速查看 | 2h |

#### 不做的事情

- ❌ **TUI 交互界面**（抄 tokscale）— TokenLens 定位是 Web 看板，TUI 与核心定位冲突，投入产出比低
- ❌ **3D 贡献热力图**（抄 tokscale）— 2D 热力图已足够表达时段分布，3D 增加复杂度但信息增量有限
- ❌ **全球排行榜**（抄 tokscale）— TokenLens 默认仅本地访问，排行榜需要中心化服务器，与"隐私优先"的设计理念冲突
- ❌ **多工具支持**（抄 ccusage）— TokenLens 专注 Claude Code JSONL 格式，支持 Codex/Gemini/Copilot 等会显著增加 parser 复杂度且用户暂无需求

| # | 来源 | 问题 | 修正 |
|---|------|------|------|
| 1 | 初审 | 去重用 `(sessionId, timestamp)` 不可靠 | 改用全局唯一 `uuid` |
| 2 | 初审 | 未指定文件编码 | `encoding='utf-8', errors='replace'` |
| 3 | 初审 | 未处理 `<synthetic>` 模型 | Parser 中过滤 |
| 4 | 初审 | 缓存命中率阈值 20/50/80 不符实际 | 校准为 60/80/95 + 趋势告警 |
| 5 | 初审 | `cache_create > 0` 规则永不触发 | 删除（第三方 API 恒为 0） |
| 6 | 初审 | 定价币种混乱（USD/RMB 混用） | 统一 RMB 存储 |
| 7 | 初审 | 多 worker 线程安全 | 文档注明 `--workers 1` |
| 8 | 初审 | 缺少隐私保护 | 默认 127.0.0.1 + `--host 0.0.0.0` 警告 + LLM 调用隐私标注 |

### 二审（移动端审计）

| # | 来源 | 问题 | 修正 |
|---|------|------|------|
| 9 | 二审计 | 移动端无 viewport meta | 添加 |
| 10 | 二审计 | Pico classless 无 grid/响应式 | 改用 Pico v2 标准版 |
| 11 | 二审计 | 触控目标 < 44px | 全部可点击元素 min 44×44px |
| 12 | 二审计 | 表格移动端无横向滚动 | `overflow-x: auto` + `-webkit-overflow-scrolling` |
| 13 | 二审计 | 数字在小屏不可读 | `formatTokensShort()` + title 显示完整值 |
| 14 | 二审计 | 无加载状态 | Pico `aria-busy="true"` skeleton |
| 15 | 二审计 | 空状态未设计 | 空数据占位符 + 模型 <100 条标注"样本不足" |
| 16 | 二审计 | 时区未处理 | API 参数 `tz=+8`，timestamp UTC→local |
| 17 | 二审计 | 项目同名冲突（多个同名仓库） | 用完整 `cwd` 而非 basename |
| 18 | 二审计 | 定价无法热更新 | 环境变量 `TOKENLENS_PRICING_JSON` 覆盖 |

### 三审（深度审计 — 实测验证，2026-06-17）

| # | 严重度 | 问题 | 数据验证 | 修正 |
|---|--------|------|---------|------|
| 19 | 🔴 严重 | `cwd` 碎片化：项目维度用 `record["cwd"]` 导致一个项目散成 7-8 个子目录分组 | 实测 8 种 cwd（大小写归一后 7 种），含 `D:\QQmaonian\skills\council\scripts` 等子路径 | **project 改为从扫描路径 `~/.claude/projects/<name>/` 提取**，cwd 仅保留展示（`.lower()` 归一化） |
| 20 | 🔴 严重 | UUID 非全局唯一：3,166/27,704 条 assistant 记录 UUID 重复（11.4%），其中 2 条为"幽灵记录"（usage 全 0）vs 真实记录 | 实测文件 `0582ab26-...jsonl`：UUID `c4d185b2` 出现两次，一条 input=95/output=365，一条全 0 | **按 `(uuid, -usage_sum)` 排序后去重**，优先保留有 token 消耗的版本 |
| 21 | 🟠 高 | 性能高估 3 倍：声称 0.3s，实测 ~1.0s | 137.8MB 实测 0.79s，173.6MB 预估 1.0s | 更新文档为实际值 |
| 22 | 🟠 高 | `cwd` 大小写不一致：`D:\QQmaonian` vs `d:\QQmaonian` | 实测两种写法共存于同一 session | `.lower()` 归一化 |
| 23 | 🟠 高 | 缺少 `json.JSONDecodeError` 处理：截断/损坏行会导致扫描崩溃 | — | 添加 try/except + skip 计数 |
| 24 | 🟡 中 | 缺少 `#` 注释行防御 | 当前数据未出现，但 Claude Code 变体可能包含 | 跳过 `line.startswith('#')` |
| 25 | 🟡 中 | `<synthetic>` 记录结构异常（含 `container`/`context_management` 字段），model 检查需前置于字段访问 | 实测 synthetic 与正常记录 message 结构不同 | model 检查放在 usage 提取之前 |
| 26 | 🟡 中 | 摘要生成隐私设计模糊：未明确 opt-in/opt-out | — | 建议默认关闭，手动触发，按钮旁标隐私提示 |
| 27 | 🟡 中 | `(sessionId, timestamp, model)` 组合键也不唯一（实测 4,999 条重复），不可作备用去重键 | 实测统计 | 文档记录 |
| 28 | 🟢 低 | 测试覆盖不足：仅 `test_parser.py` | — | 建议增加 advisor/pricing/server 测试 |
| 29 | 🟢 低 | 内存上限未讨论：173MB JSONL 解析后 ~200-400MB | — | 本地可接受，文档注明即可 |
| 30 | 🟢 低 | 缓存建议未按模型区分：`/api/cache-advice` 全局化，但各模型命中率差异大（94.2%–98.6%） | 实测 deepseek 98.1% vs kimi 94.2% | 可选添加 `?model=` 参数 |
| 31 | 🟢 低 | 静态文件无 `Cache-Control` 头 | — | FastAPI 添加 `max-age=3600` |
| 32 | 🟢 低 | 二审计 #17 修正（用完整 cwd 防同名）被三审计 #19 推翻 | 见 #19 | cwd 仅展示用，project 标识改用目录名 |

> **注**：#17（二审计）的"用完整 cwd 而非 basename"在三审计中被推翻——cwd 碎片化问题更严重。正确方案是 project 标识来源于扫描路径的目录名。

### 四审（服务器部署 + 一致性检查，2026-06-17）

| # | 严重度 | 问题 | 修正 |
|---|--------|------|------|
| 33 | 🔴 严重 | **缺少服务器部署方案**：方案全文假设纯本地运行，无数据同步、无鉴权加固、无进程守护 | 新增「服务器部署」章节：Syncthing 数据同步 + Nginx HTTPS Basic Auth + Tailscale VPN + systemd |
| 34 | 🟠 高 | 三审计修正遗漏：第 430 行增量刷新残留 0.3s（其他两处已改） | 修正为 1.0s |
| 35 | 🟠 高 | **代码示例不一致**：第 86 行 `record = json.loads(line)` 无 try/except，与第 132 行防御代码矛盾 | 第 86 行加入 `#` 检查 + try/except |
| 36 | 🟠 高 | **无数据刷新触发机制**：24×7 运行时，Syncthing 同步了新文件但无人访问 API 则数据永不过期 | 新增 `GET /api/refresh` 强制刷新；文档说明 mtime 自动检测机制 |
| 37 | 🟡 中 | "三个核心需求"实际列了 4 项 | 改为"四个核心需求" |
| 38 | 🟡 中 | 缺少 `/api/health`：服务器监控无法判断服务是否正常 | 新增端点 |
| 39 | 🟡 中 | `--data-dir` CLI 存在但 parser 模块未明确支持可配置路径 | 文档明确 `--data-dir` 传参给 Aggregator |
| 40 | 🟢 低 | 缓存建议未按模型区分：全局建议对命中率差异大的场景不准确 | `/api/cache-advice` 增加可选 `?model=` 参数 |
| 41 | 🟢 低 | 缺少 LLM 功能总开关：服务器可能不想调用外部 API | 环境变量 `TOKENLENS_LLM_ENABLED` 可禁用摘要和建议的 LLM 调用 |

### 五审（深度数据验证 + 架构完整性审计，2026-06-17）

| # | 严重度 | 问题 | 数据验证 | 修正 |
|---|--------|------|---------|------|
| 42 | 🔴 严重 | **子代理 Token 数据完全遗漏**：`subagents/` 子目录含 209 个 JSONL 文件（53MB）、402M tokens（RMB 122.95），占总费用 16.6%。`deepseek-v4-flash` 仅存在于子代理，定价表成死配置 | 实测 d--QQmaonian: 主会话 2,352M tokens / RMB 617.52 vs 子代理 403M tokens / RMB 122.95 | Parser 改用 `os.walk()` 递归扫描；提取 `source: "main"\|"subagent"` 维度；UI 提供切换开关 |
| 43 | 🔴 严重 | **摘要隐私设计文档内矛盾**：Body §4 描述自动生成，审计 #26 要求 opt-in，同一个文档两份冲突指令 | — | §4 正文改为显式 opt-in（默认关闭、手动触发、按钮旁隐私提示）；受 `TOKENLENS_LLM_ENABLED` 控制 |
| 44 | 🔴 严重 | **增量刷新无法发现新文件**：仅检查已知文件 mtime，Syncthing 同步的新 session JSONL 不会被自动发现，24×7 部署下数据可能永不过期 | — | 每次 API 请求增加 `os.listdir()` 轻量对比文件列表；新文件自动加入追踪 |
| 45 | 🔴 严重 | **API 缺少项目维度过滤**：所有聚合端点全局汇总，多项目用户无法拆分查看 | — | 所有核心端点增加可选 `?project=xxx` 参数；`/api/health` 返回项目列表供 UI 填充下拉框 |
| 46 | 🔴 严重 | **部署方案依赖域名但未声明**：Nginx + Let's Encrypt 需要已注册域名，Lighthouse 裸 IP 无法签发证书 | — | 新增 Tailscale Serve 一键 HTTPS 作为推荐方案（无需域名/certbot）；Nginx 降级为备选并标注域名前提 |
| 47 | 🟠 高 | **LLM 调用无错误降级**：advisor/summary 调用 DeepSeek API 无超时、无重试、无降级提示 | — | 添加 10s 超时 + 最多 2 次指数退避重试；失败时 UI 显示"暂不可用"，规则层建议仍正常输出 |
| 48 | 🟠 高 | **幽灵记录根本原因未排查**：2 条幽灵记录的真实版本均为完全相同的 111,308 tokens，疑似 Claude Code 系统性 bug | 实测 UUID `c4d185b2` 和 `6f403274`，真实版本 tokens 完全一致 | Parser 增加幽灵记录 warning 日志（含 sessionId + UUID），便于追踪上游修复 |
| 49 | 🟠 高 | **项目目录存在非 JSONL 文件**：`session-2026-06-07-music-feature.md` 混在 JSONL 目录中 | 实测 d--QQmaonian 目录中 1 个 .md 文件 | `os.walk()` 时显式过滤 `f.endswith('.jsonl')`；跳过统计中报告 `skipped_details.non_jsonl` |
| 50 | 🟠 高 | **启动时无配置验证**：`--data-dir` 不存在或不可读时服务静默启动，所有端点返回空数据 | — | `Config.validate()` 在 `__main__.py` 启动时检查目录存在性/可读性/JSONL 数量/DEEPSEEK_API_KEY |
| 51 | 🟠 高 | **摘要无消息截断**：用户消息可能含大量代码（10K+ tokens），拼接后超出 DeepSeek 上下文窗口 | — | 每条 user 消息截断为前 500 字符，总长度限制 8K 字符 |
| 52 | 🟡 中 | **kimi-k2.6 样本量不足但未被标记**：182 条消息低于统计可靠性门槛，但旧阈值 <100 未能标记 | 实测 kimi 182 条消息，95% 置信区间宽度约 ±3% | 阈值提高为 <300 条；低样本命中率用灰色 `~` 前缀 + hover 提示 |
| 53 | 🟡 中 | **LLM Advisor 在极高缓存命中率下价值有限**：三模型命中率 94%-99%，规则引擎已输出"极佳"，LLM 几乎永不触发（需变化 >10%） | 实测命中率 98.1% / 98.6% / 94.2% | LLM 增强标注为"实验性"，默认关闭；规则引擎作为主要输出 |
| 54 | 🟡 中 | **无数据导出功能**：用户无法导出 CSV/JSON 做外部分析 | — | 新增 `/api/export?format=csv&period=week` 端点 |
| 55 | 🟡 中 | **时间 Tab 边界定义不明确**："1天"是过去 24h 还是今天？"1周"是自然周还是滚动窗口？ | — | 明确所有 period 为滚动窗口：day=今天00:00–now, week=过去7×24h, month=30×24h, 3month=90×24h, year=365×24h |
| 56 | 🟢 低 | 测试覆盖单薄：仅 `test_parser.py`，缺少 pricing/format_utils/api 测试 | — | 新增 `test_pricing.py` + `test_format_utils.py`；API 集成测试留待 Phase 4 |
| 57 | 🟢 低 | 前端无键盘导航：Tab 栏缺少 `role="tablist"` / 键盘事件 | — | 留待后续迭代（移动端优先，触控为主要交互方式） |
| 58 | 🟢 低 | Pico CSS 版本未锁定：方案写"v2 标准版"无精确版本号 | — | 锁定 `v2.0.6`，记录下载 URL 和完整性校验 |
| 59 | 🟢 低 | 移动端数字缩写精度损失：`2,001,359,360 → 2.00B` 丢失 0.06% 精度 | — | 可接受（对应费用误差约 ¥0.20），已通过 `title` attribute 提供精确值 |
| 60 | 🟢 低 | 静态资源缓存策略未落地：审计 #31 已指出但正文未体现 | — | 在 server.py 静态文件挂载处添加 `Cache-Control: public, max-age=3600` |

> **注**：五审中最关键的发现是 #42（子代理数据遗漏）—— 仅 `d--QQmaonian` 一个项目就遗漏了 RMB 122.95（16.6%）的 API 费用。若用户使用 Workflow/多 Agent 功能频繁，实际费用可能被系统性低估 15-20%。

### 六审（定价修正 + 官方账单集成，2026-06-17）

| # | 严重度 | 问题 | 数据验证 | 修正 |
|---|--------|------|---------|------|
| 61 | 🔴 严重 | **cache_read 定价系统性高估 5-10 倍**：原 hardcoded 定价中 cache_read = input/10，但实际官方定价 cache_read 极低（DeepSeek ¥0.025/百万token，仅为 input 的 0.8%） | 实测对比：deepseek-v4-pro cache_read 从 ¥0.20 → ¥0.025（8 倍偏差）。总费用从 ¥693.91 → ¥418.04（-40%） | 更新所有模型为官方定价；新增多源自动获取（pricing_fetcher.py）；硬编码 cache_read 保护不被 OpenRouter 估算值覆盖 |
| 62 | 🔴 严重 | **定价无自动更新机制**：定价硬编码在源码中，官方调价后无从感知，费用计算持续偏差 | — | 新增 `pricing_fetcher.py`：多源抓取（DeepSeek HTML + OpenRouter JSON）→ 24h 缓存 → 降级到硬编码默认值。新增 `--fetch-pricing` / `--show-pricing` CLI + `/api/pricing` 端点 |
| 63 | 🟠 高 | **本地估算无官方数据验证**：TokenLens 的"花费"由 pricing × usage 计算，但无任何独立数据源验证其准确性 | — | 新增 `billing_fetcher.py`：通过 DeepSeek/Moonshot 官方余额 API 追踪余额变化（余额减少 = 实际花费）。`/api/billing` 端点对比官方花费 vs 本地估算，偏差 >20% 告警 |
| 64 | 🟡 中 | MiMo 无官方余额 API：小米 MiMo 计费系统尚未正式上线，无法验证其定价准确性 | — | billing_fetcher 仅覆盖 DeepSeek + Moonshot；MiMo 定价沿用官方公告的降价后价格（对标 DeepSeek），留待后续验证 |
| 65 | 🟢 低 | OpenRouter cache_read 估算不准确：OpenRouter 不暴露 cache_read 价格，其估算值（input/50）与实际偏差大 | — | pricing_fetcher 的 OpenRouter 源不设置 cache_read；合并时保护硬编码 cache_read 值 |

### 七审（前端 Bug 修复 + 功能增强 + GitHub 借鉴，2026-06-17）

| # | 严重度 | 问题 | 数据验证 | 修正 |
|---|--------|------|---------|------|
| 66 | 🔴 严重 | **趋势图不可见**：CSS `!important` 覆盖了 Chart.js canvas 内联样式，导致画布高度为 0（用户报告「token每日趋势图看不了」） | Chart.js 内部设置 canvas 内联宽高，CSS `width:100%!important` 覆盖后画布坍塌 | 移除 `!important`；改用 `display:block;max-width:100%`；添加 `safeCreateChart()` 包装器（try/catch + 错误可视化）；图表失败时显示可见错误提示 |
| 67 | 🔴 严重 | **费用计算偏差 ~5%**：DeepSeek 官方定价已更新（V4 Pro: $0.435 Miss / $0.003625 Hit / $0.87 Output），硬编码值偏低 | 旧值 ¥3.0/¥0.025/¥6.0 vs 新值 ¥3.15/¥0.026/¥6.31，周费用差 ¥6（¥303 → ¥309） | 更新所有模型定价为官方最新值；确认 `input_tokens` 不含缓存 token（实测 102 in vs 19328 cache_read），无双重计费；`/api/pricing` 端点可供验证 |
| 68 | 🔴 严重 | **时间 Tab 切换无效**：`Aggregator.get_models()` 返回全量数据，不按 period 过滤（尽管 server 端已接收 period 参数） | 用户报告「天数分类还是看不了」— 点击不同时间按钮数据不变 | 新增 `get_models_by_period(period, tz, project)` 方法；新增 `get_daily_trend()` 方法；server 所有端点改用新方法；前端 `switchPeriod()` 添加去重检查 + 图表销毁前清除 |
| 69 | 🔴 严重 | **CDN 资源在境内可能被墙**：Chart.js/qrcodejs 从 jsdelivr CDN 加载，部分中国 ISP 间歇性阻断 | 测试时 jsdelivr 可访问，但用户环境可能不同 | 添加 CDN 加载 `onerror` 回调显示友好错误；未来考虑本地化 Chart.js bundle（~200KB） |
| 70 | 🟠 高 | **Canvas 维度冲突**：Chart.js v4 通过内联样式管理 canvas 尺寸，CSS `!important` 规则优先级高于内联样式 | — | 移除所有 canvas 的 `!important` 规则；Canvas 只设 `display:block; max-width:100%`，剩余由 Chart.js 管理 |
| 71 | 🟠 高 | **缺少工具调用分析**：数据包含丰富的 tool_use 信息（Read/Bash/Grep/Edit 等），但前端无展示 | 实测周度工具调用：Read 3970 / Bash 2102 / Grep 1422 / Edit 1257 / MCP 879 次 | 新增 `/api/tools` 端点（重新扫描 JSONL 提取 content block tool_use）；新增横向柱状图展示 Top 12 工具 |
| 72 | 🟡 中 | **无时段热力图**：用户无法看到一天中哪个时段 Token 消耗最高 | 实测峰值时段含 245M tokens/h | 新增 `/api/hourly` 端点（按本地小时聚合）；新增热力图（24 小时柱状图，渐变色彩表示用量密度） |
| 73 | 🟡 中 | **无每日费用趋势**：只有每日 Token 趋势，缺少费用维度的趋势图 | — | 新增费用趋势图（双 Y 轴：每日费用柱状 + 累计费用折线） |
| 74 | 🟡 中 | **无 KPI 动画**：统计数字瞬间跳变，用户可能未察觉数据变化 | — | 新增 `animateValue()` 函数（easeOutCubic 缓动，800ms count-up） |
| 75 | 🟡 中 | **无分享/导出卡片**：用户想截图分享用量时需手动截屏 | — | 新增分享卡片 section（Canvas 2D 绘制 PNG 下载 + 文本复制到剪贴板） |
| 76 | 🟡 中 | **无 PWA 支持**：手机浏览器访问缺少"添加到主屏幕"能力 | — | 新增 `manifest.json`；apple-mobile-web-app meta 标签；主题色 meta |
| 77 | 🟡 中 | **定价透明度不足**：用户无法确认当前使用的定价来源 | — | `/api/pricing` 返回 `meta.source` / `fetched_at` / `usd_to_rmb`；前端工具栏显示官方 vs 本地费用对比 |
| 78 | 🟢 低 | 测试文件导入路径问题：从 `tools/` 目录运行 `pytest tokenlens/tests/` 时 import 失败 | — | 文档注明需从项目根目录运行：`python -m pytest tools/tokenlens/tests/` |
| 79 | 🟢 低 | `__main__.py` 打印含 emoji/中文，Windows GBK 控制台报 `UnicodeEncodeError` | — | 包装 stdout：`io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')` |

### 十审（代码 vs 计划一致性深度审查 + 全部修复，2026-06-17）

> 逐文件对比 `tokenlens-plan.md` v9 设计文档与实际代码实现，发现 11 项偏差——从导致前端核心功能报废的重复函数定义，到后端 API 空壳实现。所有问题已修复，61 个测试全部通过。

| # | 严重度 | 文件 | 问题 | 修正 |
|---|--------|------|------|------|
| 93 | 🔴 严重 | `app.js:513-562` | **函数重复定义导致排序/详情/柱状图全部失效**：`renderSessionsTable()` 在第 432 行定义完整版（7 列含排序+行内柱状图+详情按钮），第 541 行又被精简版（6 列无排序无详情）覆盖。`renderAdvice()` 同样重复（485 vs 513）。由于 JS 函数声明 hoisting，第二个定义完全覆盖第一个。同时 `_sortModelHandler` / `_sortSessionHandler` 仅在第一版中初始化 → 永远为 null → `initTableSort` 传入空函数 → 两个表格点击排序静默失效 | 删除第 513-562 行（孤立的 renderAdvice 代码块 + 重复的 renderSessionsTable） |
| 94 | 🔴 严重 | `server.py:426` | **`/api/summary` 是空壳**：计划描述完整的 LLM 摘要生成（提取 user 消息→截断→DeepSeek API），但端点直接返回 `"error": "not_yet_implemented"`。`summary.py` 的 `generate_summary()` 完整可用但从未被调用 | 实现完整的摘要流程：扫描 JSONL 提取该 session 的 user 消息 → 调用 `generate_summary()` → 缓存结果 |
| 95 | 🔴 严重 | `server.py:378` | **`primary_model` 按字母序选择而非按使用量**：`max(s["models"], key=lambda m: m)` 选字母序最大的模型名（如 `mimo-v2.5-pro` 永远排在 `deepseek-v4-pro` 之后），而非最常用的模型 | 在聚合循环中增加 `model_msgs` 字典追踪每个模型的消息数；`primary_model` 改为 `max(s["model_msgs"], key=lambda m: s["model_msgs"][m])` |
| 96 | 🔴 严重 | `server.py:261-264` + `parser.py:434` | **source 过滤只在模型级别剔除，token 计数仍含全部来源**：`api_models` 选 `source=main` 时仅过滤 `source_main > 0` 的模型，但保留模型的 input/output/cache_read 仍包含子代理数据。用户看到的是"含子代理 token 的模型列表，仅排除了纯子代理模型" | `get_models_by_period()` 新增 `source` 参数，在聚合前按 `r["source"]` 过滤记录；`api_models` 直接传参而非后过滤 |
| 97 | 🟠 高 | `server.py:472` | **`api_cache_advice` 缺少 `?project=` 参数**：所有其他端点支持项目过滤，唯独 cache-advice 调用 `get_models_by_period(period, tz)` 时 project 硬编码为 None。用户选特定项目时 AI 建议仍基于全局数据 | 添加 `project: str \| None = Query(None)` 参数并传入 `get_models_by_period` |
| 98 | 🟠 高 | `server.py:143` | **`api_stats_compare` 冗余变量 + 低效 import**：`prev_boundary` 计算后从未使用；每行记录用 `__import__("datetime").datetime.fromisoformat(...)` 解析时间戳（每次调用重新导入模块） | 删除 `prev_boundary`；改用顶部已导入的 `datetime.fromisoformat` 和 `timezone.utc` |
| 99 | 🟡 中 | `server.py:103-106` | **死代码**：`api_stats` 中 `all_sessions = set()` 构建后仅含 `pass`，从未使用 | 删除 4 行死代码 |
| 100 | 🟡 中 | `server.py:641-692` | **`api_pricing` 中内联 import 脆弱**：函数体内 `from .pricing import PRICING, CACHE_PATH` 每次请求重新导入；`__import__("os")` 绕过了顶部 `import os` | 将 `PRICING`/`CACHE_PATH`/`calc_cost`/`reload_pricing` 提升到模块顶部导入；移除所有函数内 `from .pricing import ...` 和 `__import__(...)` |
| 101 | 🟢 低 | `test_format_utils.py:91-95` | **`is False` / `is True` 断言不惯用**：`assert x is False` 依赖 Python 布尔单例，虽能工作但不符合 pytest 惯例 | 改为 `assert not x` 和 `assert x` |
| 102 | 🟢 低 | `pricing_fetcher.py:39-54` | **`FALLBACK_PRICING` 与 `pricing.py:_DEFAULT_PRICING` 内容重复**：两处维护相同的 7 个模型硬编码价格，八审 #85 已指出但仅同步了数值，未解决结构性问题 | 在 `FALLBACK_PRICING` 上方添加同步维护注释，明确 `pricing.py` 为权威来源 |
| 103 | 🟢 低 | `app.js:1040-1048` | **resize 事件触发 9 个 API 请求**：窗口大小变化 → 500ms debounce → `destroyAllCharts()` + `loadAll()`（9 个并行 API 请求）。注释写"只重建图表，不重新加载数据"但代码实际调用了 `loadAll()` | 新增 `_cachedChartData` 缓存最近一次图表数据；resize 时仅调用 `renderAllCharts()` 使用缓存数据重建图表，不发起网络请求 |

> **注**：十审最关键的发现是 #93（前端函数重复定义）—— 这是一个静默 bug：无报错、无崩溃、数据正常加载，但表格排序（▲/▼ 箭头）、行内柱状图、会话详情按钮三个功能完全不可用。用户体感就是"表格不能排序"。根因是编辑时复制粘贴后忘记删除旧版本。

