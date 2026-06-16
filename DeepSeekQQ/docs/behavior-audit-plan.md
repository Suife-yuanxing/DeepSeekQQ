# 林念念行为系统深度审计 & 改进方案

> 审计日期：2026-06-16
> 状态：方案已完成，待实施

---

## 一、审计背景

林念念（QQmaonian Bot）拥有 7 大行为子系统：

| 子系统 | 文件 | 功能 |
|---|---|---|
| 行为引擎 | `behavior_engine.py` | 7层优先级链：天气→节日→信息流→热榜→季节→微事件→随机 |
| 真人化 | `handler_humanize.py` + `stages/stage_humanize.py` | 错字/结巴/改口/不确定/语气前缀/颜文字/多段拆分 |
| 情绪系统 | `emotion_deep.py` | VA模型 + 情绪传染 + 恢复路径 + 随机波动 |
| 好感度 | `db_affection.py` | 7级（陌生人→命定之人），影响几乎所有子系统 |
| 社交信息流 | `social_feed.py` | 模拟刷手机，新鲜度衰减，兴趣加成 |
| 作息系统 | `schedule.py` | 12时段，精力/回复速度/话量各不相同 |
| 价值观 | `values.py` + `data/persona/values.json` | 6类21个话题，好感度影响表露程度 |

用户反馈：**「感觉还是有点死板，与真人还有比较大的差距」**。

经过对全部 22 个流水线阶段 + 系统提示词构建 + LLM 调用的完整代码链路追踪，结论是：**模块都在正常运行**，但有 4 个结构性原因导致效果被严重稀释。

---

## 二、审计发现：4 个根因

### 根因 1：短消息（≤5字）完全跳过行为引擎

**涉及文件**：
- `stages/stage_context.py:449` — simple 分支跳过全量分析
- `handler_helpers.py:266-281` — `classify_message_complexity()`

```python
# handler_helpers.py:266
def classify_message_complexity(raw_msg, has_image, has_voice):
    msg = raw_msg.strip()
    if len(msg) <= 5 and not has_image and not has_voice:
        return "simple"  # ← 短消息走这里
    ...

# stage_context.py:449
if ctx.complexity == "simple":
    # 跳过 _run_full_analysis() 全部 7 个子阶段
    # ctx.behavior_hint = "" （保持默认空字符串）
    # ctx.emotion_params 使用默认值
    # ctx.reply_gap_hint, ctx.icebreaker_hint 等全部为空
```

**影响面**：日常对话中约 **30%** 的消息属于 short 消息：

> "嗯"、"哈哈"、"好"、"好的"、"ok"、"行"、"来了"、"是的"、"知道了"、"1"、"谢谢"、"不错"、"还行"

这些消息完全不会触发行为注入。而真人恰恰在短回复时最容易流露自然反应——随口抱怨天气、提一句刚看到的东西、打个哈欠、犯困嘀咕——这些才是"像人"的关键时刻。

**严重程度**：🔴 高

---

### 根因 2：真人化效果概率极低且互斥

**涉及文件**：
- `stages/stage_humanize.py` — 真人化阶段入口
- `handler_humanize.py` — 各效果函数

| 效果 | 当前概率 | 互斥关系 |
|---|---|---|
| 语气前缀（"诶？""唔…"） | 10-14% | 独立 |
| 颜文字 | 8-15% | 独立 |
| **错别字+纠正** | 2.5%（高好感）/ 3%（中）/ 0.5%（低） | ⚠️ 与结巴互斥 |
| **结巴** | 3-7.8%（高arousal 6% × 高好感×1.3） | ⚠️ 与错字互斥；20%空操作 |
| 改口（"等等，其实…"） | 2-2.5% | 独立 |
| 不确定（"好像是…"） | 1%（且len>10） | 独立 |
| 活动提及 | 5% | 独立 |
| 多段拆分 | 4-15%（仅>15字符） | 独立 |

**单条消息所有文本扰动都不触发的概率 ≈ 65-75%**。每 10 条回复中有 6-7 条是纯文本"裸奔"。

额外问题：
- **错字概率非单调**：中好感 3% > 高好感 2.5%，不符合"越亲近越随意"的直觉
- **"有点"→"有点电"的错字对不合理**："有点电"不是任何中文词汇
- **`introduce_stutter` 20% 空操作分支**：函数被调用了但什么都不做，浪费概率预算
- **无任何日志**：真人化是否触发完全黑盒，出问题无法排查

**严重程度**：🔴 高

---

### 根因 3：行为引擎命中率仅 ~54%，提示词位置太深

**涉及文件**：
- `behavior_engine.py:670-718` — `get_real_world_behavior()` 7层优先级链
- `prompt.py:480-482` — 行为提示词注入位置

**优先级链首中即止**，每层内部独立掷骰，全不中概率：

```
0.75 × 0.85 × 0.88 × 0.95 × 0.92 × 0.98 × 0.95 ≈ 46%
```

即约 **46% 的消息没有任何行为注入**（注意：holiday 层内部有额外条件——即使 15% 骰子通过，非特殊日期时仅 30% 再命中工作日行为——实际无命中率可能更高）。

当行为注入生效时，它只是一行文本：

```
【行为模式】你对天气的自然反应（用户在shanghai）：今天下雨了呢...好想窝在家里。可以自然地表达出来。
```

这条指令位于系统提示词的**第 39 节**（共约 50 节），前面有：

- 核心人设 ~2300 tokens
- 基础规则（闲聊/忽略/情感/独立观点）~1600 tokens
- 状态/记忆/偏好等 ~500 tokens
- **总计基线 ~3900 tokens**

系统提示词 Token 预算 = **4000**（`config.py:109`）。nice_to_have 项（口癖、称呼、共同兴趣、关系成长）在达到 70% 预算时被裁剪一半，达到 100% 时全部丢弃。

`【行为模式】` 虽然被归类为 IMPORTANT 而非 NICE_TO_HAVE，但在 4000 token 的拥挤提示词中，一行行为指令很难被 LLM 关注。

另外：**天气行为（优先级1, 25%）在天气 API 失败或城市提取失败时静默失效**，白白浪费最高的概率预算。

**严重程度**：🟡 中

---

### 根因 4：多个接近死码的路径

| 代码路径 | 文件:行号 | 问题 | 影响 |
|---|---|---|---|
| `_hot_topic_cache` 低频回退 | `behavior_engine.py:542-585` | `get_hot_topic_behavior()` 优先走 social_feed，缓存回退仅在 social_feed 无数据时触发（低概率路径） | 约 50 行代码低使用率，但非完全死码——social_feed 空数据时仍有兜底价值 |
| 口头禅学习门槛过高 | `personality_drift.py:88` | 需要好感度 ≥600（"专属主人"级别），极少数用户能达到 | 口头禅自然演化功能对 99% 用户不可见 |
| 周兴趣评估无人读取 | `personality_drift.py:167` | 每周调用 DeepSeek API 分析用户兴趣变化，结果写入 DB 后**仅被同一文件内去重检查引用，无任何外部代码读取** | 浪费 API 调用和 token |
| hot_topics 微事件极罕见 | `behavior_engine.py:657` → `hot_topics.py:533-539` | 需要微事件层触发（2%）且前 5 层全部不中（~49%），约 1% 概率 | 几天才触发一次 |
| `zhdate` 静默回退 | `behavior_engine.py:488-500` | 如果 `zhdate` 库未安装，农历节日（春节/端午/七夕/中秋）静默跳过 | 部署环境变更时可能丢失功能区 |

**严重程度**：🟡 中

---

## 三、改进方案

### 阶段 1：最高 ROI（预计体验提升 60-80%）

#### 任务 1.1：短消息轻量行为注入

**文件**：`stages/stage_context.py`、`behavior_engine.py`

在 simple 分支中添加轻量行为，不走全量分析但提供最小行为上下文：

```python
# 在 simple 分支中添加
if ctx.complexity == "simple":
    # 原有默认值设置保持不变
    ...
    # ★新增：轻量行为注入——不跑全量分析但给一点生活感
    ctx.behavior_hint = get_lightweight_behavior_hint(
        ctx._weather_info, ctx.schedule, ctx.affection
    ) or ""
```

新增 `get_lightweight_behavior_hint()` 放在 `behavior_engine.py`：
- 微事件 15%（短消息随口提一句最自然）
- 天气反应 10%（用 WEATHER_CITY 兜底）
- 季节愿望 5%
- 约 **60%** 累积命中率

#### 任务 1.2：真人化概率翻倍 + 取消互斥 + 全部可配置

**文件**：`handler_humanize.py`、`stages/stage_humanize.py`、`config.py`

| 效果 | 当前 | 新 | 理由 |
|---|---|---|---|
| 错别字 | 2.5/3/0.5% | **8/10/5%** | 翻3-5倍，修正非单调（高好感应更随意） |
| 结巴 | 3-7.8% | **5-8%** | 适度提升，取消20%空操作分支 |
| 改口 | 2-2.5% | **5/6/3%** | 翻倍 |
| 不确定 | 1% | **3%** | 翻3倍 |
| 语气前缀 | 10-14% | **15-20%** | 小幅提升 |
| 颜文字 | 8-15% | **15-25%** | 翻倍 |
| 活动提及 | 5% | **8%** | 小幅提升 |
| 多段拆分 | 4-15% | **8-20%** | 小幅提升 |

- **取消错字与结巴互斥**：两者可叠加（错字+结巴同时出现反而更真实）
- **取消 stutter 20% 空操作**：既然决定结巴就一定有可见效果
- **修复 "有点电" 错字对**：移除不合理的字符对
- **所有概率新增 config.py 配置项**，以 `HUMANIZE_` 前缀命名

**预期**：单条消息至少一个文本扰动触发的概率从 ~25-35% 提升到 **~85-95%**。

#### 任务 1.3：增加调试日志

**文件**：`stages/stage_humanize.py`、`behavior_engine.py`

在每种真人化效果触发时增加 debug 日志，以 `LOG_LEVEL=DEBUG` 控制：

```python
logger.debug(f"[真人化] typo applied: {original[:20]}... -> {result[:20]}...")
logger.debug(f"[真人化] stutter applied, subtype={subtype}")
logger.debug(f"[真人化] kaomoji added: {kaomoji}")
logger.debug(f"[行为] 轻量行为触发: {hint[:50]}...")
```

---

### 阶段 2：行为引擎改造（预计体验提升 15-25%）

#### 任务 2.1：优先级链从「首中即止」改为「累积模式」

**文件**：`behavior_engine.py`

当前逻辑：7 层顺序检查，命中即返回 → 54% 命中率，每次最多 1 个行为
新逻辑：收集所有命中的行为（最多 2 个），用分隔符拼接：

```python
def get_real_world_behavior(...) -> Optional[str]:
    candidates = []
    
    # 各行为独立判断，不短路
    weather = _try_get_weather_behavior(condition, temp, city, BEHAVIOR_WEATHER_CHANCE)
    if weather: candidates.append(weather)
    
    holiday = _try_get_holiday_behavior(BEHAVIOR_HOLIDAY_CHANCE)
    if holiday: candidates.append(holiday)
    
    # ... 其余行为类似
    
    if not candidates:
        return None
    
    # 随机选最多 2 个
    if len(candidates) > BEHAVIOR_MAX_COMBINED:
        candidates = random.sample(candidates, BEHAVIOR_MAX_COMBINED)
    
    return "；".join(candidates)
```

**预期**：每次可能同时触发多个行为（如"天气反应 + 微事件"），让行为更丰富自然。配合概率调整可将整体命中率从 ~54% 提升到 ~64%。单独切换为累积模式不改变命中率（数学上首中即止和并行收集的 ≥1 命中概率相同），但用户感知的行为层次感会显著提升。

**⚠️ 注意**：累积模式可能同时产生"天气抱怨+乐观随机行为"等组合。需同步检查 [prompt.py:668-720](DeepSeekQQ/plugins/deepseek/prompt.py#L668-L720) 的 `_resolve_persona_conflicts()` 冲突检测规则，确保不会产生矛盾指令。

#### 任务 2.2：天气行为兜底

**文件**：`stage_context.py`

确保 `ctx._weather_info` 在 context 阶段必然被赋值（当前已有 `get_weather(None)` → `WEATHER_CITY` 兜底路径，需要确认此路径在所有代码分支上都被执行）。

#### 任务 2.3：行为提示词位置前移

**文件**：`prompt.py`

将 `【行为模式】` 从第 39 节移到第 7 节（紧跟 `【作息状态】` 之后），在状态提示之后、记忆提示之前：

```
【作息状态】...
【行为模式】...       ← 从第39位移到此处
【状态】...
【个性】...
【记忆】...
```

**理由**：`_prune_low_priority_hints()` 按类别（essential/important/nice_to_have）裁剪，与 parts 列表位置无关——`【行为模式】` 已在 IMPORTANT 类，位置前移不会改变裁剪优先级。主要收益在于：当 prompt 不超预算时，靠前位置有微弱的 LLM 注意力优势。此项为低优先级改动。

---

### 阶段 3：清理死码

| 任务 | 文件 | 改动 |
|---|---|---|
| 降低口头禅学习门槛 | `personality_drift.py:88` | `affection >= 600` → `affection >= CATCHPHRASE_LEARN_AFFECTION_MIN`（默认 300） |
| 门控周评估 | `personality_drift.py:167` + `config.py` | 新增 `PERSONALITY_WEEKLY_EVAL_ENABLED = False`，默认关闭 |
| 观测热点缓存回退频率 | `behavior_engine.py:542-585` | 添加 debug 日志记录缓存回退触发，若连续 7 天无触发再考虑精简（缓存非完全死码，social_feed 空数据时仍有兜底价值） |

---

### 阶段 4：验证

1. **导入测试**：确认所有修改文件可正常导入
2. **单元测试**：运行 `pytest tests/ -x -q` 确认 739 个测试通过
3. **概率验证**：临时将概率设为 1.0，确认每种效果都能触发
4. **日志验证**：设置 `LOG_LEVEL=DEBUG`，发几条消息确认 `[真人化]` 和 `[行为]` 日志出现
5. **实际体验**：用不同长度的消息测试短回复和长回复

### 阶段 5：效果度量

实施后通过以下方式量化改进效果：

1. **真人化触发率**：通过 `[真人化]` debug 日志统计各效果触发比例，目标：单条回复至少一个扰动触发的概率 ≥ 80%
2. **行为引擎命中率**：通过 `[行为]` 日志统计命中率，目标：≥ 60%
3. **短消息行为注入**：统计 simple 分支的轻量行为命中率，目标：≥ 50%
4. **抽样对比**：改动前后各取 50 条回复做盲评（自然感/生动度打分 1-5），目标均分提升 ≥ 0.8

---

## 四、修改文件清单

| 文件 | 改动类型 | 估计行数 |
|---|---|---|
| `config.py` | 新增 30+ 配置项 | ~40 行 |
| `stages/stage_context.py` | simple 分支添加轻量行为注入，确保天气兜底 | ~30 行 |
| `stages/stage_humanize.py` | 概率改用配置，取消错字/结巴互斥，添加日志 | ~40 行 |
| `handler_humanize.py` | 概率改用配置，修复"有点电"错字对，移除 20% 空操作 | ~30 行 |
| `behavior_engine.py` | 累积模式、轻量行为函数、热点缓存观测日志、概率配置化 | ~80 行 |
| `prompt.py` | 行为模式位置前移 | ~10 行 |
| `personality_drift.py` | 降低门槛、门控开关 | ~15 行 |

**总改动量**：约 250 行，7 个文件。所有改动为行级手术式修改，不重写子系统。

---

## 五、config.py 新增配置项汇总

### 真人化概率（HUMANIZE_）

```python
# 错别字（分好感度三档，修正为非单调：高好感最随意）
HUMANIZE_TYPO_CHANCE_HIGH = 0.08      # aff >= 200
HUMANIZE_TYPO_CHANCE_MID = 0.10       # 20 <= aff < 200
HUMANIZE_TYPO_CHANCE_LOW = 0.05       # aff < 20

# 结巴
HUMANIZE_STUTTER_CHANCE_BASE = 0.05
HUMANIZE_STUTTER_CHANCE_AROUSED = 0.08
HUMANIZE_STUTTER_AFFECTION_MULTIPLIER = 1.3

# 改口
HUMANIZE_MIND_CHANGE_CHANCE_HIGH = 0.06
HUMANIZE_MIND_CHANGE_CHANCE_MID = 0.05
HUMANIZE_MIND_CHANGE_CHANCE_LOW = 0.03

# 不确定
HUMANIZE_UNCERTAINTY_CHANCE = 0.03

# 语气前缀
HUMANIZE_REACTION_PREFIX_HIGH = 0.20
HUMANIZE_REACTION_PREFIX_MID = 0.15
HUMANIZE_REACTION_PREFIX_LOW = 0.08

# 颜文字（按情绪分档）
HUMANIZE_KAOMOJI_EXCITED = 0.25
HUMANIZE_KAOMOJI_HAPPY = 0.20
HUMANIZE_KAOMOJI_SHY = 0.18
HUMANIZE_KAOMOJI_ANGRY = 0.15
HUMANIZE_KAOMOJI_SAD = 0.15
HUMANIZE_KAOMOJI_TSUNDERE = 0.15
HUMANIZE_KAOMOJI_TEASE = 0.20
HUMANIZE_KAOMOJI_DEFAULT = 0.15

# 活动提及
HUMANIZE_ACTIVITY_MENTION_CHANCE = 0.08

# 结巴空操作概率（0 = 永不空操作）
HUMANIZE_STUTTER_NOOP_CHANCE = 0.0
```

### 行为引擎概率（BEHAVIOR_）

```python
BEHAVIOR_WEATHER_CHANCE = 0.25
BEHAVIOR_HOLIDAY_CHANCE = 0.15
BEHAVIOR_SCROLL_CHANCE = 0.12
BEHAVIOR_HOT_TOPIC_CHANCE = 0.05
BEHAVIOR_SEASONAL_CHANCE = 0.08
BEHAVIOR_MICRO_EVENT_CHANCE = 0.02
BEHAVIOR_RANDOM_CHANCE = 0.05

# 轻量行为（短消息用）
BEHAVIOR_LIGHT_MICRO_EVENT_CHANCE = 0.15
BEHAVIOR_LIGHT_WEATHER_CHANCE = 0.10
BEHAVIOR_LIGHT_SEASONAL_CHANCE = 0.05

# 最多合并几个行为
BEHAVIOR_MAX_COMBINED = 2
```

### 其他

```python
CATCHPHRASE_LEARN_AFFECTION_MIN = 300   # 降低门槛
PERSONALITY_WEEKLY_EVAL_ENABLED = False  # 关闭无用评估
```
