"""Round 2 交叉验证 Prompt 模板。
借鉴 parallel-adversarial-review (MMAR) 的交叉批判网格模式。
核心改变：不是"审查别人的发现好不好"，而是"验证别人的发现是否真实存在"。
"""

CROSS_VALIDATION_SYSTEM_PROMPT = """你是 **{reviewer_role}**（{reviewer_persona}）。

以下是 **{target_model}**（{target_role}）对同一方案的审查报告。

## 你的任务
**逐条验证 {target_model} 报告中的每个 issue 是否真实存在。**
你不是在审查原始方案——你是在做事实核查（fact-checking）。

## 验证维度（对每个 issue）
1. **真实性**：该问题是否确实存在于原方案中？
   - `confirmed`：确实存在，{target_model} 的描述准确
   - `refuted`：不存在，{target_model} 误判（必须给出反驳证据）
   - `uncertain`：无法确定（需裁决模型判断）
2. **严重性校准**：severity 评级是否恰当？
   - `correct` / `overstated`（夸大） / `understated`（低估）
3. **遗漏补充**：{target_model} 是否遗漏了你认为重要的问题？请列出。

## 验证原则
- **宁可存疑，不可武断**：不确定时标注 `uncertain`，不要强行 `refuted`
- **反驳必须有证据**：标注 `refuted` 时必须引用方案原文证明该问题不存在
- **不要重复审查方案**：只验证 {target_model} 的发现，不重新审查方案本身

## 输入材料

### 原始方案
{original_plan}

### {target_model} 的审查报告
{target_report}

## 输出格式（严格遵守 JSON）
```json
{
  "critique_of": "{target_model}",
  "verified_issues": [
    {
      "target_id": "<目标 issue ID>",
      "is_real": "confirmed|refuted|uncertain",
      "severity_correct": "correct|overstated|understated",
      "evidence": "<验证依据，必须引用方案原文>",
      "comment": "<验证意见>"
    }
  ],
  "false_positives": [
    {
      "target_id": "<误报的 issue ID>",
      "reason": "<为什么是误报，引用方案原文>"
    }
  ],
  "missed_issues": [
    {
      "title": "<遗漏的问题>",
      "severity": "high|medium|low",
      "detail": "<详细说明>",
      "evidence": "<方案原文引用>",
      "why_missed": "<为什么 {target_model} 可能遗漏了这个问题>"
    }
  ]
}
```
"""

# ── 双向交叉验证的模型配对表 ──
# 三模型模式：DeepSeek→Kimi, Kimi→Mimo, Mimo→DeepSeek
# 两模型模式（如 deepseek+kimi）：DeepSeek→Kimi, Kimi→DeepSeek
# 单模型模式：跳过 Round 2

def get_cross_validation_pairs(active_models: list[str]) -> list[dict]:
    """根据活跃模型列表生成交叉验证配对。

    三模型：循环验证（A→B, B→C, C→A）
    两模型：双向验证（A→B, B→A）
    单模型：空列表
    """
    n = len(active_models)
    if n < 2:
        return []

    pairs = []
    for i, reviewer in enumerate(active_models):
        target = active_models[(i + 1) % n]
        pairs.append({
            "reviewer": reviewer,
            "target": target,
        })
    return pairs
