# Council Skill 实施报告

> 最后更新：2026-06-16 | 状态：**✅ 全部完成 — Kimi k2.6上线 + CI/CD就绪 + 环境变量正常 + 59测试全过**

---

## 一、已完成

### 文件清单

```
%USERPROFILE%\.agents\skills\council\
├── SKILL.md                    ✅ Skill 定义（自然语言 + /council 触发）
├── models.json                 ✅ 模型注册表（新增模型只需改此文件）
├── .env                        ❌ 已迁移至系统环境变量（备份为 .env.bak）
├── .env.example                ✅ 示例配置
├── requirements.txt            ✅ 依赖声明（httpx + tiktoken）
├── prompts/
│   ├── __init__.py
│   ├── review_prompts.py       ✅ Round 1 四模型人格化 Prompt（Architect/Auditor/Skeptic/Pragmatist）
│   ├── critique_prompts.py     ✅ Round 2 交叉验证 Prompt
│   └── judge_prompt.py         ✅ Round 3 Chairman 裁决 Prompt + 质量门控
└── scripts/
    ├── __init__.py
    ├── utils.py                 ✅ 通用工具（日志/token计数/进度条/门控提取）
    ├── config.py                ✅ 配置加载（models.json + .env → 运行时配置）
    ├── api_client.py            ✅ API调用 + JSON解析 + 消息构建
    ├── council_call.py          ✅ CLI入口 + 编排(R1/R2/R3) + 去重 + 报告 (~870行)
    ├── test_boundary.py         ✅ 边界测试套件（59 测试，10 类）
    └── verify_dedup.py          ✅ 去重算法验证（20 组标注数据）
```

### 核心功能（3 种模式）

| 模式 | 调用 | 耗时（2 模型） | 耗时（3 模型） | 裁决 | 适用 |
|------|------|---------------|---------------|------|------|
| `--mode=fast` | 2~3 次 | ~12-22s | ~25s | ❌ | 小改动扫描 |
| `--mode=debate` | 4~6 次 | ~30s | ~150s | ❌ | 一般方案 |
| `--mode=deep` | 5~7 次 | ~112s | ~360s | ✅ | 架构决策 |

> 💡 **日常推荐**：`--mode=fast --models deepseek,kimi`（~12-22s，¥0.01），
> Mimo 单次 90-160s，仅在需要 Pragmatist 视角时使用三模型。

### 两模型 Fast 模式实测 (2026-06-16)

```
22.1s | 3,963 tokens | ¥0.01
├── R1: Kimi 12.5s ✅ | DeepSeek 22.1s ✅
└── 报告: 两个模型审查结果均已输出，质量良好
```

### 两模型 Deep 模式实测 (2026-06-16)

```
112.5s | 22,039 tokens | ¥0.08
├── R1: DeepSeek 21.5s ✅ | Kimi 11.1s ✅
├── R2: deepseek→kimi ✅ | kimi→deepseek ✅
├── 去重: 12 个独立问题
└── R3: Chairman (deepseek-v4-pro) 60.5s ✅ → BLOCK
```

### 关键修复记录

| ID | 描述 | 状态 |
|----|------|------|
| F1 | 移除 `load_config()` 对 `d:\QQmaonian\DeepSeekQQ\.env` 的外部依赖，Skill 完全自包含 | ✅ |
| F2 | Round 2 检查 `target_report.parse_failed`，跳过交叉验证并标注 `uncertain` | ✅ |
| F3 | Chairman 降级链: deepseek-v4-pro → deepseek-v4-flash（自动切换） | ✅ |
| F4 | 2-策略 JSON 解析：先无修改直接解析，失败再弯引号标准化 | ✅ |
| F5 | Jaccard 去重阈值 0.8 → 0.35（20组标注验证：召回 0%→50%，精确 100%） | ✅ |
| F6 | SKILL.md 自然语言触发词扩充 | ✅ |
| Mimo 超时 | 默认 120s → 180s + 方案 >8K 截断（保留首尾各 4K） | ✅ |
| F7 | verify_dedup.py 硬编码 0.8 → 导入 `JACCARD_THRESHOLD` 常量 (0.35) | ✅ |
| F8 | 无效 --models 参数 KeyError → 友好报错 + sys.exit(1) | ✅ |
| F9 | max_tokens 8192 → 4096 优化（推理模型减半，Chairman 131s→60s） | ✅ |
| F10 | SKILL.md + CLI help 耗时表更新为实测数据 | ✅ |
| F11 | kimi-k2.6 强制 `temperature=1`，models.json 添加 overrides 修复 400 错误 | ✅ |
| F12 | `test_judge_fallback_triggered` 添加 `@patch('os.getenv')` 修复 Fallback 链 Key 读取 | ✅ |
| F13 | `_call_one_r2` 返回 tuple 但调用方未解包 → `'tuple' object has no attribute 'get'` 崩溃修复 | ✅ |
| F14 | 4 个模块（api_client/council_call/test_boundary/verify_dedup）新增 `SCRIPT_DIR` 到 sys.path | ✅ |
| F15 | `call_model` 异常处理新增 HTTP 响应体提取，400 错误可看到具体原因 | ✅ |
| F16 | `.env.example` 缺少 MiniMax 环境变量模板（`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`） | ✅ |
| F17 | `DeepSeekQQ/plugins/deepseek/config.py` 缺少 MiniMax 配置块，补全 `MINIMAX_API_KEY/BASE_URL/MODEL` | ✅ |

### 模块化重构 (2026-06-16)

原有 `council_call.py`（~1450 行）拆分为 4 个模块：

| 模块 | 行数 | 职责 |
|------|------|------|
| `utils.py` | ~120 | 日志 / tiktoken 精确计数 / 文本截断 / 进度条+ETA / 门控提取 |
| `config.py` | ~170 | 配置加载 / models.json 解析 / 运行时常量 / Key 安全提醒 |
| `api_client.py` | ~340 | API 调用 / JSON 解析(2策略+弯引号) / JSON 重试 / 消息构建 |
| `council_call.py` | ~870 | CLI 入口 / Round1-3 编排 / 流水线化 / 去重 / 报告生成 |

### 9 项优化记录 (2026-06-16)

| 优先级 | ID | 描述 | 状态 |
|--------|----|------|------|
| 🔴 P0 | O1 | **门控结果不一致**：`evaluate_gate()` 数学计算 vs Chairman 报告输出可能矛盾。修复：优先从 Chairman 报告提取门控（`extract_gate_from_report()`），提取失败才 fallback 到数学计算 | ✅ |
| 🔴 P0 | O2 | **JSON 解析失败不重试**：新增 `call_model_with_json_retry()`，解析失败时发送修正消息让 AI 重新输出，实测多数 JSON 问题重试一次可解决 | ✅ |
| 🔴 P0 | O3 | **文件大小无上限**：新增 `MAX_PLAN_CHARS = 200_000`，超大文件直接拒绝，防止 OOM 和 API 额度耗尽 | ✅ |
| 🟡 P1 | O4 | **Round 1→2 串行等待**：新增 `run_round1_and_2_pipelined()`，快模型(Kimi ~11s)的交叉验证在慢模型完成前即启动，总耗时减少 20-30% | ✅ |
| 🟡 P1 | O5 | **模型配置硬编码**：新增 `models.json` 模型注册表，新增模型只需编辑 JSON，不改 Python 代码。`load_config()` 动态遍历 `model_registry` 构建运行时配置 | ✅ |
| 🟡 P1 | O6 | **Token 计数靠 char/2 估算**：集成 tiktoken (`cl100k_base`)，`count_tokens()` 和 `truncate_to_tokens()` 精确计数，误差从 ±30% 降到 ±5%。未安装时自动回退 | ✅ |
| 🟢 P2 | O7 | **无进度条**：新增 `ProgressTracker` 类，R1+R2 流水线和 R3 裁决均显示 `[████░░░░] 3/6 \| 45s \| 剩余 ~30s` 格式进度 | ✅ |
| 🟢 P2 | O8 | **单文件 1450 行**：拆分为 utils / config / api_client / council_call 四个模块，主文件缩减 40% | ✅ |
| 🟢 P2 | O9 | **API Key 明文存储无提醒**：新增 `_check_key_security()`，启动时检测 `.env` 明文 Key 并输出安全提醒，引导用户迁移至系统环境变量 | ✅ |

### Key 迁移：.env → 系统环境变量 (2026-06-16)

| 变量 | 存储位置 | 状态 |
|------|---------|------|
| `DEEPSEEK_API_KEY` | Windows 系统环境变量 (`setx`) | ✅ 已迁移 |
| `KIMI_API_KEY` | Windows 系统环境变量 (`setx`) | ✅ 已迁移 |
| `MINIMAX_API_KEY` | Windows 系统环境变量 (`setx`) | ✅ 已迁移 |
| `MIMO_CHAT_API_KEY` | Windows 系统环境变量 (`setx`) | ✅ 已迁移 |
| `COUNCIL_JUDGE_API_KEY` | Windows 系统环境变量 (`setx`) | ✅ 已迁移 |
| `.env` 文件 | 重命名为 `.env.bak`（备份） | ✅ 已禁用 |
| `DeepSeekQQ/plugins/deepseek/config.py` | 新增 Kimi / MiniMax / Mimo Chat 配置块 | ✅ 已集成 |

读取优先级：**系统环境变量 > Skill .env > models.json 默认值**

### 三模型 Deep 模式实测（优化前参考）

```
360.3s | 120,054 tokens | ¥0.28
├── R1: DeepSeek 48s ✅ | Kimi 11s ✅ | Mimo 157s ✅
├── R2: mimo→deepseek ✅ | deepseek→kimi ⚠️ JSON | kimi→mimo ⚠️ JSON
├── 去重: 18 个独立问题
└── R3: Chairman 131s ✅（首选 deepseek-v4-pro）
```

---

## 二、已知限制

| 优先级 | 问题 | 详情 |
|--------|------|------|
| 🟡 P1 | Kimi k2.6 JSON | 已切换至 kimi-k2.6（temperature=1 修复完成）。实测 R1 JSON 成功率 ~50%（和旧模型持平），JSON 修正重试偶有 400。DeepSeek 始终稳定，推荐 `--models deepseek,kimi --mode=fast` |
| 🟡 P1 | Mimo 慢 | v2.5-pro 推理 token 占 99%+，单次 88~157s。O4 流水线化可部分缓解（快模型的交叉验证不等待 Mimo）。两模型模式跳过 Mimo 可提速 3× |
| 🟢 P2 | 成本估算粗糙 | dry-run 用 tiktoken 精确计算，实际调用以 API 返回的 usage 为准 |
| 🟢 P2 | Python 3.14 + pytest | `python -m pytest` 因 pytest 兼容性 crash，用 `python test_boundary.py` 可绕过。CI 固定 Python 3.11/3.12 |

---

## 三、完成记录

### 边界测试 ✅ (2026-06-16)
- [x] 缺 Key 模型 → 跳过并继续 — `TestBoundaryMissingKey`
- [x] `--models deepseek` 单模型 → 跳过 R2/R3 — `TestBoundarySingleModel`
- [x] `--models deepseek,kimi` 两模型 fast/debate/deep — `TestBoundaryTwoModels`
- [x] 全部模型失败 → 优雅报错 — `TestBoundaryAllModelsFail`
- [x] JSON 解析边缘情况 — `TestJsonExtraction` (7 用例)
- [x] 去重算法 — `TestDeduplication` (6 用例)
- [x] 裁决降级链 — `TestJudgeFallback` (4 用例)
- [x] Mimo 特殊处理 — `TestMimoHandling` (2 用例)
- [x] Dry-run / 报告生成 / 配置加载 / 成本估算 / 交叉配对 — 各 2~3 用例
- [x] 模型名校验 — `TestConfigLoading.test_supported_models_constant` + `test_invalid_model_rejected`
- [x] **59 个测试全部通过**

### 安全审计 ✅ (2026-06-16)
- [x] `grep -r "sk-" *.md *.py *.json` 确认无 Key 泄漏（仅测试文件含假 Key）
- [x] .env 仅存在于 Skill 目录，不参与版本控制
- [x] 无硬编码绝对路径依赖

### 去重算法验证 ✅ (2026-06-16)

20 组中文 issue 标题对标注测试：

| 阈值 | 召回率 | 精确率 | F1 |
|------|--------|--------|-----|
| 0.8（旧） | 0% | — | 0% |
| **0.35（当前）** | **50%** | **100%** | **67%** |
| 0.3 | 60% | 100% | 75% |

结论：纯 CJK 2-gram 对中文语义去重能力有限。当前采用 0.35 保守阈值（宁漏勿错），剩余漏网之鱼由 Round 3 Chairman 语义合并兜底。

### 可选增强
- [x] `requirements.txt`（httpx + tiktoken 依赖声明）
- [x] 去重算法验证脚本（`verify_dedup.py`）
- [x] 去重阈值修复（0.8 → 0.35，提取为模块常量）
- [x] SKILL.md 自然语言触发词扩充（11 种中文表达）
- [x] verify_dedup.py 阈值同步（导入 `JACCARD_THRESHOLD` 常量）
- [x] 模型名校验（`SUPPORTED_MODELS` + 友好报错 → O5 改为 models.json 动态加载）
- [x] max_tokens 优化（8192 → 4096，实测 Chairman 加速 54%）
- [x] SKILL.md + CLI help 耗时更新为实测数据
- [x] 模块化拆分（O8：utils / config / api_client / council_call 四模块）
- [x] tiktoken 精确计数（O6：dry-run + truncate_to_tokens）
- [x] JSON 解析失败自动重试（O2：call_model_with_json_retry）
- [x] 进度条 + ETA 估算（O7：ProgressTracker）
- [x] Round 1→2 流水线化（O4：run_round1_and_2_pipelined）
- [x] 门控一致性修复（O1：extract_gate_from_report）
- [x] API Key 安全提醒 + 系统环境变量迁移（O9）
- [x] models.json 模型注册表（O5：新增模型不改 Python）
- [x] Kimi 换 kimi-k2.6 测试 → 见下方 2026-06-16 更新
- [x] CI/CD 集成（GitHub Actions + pytest，59 测试全过）

### 2026-06-16 最新更新

**Bug 修复 (5 项)**：
| ID | 描述 |
|----|------|
| F11 | kimi-k2.6 强制 `temperature=1`，`models.json` 添加 `overrides: {"temperature": 1.0}`，修复 400 错误 |
| F12 | `test_judge_fallback_triggered` Fallback 链改用 `os.getenv` 读 Key 后测试挂掉，添加 `@patch('os.getenv')` 修复 |
| F13 | `_call_one_r2` 返回 `(key, result)` 元组但调用方未解包，pipeline 崩溃修复 |
| F14 | 4 个模块新增 `sys.path.insert(0, str(SCRIPT_DIR))`，支持从任意目录运行 |
| F15 | `call_model` 异常处理新增 HTTP 响应体提取，400 错误不再只显示 "Bad Request" |

**Kimi k2.6 实测结论**：
- R1 JSON 成功率 ~50%（和旧模型持平），DeepSeek 始终稳定
- JSON 修正重试偶有 400（HTTP 层），但正则 fallback 仍能提取审查内容
- 推荐日常：`--mode=fast --models deepseek,kimi`（~22s，¥0.01）

**CI/CD 集成**：
- GitHub Actions: `.github/workflows/council-test.yml`
- 触发条件: push/PR 到 `skills/council/**`，手动触发
- Python 3.11 + 3.12（避开 3.14 的 pytest bug）
- 59 测试 + 去重验证，全通过

**配置补全 (2026-06-16)**：
| ID | 描述 |
|----|------|
| F16 | `.env.example` 新增 MiniMax 配置模板（`MINIMAX_API_KEY`/`MINIMAX_BASE_URL`），此前仅 models.json 引用但模板缺失 |
| F17 | `DeepSeekQQ/plugins/deepseek/config.py` 新增 MiniMax 配置块（`MINIMAX_API_KEY/BASE_URL/MODEL`），与 Kimi/Mimo Chat 并列 |

**环境变量验证** ✅：
- 系统环境变量正常读取，所有 5 个模型 (DeepSeek/Kimi/MiniMax/Mimo/Judge) 均可用
- 读取优先级: 系统环境变量 > .env > 默认值
- `.env` → `.env.bak` 备份，不再使用

**仓库结构**：
```
d:\QQmaonian\
├── .github/workflows/council-test.yml   ← CI/CD 工作流
├── skills/council/                       ← Skill 源码（供 CI）
│   ├── .gitignore
│   ├── SKILL.md
│   ├── models.json
│   ├── requirements.txt
│   ├── prompts/
│   └── scripts/ (+ pytest.ini)
└── council-skill-implementation-report.md
```
%USERPROFILE%\.agents\skills\council\  ← 实际运行目录（与仓库同步）

### 清理 ✅
- [x] 删除 11 个测试输出文件
- [x] 无临时脚本残留

---

## 四、触发方式

### 显式命令
```bash
/council plan.md
```

### 自然语言（任意一种均可触发）
- "帮我交叉验证这个方案"
- "用多模型审查一下 plan.md"
- "评审一下"
- "找三个模型一起审"
- "帮我看看这个方案有没有问题"
- "三模型验证"
- "AI 会审"

---

## 五、常用命令

```bash
# 进入 Skill 目录
cd %USERPROFILE%\.agents\skills\council\scripts

# 配置预览（不调用 API）
python council_call.py plan.md --dry-run --mode=deep

# 快速扫描（推荐日常使用，~12-22s，¥0.01）
python council_call.py plan.md --mode=fast --models deepseek,kimi

# 完整裁决（两模型，~112s，¥0.08）
python council_call.py plan.md --mode=deep --models deepseek,kimi

# 完整裁决（三模型，~360s，¥0.28 — Mimo 较慢）
python council_call.py plan.md --mode=deep --output ./report.md --json

# 运行测试
python test_boundary.py

# 去重验证
python verify_dedup.py
```

---

## 六、环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API Key | ✅ |
| `KIMI_API_KEY` | Kimi (Moonshot) API Key | 推荐 |
| `MINIMAX_API_KEY` | MiniMax API Key | 推荐 |
| `MIMO_CHAT_API_KEY` | Mimo Chat API Key | 可选 |
| `COUNCIL_JUDGE_API_KEY` | 裁决模型 Key（默认 fallback 到 DEEPSEEK_API_KEY） | 可选 |
| `COUNCIL_JUDGE_MODEL` | 裁决模型名（默认 deepseek-v4-pro） | 可选 |

所有 Key **已迁移至 Windows 系统环境变量**（`setx` 永久保存），`.env` 备份为 `.env.bak` 不再使用。读取优先级：系统环境变量 > .env 文件 > models.json 默认值。
