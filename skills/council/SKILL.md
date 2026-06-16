---
name: council
description: >
  多模型并行交叉验证 — 用 DeepSeek/MiniMax/Kimi/Mimo 四个模型从不同视角审查方案，
  交叉验证后由 Chairman 裁决，输出 PASS/BLOCK/REVISE 质量门控。
  触发词：/council、交叉验证、多模型审查、council review、
  帮我审查方案、评审一下、多模型复审、三模型验证、帮我看看这个方案有没有问题、
  找三个模型一起审、AI 会审
allowed-tools: Bash, Read, Write
---

# /council — 多模型并行交叉验证

## 审查团队（四模型四角色）

| 模型 | 角色 | 职责（大白话） |
|------|------|---------------|
| **DeepSeek** | 🏗️ The Architect 架构师 | 看整体结构稳不稳，组件之间配合好不好 |
| **MiniMax-M3** ⭐ | 🔍 The Auditor 审计师 | 逐条对规范，看有没有遗漏、自相矛盾 |
| Kimi | 🧐 The Skeptic 怀疑论者 | 专门想"出事了怎么办"，找安全漏洞 |
| Mimo | 🔧 The Pragmatist 实用主义者 | 看能不能按时上线、好不好维护 |

> 💡 **推荐组合**：`deepseek + minimax` — 架构师看整体 + 审计师查遗漏，互补最强。

## 使用时机
写方案后、写代码前。任何时候拿不准"这个方案靠不靠谱"，叫它出来。

## 前置条件
- 环境变量已配置（系统环境变量，非 .env 文件）：
  - `DEEPSEEK_API_KEY` — 必填
  - `MINIMAX_API_KEY` — 推荐
  - `KIMI_API_KEY` — 可选
  - `MIMO_CHAT_API_KEY` — 可选
  - `COUNCIL_JUDGE_API_KEY` — 可选（默认用 DeepSeek Key）
- 不依赖 Claude API Key，不走当前对话模型

## 输入
- 方案文件路径（Markdown）

## 执行流程
1. **读取方案**：Read 工具获取方案内容
2. **启动 Council**：Bash 调用子进程
3. **自动跑完三轮**：
   - Round 1 — 各模型同时看方案，独立写审查报告
   - Round 2 — 互相交换报告，验证对方发现的问题是否属实
   - [去重] — 合并说得一样的问题（Jaccard 相似度 ≥ 35%）
   - Round 3 — Chairman 综合裁决，输出 PASS/BLOCK/REVISE
4. **回显报告**：stdout 输出完整 Markdown
5. **可选微调**：改完方案用 `--mode=fast` 快速重验

## 上下文说明
所有 LLM 调用在 Python 子进程中完成，**不占对话上下文**。只有方案读取和最终报告进对话。

## 三种模式

| 模式 | 干嘛的 | 几轮 | 耗时（2 模型） | 有裁决 | 什么时候用 |
|------|--------|------|:----------:|:------:|-----------|
| `--mode=fast` | 各看各的，拼一起给你 | 仅 R1 | ~75s | ❌ | 小改动快速扫一眼 |
| `--mode=debate` | 看完互相挑刺，去重合并 | R1+R2 | ~150s | ❌ | 新功能方案 |
| `--mode=deep` ★ | 全套+主席拍板 | R1+R2+R3 | ~200s | ✅ | 架构决策、上线前 |

## 质量门控（仅 deep 模式）
- **PASS** ✅：无 🔴 问题，🟡 ≤ 3 → 直接干
- **REVISE** 🔧：🔴 已修但 🟡 > 3 → 改完再干
- **BLOCK** 🛑：≥1 个 🔴 没解决 → 必须打回去重改

## 常用命令

```bash
# ═══ 日常推荐 ═══
# 快速扫描（~75s，¥0.05）
python council_call.py plan.md --mode=fast --models deepseek,minimax

# 完整审查（~200s，¥0.15）
python council_call.py plan.md --mode=deep --models deepseek,minimax

# ═══ 其他组合 ═══
# 偏安全审查
python council_call.py plan.md --mode=deep --models deepseek,kimi

# 三模型全上（Mimo 慢 ~150s/次，慎用）
python council_call.py plan.md --mode=deep --models deepseek,minimax,mimo

# ═══ 辅助 ═══
# 先看配置不花钱
python council_call.py plan.md --dry-run --models deepseek,minimax

# 输出到指定文件 + JSON
python council_call.py plan.md --mode=deep --output report.md --json

# 运行测试
python test_boundary.py
```

## 选项一览

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--mode` | fast / debate / deep | `deep` |
| `--models` | 参与模型，逗号分隔 | `deepseek,kimi,mimo` |
| `--output` | 报告输出路径 | `./council-verified-{时间戳}.md` |
| `--json` | 同时输出 JSON | 不输出 |
| `--dry-run` | 只预览配置，不调 API | 实际调用 |

## 注册方式
本 Skill 位于 `%USERPROFILE%\.agents\skills\council\`，Claude Code 启动时自动发现。
