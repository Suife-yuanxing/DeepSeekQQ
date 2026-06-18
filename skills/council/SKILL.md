---
name: council
description: >
  多模型并行交叉验证 — DeepSeek/Kimi/MiniMax/Mimo 四模型审查 + glm-5.2 智谱裁判，
  交叉验证后裁决，输出 PASS/BLOCK/REVISE 质量门控。
  触发词：/council、交叉验证、多模型审查、council review、
  帮我审查方案、评审一下、多模型复审、三模型验证、帮我看看这个方案有没有问题、
  找三个模型一起审、AI 会审
allowed-tools: Bash, Read, Write
---

# /council — 多模型并行交叉验证

## 审查团队（四模型审查 + 独立裁判）

| 模型 | 角色 | 职责（大白话） |
|------|------|---------------|
| **DeepSeek v4-pro** | 🏗️ The Architect 架构师 | 看整体结构稳不稳，组件之间配合好不好 |
| **MiniMax-M3** ⭐ | 🔍 The Auditor 审计师 | 逐条对规范，看有没有遗漏、自相矛盾 |
| Kimi k2.6 | 🧐 The Skeptic 怀疑论者 | 专门想"出事了怎么办"，找安全漏洞 |
| Mimo v2.5-pro | 🔧 The Pragmatist 实用主义者 | 看能不能按时上线、好不好维护 |
| **glm-5.2** ⚖️ | 🎓 Chairman 裁判 | 综合各方意见，拍板 PASS/BLOCK/REVISE |

> **审查模型和裁判模型完全独立** — 审查用 DeepSeek/Kimi，裁判用智谱 glm-5.2，避免"自己审自己判"。

> 💡 **推荐组合**：`deepseek + kimi` — 架构师看整体 + 怀疑论者找漏洞，互补最强（deep 模式 ~60-120s / ¥0.05 起）。三模型加 minimax 做审计，四模型加 mimo 做实操评估。

## 使用时机
写方案后、写代码前。任何时候拿不准"这个方案靠不靠谱"，叫它出来。

## 前置条件
- 环境变量已配置（系统环境变量，非 .env 文件）：
  - `DEEPSEEK_API_KEY` — 必填（审查 + 裁判兜底）
  - `COUNCIL_JUDGE_API_KEY` — 推荐（智谱 API Key，默认用 glm-5.2 做法官）
  - `MINIMAX_API_KEY` — 推荐
  - `KIMI_API_KEY` — 可选
  - `MIMO_CHAT_API_KEY` — 可选
- 裁判降级链（自动切换，无需手动干预）：glm-5.2 → glm-4-plus → deepseek-v4-pro
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
   - Round 3 — Chairman（glm-5.2）综合裁决，输出 PASS/BLOCK/REVISE
4. **回显报告**：stdout 输出完整 Markdown
5. **可选微调**：改完方案用 `--mode=fast` 快速重验

## 上下文说明
所有 LLM 调用在 Python 子进程中完成，**不占对话上下文**。只有方案读取和最终报告进对话。

## 三种模式

| 模式 | 干嘛的 | 几轮 | 耗时（2 模型） | 有裁决 | 什么时候用 |
|------|--------|------|:----------:|:------:|-----------|
| `--mode=fast` | 各看各的，拼一起给你 | 仅 R1 | ~12-25s | ❌ | 小改动快速扫一眼 |
| `--mode=debate` | 看完互相挑刺，去重合并 | R1+R2 | ~30-150s | ❌ | 新功能方案 |
| `--mode=deep` ★ | 全套+glm-5.2 拍板 | R1+R2+R3 | ~60-360s | ✅ | 架构决策、上线前 |

> ⚠️ 耗时随方案大小和模型数量波动。Mimo 单次 88-157s，Kimi 上限 180s，三/四模型+deep 模式总耗时约 5-6 分钟，慎用。费用同理：fast ¥0.01 起，deep ¥0.05 起，大方案/多模型时翻倍。

## 质量门控（仅 deep 模式）
- **PASS** ✅：无 🔴 问题，🟡 ≤ 3 → 直接干
- **REVISE** 🔧：🔴 已修但 🟡 > 3 → 改完再干
- **BLOCK** 🛑：≥1 个 🔴 没解决 → 必须打回去重改

## 裁判模型

| 层级 | 模型 | 厂商 | 特点 | 耗时 |
|------|------|------|------|:--:|
| 首选 | **glm-5.2** | 智谱 | 推理模型，思考充分 | ~50s |
| 降级 | glm-4-plus | 智谱 | 标准模型，快速响应 | ~7s |
| 兜底 | deepseek-v4-pro | DeepSeek | 审查模型同款 | ~30s |

> **配置**：设 `COUNCIL_JUDGE_API_KEY` 为智谱 Key 即自动启用。未设则回退到 DeepSeek 自审自判（不推荐）。

## 常用命令

```bash
# ═══ 日常推荐 ═══
# 快速扫描（~12-25s，¥0.01 起）
python council_call.py plan.md --mode=fast --models deepseek,kimi

# 完整审查 + glm-5.2 裁决（~60-120s，¥0.05 起）
python council_call.py plan.md --mode=deep --models deepseek,kimi

# ═══ 其他组合 ═══
# 偏架构+审计+独立裁决（~120-300s 三模型）
python council_call.py plan.md --mode=deep --models deepseek,kimi,minimax

# 偏安全审查 + 独立裁决（~60-120s）
python council_call.py plan.md --mode=deep --models deepseek,kimi

# 三模型全上（Mimo+Kimi 都慢，总 ~180-360s，慎用）
python council_call.py plan.md --mode=deep --models deepseek,kimi,mimo

# ═══ 辅助 ═══
# 先看配置不花钱（含裁判模型和降级链预览）
python council_call.py plan.md --dry-run --models deepseek,kimi

# 输出到指定文件 + JSON
python council_call.py plan.md --mode=deep --output report.md --json

# 运行测试
python test_boundary.py
```

## 选项一览

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--mode` | fast / debate / deep | `deep` |
| `--models` | 参与审查的模型，逗号分隔 | `deepseek,kimi` |
| `--output` | 报告输出路径 | `./council-verified-{时间戳}.md` |
| `--json` | 同时输出 JSON | 不输出 |
| `--dry-run` | 只预览配置，不调 API | 实际调用 |

## 架构说明

```
Round 1                  Round 2                  Round 3
┌──────────┐          ┌──────────────┐          ┌─────────────┐
│ DeepSeek │──┐       │ kimi→deepseek│          │  glm-5.2    │
│ (架构师)  │  │       │ deepseek→kimi│  去重    │  (智谱裁判)  │
├──────────┤  │       ├──────────────┤  ───→   │             │
│  Kimi    │  │       │   交叉验证    │  合并    │ PASS/BLOCK/ │
│ (怀疑论者) │──┘       │  互相挑刺     │  ───→   │  REVISE     │
└──────────┘          └──────────────┘          └─────────────┘
   并行调用               流水线触发               独立厂商裁决
```

## 注册方式
本 Skill 位于 `%USERPROFILE%\.agents\skills\council\`，Claude Code 启动时自动发现。
