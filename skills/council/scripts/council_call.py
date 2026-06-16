#!/usr/bin/env python3
"""council_call.py — 多模型并行交叉验证 CLI。

模块化结构：
  utils.py       — 日志、token计数、进度条、门控提取
  config.py      — 配置加载、运行时常量
  api_client.py  — API调用、JSON解析、消息构建
  council_call.py — 编排(R1/R2/R3)、去重、报告生成、CLI入口
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Windows 控制台 UTF-8 编码修复 ──
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 将 prompts 目录和 scripts 目录加入路径 ──
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SKILL_DIR))

from prompts.review_prompts import MODEL_ROLE_MAP, CROSS_VALIDATION_MAP, OUTPUT_SCHEMA
from prompts.critique_prompts import CROSS_VALIDATION_SYSTEM_PROMPT, get_cross_validation_pairs
from prompts.judge_prompt import JUDGE_SYSTEM_PROMPT, evaluate_gate

# ── 从子模块导入 ──
from utils import (
    log_progress, ProgressTracker, count_tokens, truncate_to_tokens,
    extract_gate_from_report, HAS_TIKTOKEN,
)
from config import (
    MODE_CONFIG, DEFAULT_TIMEOUT, MIMO_TIMEOUT, MIMO_MAX_PLAN_CHARS,
    JACCARD_THRESHOLD, MAX_PLAN_CHARS, SUPPORTED_MODELS, MODEL_OVERRIDES,
    COST_PER_M, JUDGE_FALLBACK_CHAIN, load_config,
)
from api_client import (
    call_model, call_model_with_json_retry,
    extract_json, extract_json_fields_regex,
    build_round1_messages, build_cross_validation_messages,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Round 1: 独立审查
# ═══════════════════════════════════════════════════════════════════════════════

def run_round1(plan_text: str, active_models: list[str],
               config: dict) -> dict[str, dict]:
    """并行执行 Round 1：每个模型独立审查方案。"""
    log_progress(f"Round 1: {len(active_models)} 模型并行审查", "phase")

    results = {}

    def _call_one(model_key):
        model_config = config[model_key]
        messages = build_round1_messages(plan_text, model_key)
        log_progress(f"  调用 {model_key} ({MODEL_ROLE_MAP[model_key]['persona']})...", "info")
        timeout = MIMO_TIMEOUT if model_key == "mimo" else None
        result = call_model_with_json_retry(model_config, messages,
                                            max_tokens=2048, timeout=timeout)
        if result["status"] == "success":
            if not result.get("parse_failed"):
                retry_info = f" (重试 {result['_json_retries']} 次)" if result.get("_json_retries") else ""
                log_progress(f"  {model_key}: ✅ 成功 ({result['time_s']}s, {result['tokens'].get('total', '?')} tokens){retry_info}", "success")
            else:
                log_progress(f"  {model_key}: ⚠️ JSON 解析失败，保留 raw_text", "error")
        else:
            log_progress(f"  {model_key}: ❌ 失败 ({result.get('error', 'unknown')[:100]})", "error")
        return model_key, result

    with ThreadPoolExecutor(max_workers=len(active_models)) as executor:
        futures = {executor.submit(_call_one, m): m for m in active_models}
        for future in as_completed(futures):
            model_key, result = future.result()
            results[model_key] = result

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Round 2: 交叉验证
# ═══════════════════════════════════════════════════════════════════════════════

def run_round2(plan_text: str, round1_results: dict[str, dict],
               active_models: list[str], config: dict) -> dict[str, dict]:
    """并行执行 Round 2：交叉验证 Round 1 的发现。"""
    if len(active_models) < 2:
        log_progress("Round 2: 跳过（少于 2 个模型）", "phase")
        return {}

    pairs = get_cross_validation_pairs(active_models)
    log_progress(f"Round 2: {len(pairs)} 对交叉验证", "phase")

    results = {}

    def _call_one(pair):
        reviewer = pair["reviewer"]
        target = pair["target"]
        target_report = round1_results.get(target)
        if not target_report or target_report["status"] != "success":
            log_progress(f"  跳过 {reviewer}→{target}（target 无可用报告）", "info")
            return f"{reviewer}_to_{target}", {"status": "skipped"}
        if target_report.get("parse_failed"):
            log_progress(f"  跳过 {reviewer}→{target}（target JSON 解析失败，无法可靠验证）", "info")
            return f"{reviewer}_to_{target}", {
                "status": "skipped", "skip_reason": "target_parse_failed",
                "target_model": target,
            }
        model_config = config[reviewer]
        messages = build_cross_validation_messages(plan_text, reviewer, target, target_report)
        log_progress(f"  交叉验证: {reviewer} → {target}...", "info")
        result = call_model_with_json_retry(model_config, messages, max_tokens=2048)
        if result["status"] == "success":
            if not result.get("parse_failed"):
                log_progress(f"  {reviewer}→{target}: ✅ 验证完成", "success")
            else:
                log_progress(f"  {reviewer}→{target}: ⚠️ JSON 解析失败", "error")
        else:
            log_progress(f"  {reviewer}→{target}: ❌ 失败", "error")
        return f"{reviewer}_to_{target}", result

    with ThreadPoolExecutor(max_workers=len(pairs)) as executor:
        futures = {executor.submit(_call_one, p): p for p in pairs}
        for future in as_completed(futures):
            key, result = future.result()
            results[key] = result

    return results


def run_round1_and_2_pipelined(plan_text: str, active_models: list[str],
                                config: dict) -> tuple[dict, dict]:
    """流水线执行 Round 1 + Round 2：R1 结果就绪后立即开始对应 R2 验证。"""
    if len(active_models) < 2:
        r1 = run_round1(plan_text, active_models, config)
        return r1, {}

    pairs = get_cross_validation_pairs(active_models)
    total_tasks = len(active_models) + len(pairs)
    progress = ProgressTracker(total_tasks, f"R1+R2 ({len(active_models)} 审 + {len(pairs)} 验)")
    log_progress(f"Round 1+2 流水线: {len(active_models)} 模型审查 + {len(pairs)} 对交叉验证", "phase")

    round1_results: dict[str, dict] = {}
    round2_results: dict[str, dict] = {}
    r2_by_target: dict[str, list[dict]] = {}
    for pair in pairs:
        target = pair["target"]
        r2_by_target.setdefault(target, []).append(pair)
    r2_submitted: set[str] = set()

    with ThreadPoolExecutor(max_workers=len(active_models) + len(pairs)) as executor:
        r1_futures = {}
        for m in active_models:
            fut = executor.submit(_call_one_r1, m, plan_text, config)
            r1_futures[fut] = m
        r2_futures: dict[object, str] = {}
        pending_r1 = set(r1_futures.values())

        while pending_r1 or r2_futures:
            all_futures = list(r1_futures.keys()) + list(r2_futures.keys())
            if not all_futures:
                break
            for future in as_completed(all_futures):
                if future in r1_futures:
                    model_key = r1_futures.pop(future)
                    pending_r1.discard(model_key)
                    result = future.result()
                    round1_results[model_key] = result
                    progress.task_done(f"R1:{model_key}", result.get("time_s", 0) if result["status"] == "success" else 0)
                    progress.log()
                    status_icon = "✅" if result["status"] == "success" and not result.get("parse_failed") else "⚠️" if result["status"] == "success" else "❌"
                    log_progress(f"  R1 {model_key} {status_icon} → 检查可启动的 R2 对...", "info")
                    for pair in r2_by_target.get(model_key, []):
                        cv_key = f"{pair['reviewer']}_to_{pair['target']}"
                        if cv_key in r2_submitted:
                            continue
                        target_report = round1_results.get(pair["target"])
                        if target_report is None:
                            continue
                        if target_report.get("status") != "success" or target_report.get("parse_failed"):
                            log_progress(f"  跳过 R2 {cv_key}（target 不可用）", "info")
                            round2_results[cv_key] = {
                                "status": "skipped",
                                "skip_reason": "target_parse_failed" if target_report.get("parse_failed") else "target_error",
                                "target_model": pair["target"],
                            }
                            r2_submitted.add(cv_key)
                            continue
                        r2_submitted.add(cv_key)
                        fut = executor.submit(_call_one_r2, pair, plan_text, round1_results, config)
                        r2_futures[fut] = cv_key
                elif future in r2_futures:
                    cv_key = r2_futures.pop(future)
                    returned_key, result = future.result()  # _call_one_r2 returns (cv_key, result)
                    round2_results[cv_key] = result
                    progress.task_done(f"R2:{cv_key}", result.get("time_s", 0) if result.get("status") == "success" else 0)
                    progress.log()
                    status_icon = "✅" if result.get("status") == "success" and not result.get("parse_failed") else "⚠️" if result.get("status") == "success" else "❌"
                    log_progress(f"  R2 {cv_key} {status_icon}", "info")
                break

    progress.done()
    return round1_results, round2_results


def _call_one_r1(model_key: str, plan_text: str, config: dict) -> dict:
    """执行单个模型的 Round 1 审查（供流水线使用）。"""
    model_config = config[model_key]
    messages = build_round1_messages(plan_text, model_key)
    timeout = MIMO_TIMEOUT if model_key == "mimo" else None
    return call_model_with_json_retry(model_config, messages, max_tokens=2048, timeout=timeout)


def _call_one_r2(pair: dict, plan_text: str, round1_results: dict, config: dict) -> tuple:
    """执行单对交叉验证（供流水线使用）。"""
    reviewer = pair["reviewer"]
    target = pair["target"]
    cv_key = f"{reviewer}_to_{target}"
    target_report = round1_results.get(target)
    if not target_report or target_report.get("status") != "success":
        return (cv_key, {"status": "skipped", "skip_reason": "target_unavailable"})
    model_config = config[reviewer]
    messages = build_cross_validation_messages(plan_text, reviewer, target, target_report)
    log_progress(f"  交叉验证: {reviewer} → {target}...", "info")
    result = call_model_with_json_retry(model_config, messages, max_tokens=2048)
    return (cv_key, result)


# ═══════════════════════════════════════════════════════════════════════════════
# 去重合并
# ═══════════════════════════════════════════════════════════════════════════════

def _keyword_jaccard(text_a: str, text_b: str) -> float:
    """计算两个文本的关键词 Jaccard 相似度（CJK 2-gram + 英文单词）。"""
    def _ngrams(s: str) -> set:
        cjk_chars = re.findall(r'[一-鿿㐀-䶿]', s)
        bigrams = set()
        for i in range(len(cjk_chars) - 1):
            bigrams.add(cjk_chars[i] + cjk_chars[i + 1])
        en_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{2,}', s))
        bigrams.update(en_words)
        nums = set(re.findall(r'\d+', s))
        bigrams.update(nums)
        return bigrams
    set_a = _ngrams(text_a)
    set_b = _ngrams(text_b)
    if not set_a and not set_b:
        return 0.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def deduplicate_issues(round1_results: dict[str, dict],
                       round2_results: dict[str, dict] | None = None) -> list[dict]:
    """合并 Round 1 + Round 2 的发现，去除重复 issue。"""
    all_issues = []
    for model_key, result in round1_results.items():
        if result["status"] != "success":
            continue
        parsed = result.get("parsed") or {}
        for issue in (parsed.get("issues") or []):
            issue["_source"] = model_key
            issue["_round"] = 1
            if result.get("parse_failed"):
                issue["_confidence"] = "uncertain"
                issue.setdefault("_note", "来源报告 JSON 解析失败，置信度降级")
            else:
                issue["_confidence"] = "normal"
            all_issues.append(issue)
    if round2_results:
        for cv_key, result in round2_results.items():
            if result.get("status") != "success":
                continue
            parsed = result.get("parsed") or {}
            reviewer = cv_key.split("_to_")[0]
            for issue in (parsed.get("missed_issues") or []):
                issue["_source"] = reviewer
                issue["_round"] = 2
                if "id" not in issue:
                    issue["id"] = f"{MODEL_ROLE_MAP.get(reviewer, {}).get('prefix', '?')}-R2-{len(all_issues)}"
                all_issues.append(issue)
    if not all_issues:
        return []
    merged = []
    duplicate_ids = set()
    for i, issue_a in enumerate(all_issues):
        if issue_a.get("id") in duplicate_ids:
            continue
        is_dup = False
        for j, issue_b in enumerate(merged):
            title_a = issue_a.get("title", "")
            title_b = issue_b.get("title", "")
            if title_a in title_b or title_b in title_a:
                is_dup = True
                sev_order = {"low": 0, "medium": 1, "high": 2}
                if sev_order.get(issue_a.get("severity"), 0) > sev_order.get(issue_b.get("severity"), 0):
                    issue_b["severity"] = issue_a["severity"]
                issue_b.setdefault("duplicate_of", [])
                issue_b["duplicate_of"].append(issue_a.get("id", "?"))
                duplicate_ids.add(issue_a.get("id"))
                break
            if _keyword_jaccard(title_a, title_b) > JACCARD_THRESHOLD:
                is_dup = True
                duplicate_ids.add(issue_a.get("id"))
                issue_b.setdefault("duplicate_of", [])
                issue_b["duplicate_of"].append(issue_a.get("id", "?"))
                break
        if not is_dup:
            merged.append(issue_a)
    if round2_results:
        severity_order = ["low", "medium", "high"]
        for cv_key, result in round2_results.items():
            if result.get("status") != "success":
                continue
            parsed = result.get("parsed") or {}
            reviewer = cv_key.split("_to_")[0]
            for verification in (parsed.get("verified_issues") or []):
                if verification.get("severity_correct") == "understated":
                    target_id = verification.get("target_id", "")
                    for issue in merged:
                        if issue.get("id") == target_id:
                            current = issue.get("severity", "medium")
                            idx = severity_order.index(current) if current in severity_order else 1
                            if idx < 2:
                                issue["severity"] = severity_order[idx + 1]
                                issue.setdefault("severity_upgraded_by", [])
                                issue["severity_upgraded_by"].append(reviewer)
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# Round 3: 裁决
# ═══════════════════════════════════════════════════════════════════════════════

def truncate_context(plan_text: str, round1_results: dict, round2_results: dict,
                     merged_issues: list, max_tokens: int = 60000) -> str:
    """智能截断上下文至 token 限制内。"""
    context_parts = [
        "# 原始方案\n\n" + plan_text,
        "\n\n# Round 1 独立审查\n\n",
    ]
    for model_key, result in round1_results.items():
        if result["status"] != "success":
            context_parts.append(f"\n## {model_key}: ❌ 调用失败\n")
            continue
        parsed = result.get("parsed") or {}
        if result.get("parse_failed"):
            raw = result.get("raw_text", "")[:800]
            context_parts.append(f"\n## {model_key} [raw_text]\n{raw}\n")
        else:
            mini = {
                "score": parsed.get("score"),
                "summary": truncate_to_tokens(parsed.get("summary", ""), 80),
                "issues": parsed.get("issues", []),
                "strengths": (parsed.get("strengths", []) or [])[:3],
            }
            context_parts.append(f"\n## {model_key}\n```json\n{json.dumps(mini, ensure_ascii=False, indent=2)}\n```\n")
    if round2_results:
        context_parts.append("\n\n# Round 2 交叉验证\n\n")
        for cv_key, result in round2_results.items():
            if result.get("status") == "skipped":
                skip_reason = result.get("skip_reason", "unknown")
                target = result.get("target_model", "?")
                context_parts.append(f"\n## {cv_key}: ⚠️ 跳过（{skip_reason}，target={target}）\n")
                continue
            if result.get("status") != "success":
                continue
            parsed = result.get("parsed") or {}
            if result.get("parse_failed"):
                raw = result.get("raw_text", "")[:800]
                context_parts.append(f"\n## {cv_key} [raw_text]\n{raw}\n")
            else:
                mini = {
                    "critique_of": parsed.get("critique_of"),
                    "verified_issues": parsed.get("verified_issues", []),
                    "false_positives": parsed.get("false_positives", []),
                }
                missed = (parsed.get("missed_issues") or [])[:3]
                if missed:
                    mini["missed_issues"] = missed
                context_parts.append(f"\n## {cv_key}\n```json\n{json.dumps(mini, ensure_ascii=False, indent=2)}\n```\n")
    context = "".join(context_parts)
    if len(context) > max_tokens * 2:
        context = context[:max_tokens * 2] + "\n\n[... 截断 ...]"
    return context


def run_round3(plan_text: str, round1_results: dict, round2_results: dict,
               merged_issues: list, config: dict) -> dict:
    """Round 3: Chairman 综合裁决（含模型降级链）。"""
    log_progress("Round 3: Chairman 综合裁决", "phase")
    primary_judge = config["judge"]
    if not primary_judge["api_key"]:
        log_progress("  裁决模型 API Key 未配置，跳过", "error")
        return {"status": "skipped"}
    context = truncate_context(plan_text, round1_results, round2_results, merged_issues)
    high = [i for i in merged_issues if i.get("severity") == "high"]
    medium = [i for i in merged_issues if i.get("severity") == "medium"]
    low = [i for i in merged_issues if i.get("severity") == "low"]
    issues_summary = f"""
## 去重合并后的问题

### 🔴 高严重性 ({len(high)} 个)
{json.dumps(high, ensure_ascii=False, indent=2) if high else "（无）"}

### 🟡 中严重性 ({len(medium)} 个)
{json.dumps(medium, ensure_ascii=False, indent=2) if medium else "（无）"}

### 🟢 低严重性 ({len(low)} 个)
{json.dumps(low, ensure_ascii=False, indent=2) if low else "（无）"}
"""
    tz = timezone(timedelta(hours=8))
    timestamp = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S CST")
    system_prompt = JUDGE_SYSTEM_PROMPT.replace("{timestamp}", timestamp)
    user_prompt = f"""请对以下材料进行裁决：

{context}

{issues_summary}

请输出完整的 Markdown 裁决报告（含质量门控 PASS/BLOCK/REVISE）。"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    candidates = [{
        "model": primary_judge["model"],
        "base_url": primary_judge["base_url"],
        "api_key": primary_judge["api_key"],
        "auth_header": primary_judge["auth_header"],
    }]
    seen = {(primary_judge["model"], primary_judge["base_url"])}
    for fb in JUDGE_FALLBACK_CHAIN:
        fb_key = os.getenv(fb["api_key_env"], "")
        fb_url = os.getenv(fb["base_url_env"], fb["default_base_url"])
        fb_combo = (fb["model"], fb_url)
        if fb_key and fb_combo not in seen:
            candidates.append({
                "model": fb["model"],
                "base_url": fb_url,
                "api_key": fb_key,
                "auth_header": fb["auth_header"],
            })
            seen.add(fb_combo)
    progress = ProgressTracker(len(candidates), "裁决")
    result = None
    tried_models = []
    for i, jc in enumerate(candidates):
        label = f"{jc['model']}" + (" (首选)" if i == 0 else " (降级)")
        log_progress(f"  调用 Chairman {label}...", "info")
        tried_models.append(jc["model"])
        sp = system_prompt.replace("{judge_model}", jc["model"])
        msg = [
            {"role": "system", "content": sp},
            {"role": "user", "content": user_prompt},
        ]
        result = call_model(jc, msg, max_tokens=4096)
        result["high_count"] = len(high)
        result["medium_count"] = len(medium)
        if result["status"] == "success":
            progress.task_done(jc["model"], result.get("time_s", 0))
            progress.done()
            extracted_gate = extract_gate_from_report(result.get("content", ""))
            if extracted_gate:
                result["gate"] = extracted_gate
                result["gate_source"] = "chairman_report"
            else:
                result["gate"] = evaluate_gate(len(high), len(medium))
                result["gate_source"] = "computed"
            log_progress(f"  Chairman ({jc['model']}): ✅ 裁决完成 ({result['time_s']}s) → {result['gate']}", "success")
            result["judge_model_used"] = jc["model"]
            result["judge_fallback_used"] = i > 0
            result["judge_tried"] = tried_models
            return result
        else:
            progress.task_done(jc["model"], 0)
            progress.log()
            log_progress(f"  Chairman ({jc['model']}): ❌ 失败，尝试下一个...", "error")
    progress.done()
    log_progress(f"  Chairman: ❌ 全部降级模型失败 ({', '.join(tried_models)})", "error")
    result = result or {"status": "error", "error": "all judge models failed"}
    result["judge_tried"] = tried_models
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════════════════════════════════════════════

def build_fast_report(plan_path: str, round1_results: dict,
                      config: dict, total_time: float,
                      total_tokens: dict) -> str:
    """生成 fast 模式报告。"""
    lines = [
        "# 多模型独立审查报告 (fast)",
        "",
        f"> 审查时间：{datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S CST')}",
        f"> 方案文件：{plan_path}",
        f"> 参与模型：{', '.join(round1_results.keys())}",
        f"> 耗时：{total_time:.1f}s | Token：{total_tokens.get('total', '?')}",
        "",
        "---",
        "",
    ]
    for model_key, result in round1_results.items():
        role_info = MODEL_ROLE_MAP.get(model_key, {})
        lines.append(f"## {model_key} — {role_info.get('persona', '?')}")
        lines.append("")
        if result["status"] != "success":
            lines.append(f"❌ **调用失败**：{result.get('error', 'unknown')}")
            lines.append("")
            continue
        parsed = result.get("parsed") or {}
        if result.get("parse_failed"):
            lines.append(f"⚠️ JSON 解析失败，以下为 raw_text：")
            lines.append("")
            lines.append(result.get("raw_text", result.get("content", ""))[:2000])
            lines.append("")
            continue
        lines.append(f"**综合评分**：{parsed.get('score', '?')}/10")
        lines.append(f"**摘要**：{parsed.get('summary', '?')}")
        lines.append("")
        issues = parsed.get("issues") or []
        if issues:
            lines.append("### 发现的问题")
            lines.append("")
            for issue in issues:
                sev_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(issue.get("severity"), "⚪")
                lines.append(f"**{sev_emoji} [{issue.get('id', '?')}] {issue.get('title', '?')}**")
                lines.append(f"- 严重性：{issue.get('severity', '?')}")
                lines.append(f"- 原文引用：> {issue.get('evidence', '?')}")
                lines.append(f"- 详情：{issue.get('detail', '?')}")
                lines.append(f"- 修复建议：{issue.get('fix_suggestion', '?')}")
                lines.append("")
        strengths = parsed.get("strengths") or []
        if strengths:
            lines.append("### 方案优点")
            for s in strengths:
                lines.append(f"- ✅ {s}")
            lines.append("")
        suggestions = parsed.get("suggestions") or []
        if suggestions:
            lines.append("### 改进建议")
            for s in suggestions:
                lines.append(f"- 💡 **{s.get('title', '?')}**：{s.get('detail', '?')}（收益：{s.get('benefit', '?')}）")
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def build_debate_report(plan_path: str, round1_results: dict,
                        round2_results: dict, merged_issues: list,
                        config: dict, total_time: float,
                        total_tokens: dict) -> str:
    """生成 debate 模式报告。"""
    fast_part = build_fast_report(plan_path, round1_results, config, total_time, total_tokens)
    lines = [fast_part]
    lines.append("")
    lines.append("# Round 2 交叉验证结果")
    lines.append("")
    for cv_key, result in round2_results.items():
        if result.get("status") != "success":
            continue
        reviewer, _, target = cv_key.partition("_to_")
        lines.append(f"## {reviewer} → {target}")
        lines.append("")
        parsed = result.get("parsed") or {}
        if result.get("parse_failed"):
            lines.append(f"⚠️ JSON 解析失败，raw_text：")
            lines.append(result.get("raw_text", "")[:1000])
            continue
        verified = parsed.get("verified_issues") or []
        if verified:
            lines.append("### 验证结果")
            lines.append("")
            for v in verified:
                is_real_icon = {"confirmed": "✅", "refuted": "❌", "uncertain": "❓"}.get(v.get("is_real"), "❓")
                lines.append(f"- {is_real_icon} **{v.get('target_id', '?')}**：`{v.get('is_real', '?')}`")
                lines.append(f"  - 严重性校准：{v.get('severity_correct', '?')}")
                if v.get("evidence"):
                    lines.append(f"  - 依据：{v['evidence'][:200]}")
                if v.get("comment"):
                    lines.append(f"  - 备注：{v['comment'][:200]}")
            lines.append("")
        false_pos = parsed.get("false_positives") or []
        if false_pos:
            lines.append("### 误报")
            for fp in false_pos:
                lines.append(f"- ❌ **{fp.get('target_id', '?')}**：{fp.get('reason', '?')[:200]}")
            lines.append("")
        missed = parsed.get("missed_issues") or []
        if missed:
            lines.append("### 新发现的问题")
            for m in missed:
                sev = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(m.get("severity"), "⚪")
                lines.append(f"- {sev} **{m.get('title', '?')}**：{m.get('detail', '?')[:200]}")
                if m.get("why_missed"):
                    lines.append(f"  - 遗漏原因：{m['why_missed'][:200]}")
            lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 去重合并结果")
    lines.append("")
    lines.append(f"共 {len(merged_issues)} 个独立问题：")
    lines.append("")
    high = [i for i in merged_issues if i.get("severity") == "high"]
    medium = [i for i in merged_issues if i.get("severity") == "medium"]
    low = [i for i in merged_issues if i.get("severity") == "low"]
    for label, issues in [("🔴 高严重性", high), ("🟡 中严重性", medium), ("🟢 低严重性", low)]:
        if issues:
            lines.append(f"### {label}（{len(issues)} 个）")
            for issue in issues:
                source = issue.get("_source", "?")
                lines.append(f"- **[{issue.get('id', '?')}]** {issue.get('title', '?')} (来源: {source}, 轮次: {issue.get('_round', '?')})")
                if issue.get("duplicate_of"):
                    lines.append(f"  - 合并自：{', '.join(issue['duplicate_of'])}")
                if issue.get("severity_upgraded_by"):
                    lines.append(f"  - 严重性由 {', '.join(issue['severity_upgraded_by'])} 升级")
            lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 成本估算
# ═══════════════════════════════════════════════════════════════════════════════

def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """估算单次调用成本（人民币）。"""
    rates = COST_PER_M.get(model)
    if not rates:
        rates = {"input": 2.0, "output": 8.0}
    cost = (input_tokens / 1_000_000) * rates["input"] + \
           (output_tokens / 1_000_000) * rates["output"]
    return round(cost, 4)


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="council_call.py — 多模型并行交叉验证",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
模式选择:
  --mode=fast     仅 Round 1（2~3 次并行调用），约 12~25s
  --mode=debate   Round 1 + 2（4~6 次调用 + 去重），约 30~150s
  --mode=deep     全流程（5~7 次调用 + 去重 + 裁决），约 60~360s（默认）

模型选择:
  --models deepseek,kimi,mimo      三模型（默认）
  --models deepseek,kimi           两模型
  --models deepseek                单模型

示例:
  python council_call.py plan.md --mode=deep
  python council_call.py plan.md --mode=fast --models deepseek,kimi
  python council_call.py plan.md --dry-run
        """,
    )
    parser.add_argument("plan", help="方案文件路径（Markdown）")
    parser.add_argument("--mode", choices=["fast", "debate", "deep"],
                        default="deep", help="审查模式（默认: deep）")
    parser.add_argument("--models", default="deepseek,kimi,mimo",
                        help="参与模型，逗号分隔（默认: deepseek,kimi,mimo）")
    parser.add_argument("--output", "-o", default=None,
                        help="输出文件路径（默认: ./council-verified-{timestamp}.md）")
    parser.add_argument("--json", action="store_true",
                        help="同时输出结构化 JSON")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅打印配置和 prompt 预览，不调用 API")
    args = parser.parse_args()

    config = load_config()
    active_models = [m.strip() for m in args.models.split(",") if m.strip()]
    mode_config = MODE_CONFIG[args.mode]

    invalid = [m for m in active_models if m not in SUPPORTED_MODELS]
    if invalid:
        print(f"❌ 不支持的模型: {', '.join(invalid)}", file=sys.stderr)
        print(f"   支持的模型: {', '.join(sorted(SUPPORTED_MODELS))}", file=sys.stderr)
        sys.exit(1)

    missing = [m for m in active_models if not config[m]["api_key"]]
    if missing:
        log_progress(f"缺少 API Key: {', '.join(missing)}", "error")
        log_progress("请设置对应的环境变量或创建 Skill .env 文件", "error")

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"❌ 文件不存在: {args.plan}", file=sys.stderr)
        sys.exit(1)

    plan_text = plan_path.read_text(encoding="utf-8")
    log_progress(f"已读取方案：{plan_path}（{len(plan_text)} 字符）", "success")

    if len(plan_text) > MAX_PLAN_CHARS:
        print(f"❌ 方案文件过大（{len(plan_text)} 字符，上限 {MAX_PLAN_CHARS} 字符）", file=sys.stderr)
        print(f"   建议：拆分方案为多个子文件，分批审查", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        _do_dry_run(plan_text, active_models, config, args.mode)
        return

    t_start = time.time()
    total_tokens = {"input": 0, "output": 0, "total": 0}
    total_cost = 0.0

    round1_results = {}
    round2_results = {}
    merged_issues = []

    if 1 in mode_config["rounds"]:
        if 2 in mode_config["rounds"] and len(active_models) >= 2:
            round1_results, round2_results = run_round1_and_2_pipelined(
                plan_text, active_models, config
            )
        else:
            round1_results = run_round1(plan_text, active_models, config)

        for result in round1_results.values():
            if result["status"] == "success":
                tokens = result.get("tokens", {})
                total_tokens["input"] += tokens.get("input", 0)
                total_tokens["output"] += tokens.get("output", 0)
                total_tokens["total"] += tokens.get("total", 0)
                total_cost += estimate_cost(result.get("model", ""), tokens.get("input", 0), tokens.get("output", 0))

    if 2 in mode_config["rounds"] and not round2_results:
        round2_results = run_round2(plan_text, round1_results, active_models, config)

    if round2_results:
        for result in round2_results.values():
            if result.get("status") == "success":
                tokens = result.get("tokens", {})
                total_tokens["input"] += tokens.get("input", 0)
                total_tokens["output"] += tokens.get("output", 0)
                total_tokens["total"] += tokens.get("total", 0)
                total_cost += estimate_cost(result.get("model", ""), tokens.get("input", 0), tokens.get("output", 0))
        if mode_config["deduplicate"]:
            merged_issues = deduplicate_issues(round1_results, round2_results)
            log_progress(f"去重：{len(merged_issues)} 个独立问题", "info")

    round3_result = None
    if mode_config["judge"]:
        round3_result = run_round3(plan_text, round1_results, round2_results, merged_issues, config)
        if round3_result and round3_result.get("status") == "success":
            tokens = round3_result.get("tokens", {})
            total_tokens["input"] += tokens.get("input", 0)
            total_tokens["output"] += tokens.get("output", 0)
            total_tokens["total"] += tokens.get("total", 0)
            total_cost += estimate_cost(round3_result.get("model", ""), tokens.get("input", 0), tokens.get("output", 0))

    total_time = time.time() - t_start

    if args.mode == "fast":
        report = build_fast_report(str(plan_path), round1_results, config, total_time, total_tokens)
    elif args.mode == "debate":
        report = build_debate_report(str(plan_path), round1_results, round2_results, merged_issues, config, total_time, total_tokens)
    else:
        if round3_result and round3_result["status"] == "success":
            report = round3_result["content"]
            stats = f"""

---

## 📈 统计

| 指标 | 数值 |
|------|------|
| 总 Token 消耗 | {total_tokens.get('total', '?')} |
| 总耗时 | {total_time:.1f}s |
| 估算费用 | ¥{total_cost:.2f} |
| 发现问题（确认/排除） | {len(merged_issues)}/0 |
| 质量门控 | {round3_result.get('gate', '?')} |
"""
            report += stats
        else:
            log_progress("Chairman 裁决失败，降级为 debate 报告", "error")
            report = build_debate_report(str(plan_path), round1_results, round2_results, merged_issues, config, total_time, total_tokens)

    log_progress(f"全部完成：{total_time:.1f}s | {total_tokens.get('total', '?')} tokens | ¥{total_cost:.2f}", "success")

    tz = timezone(timedelta(hours=8))
    ts = datetime.now(tz).strftime("%Y%m%d-%H%M%S")
    output_path = args.output or f"./council-verified-{ts}.md"
    Path(output_path).write_text(report, encoding="utf-8")
    log_progress(f"报告已写入：{output_path}", "success")

    print(report)

    if args.json:
        json_path = output_path.replace(".md", ".json")
        json_data = {
            "mode": args.mode,
            "models": active_models,
            "total_time_s": total_time,
            "total_tokens": total_tokens,
            "estimated_cost_cny": total_cost,
            "gate": round3_result.get("gate") if round3_result else None,
            "round1": {k: {"parsed": v.get("parsed"), "parse_failed": v.get("parse_failed")} for k, v in round1_results.items()},
            "round2": {k: {"parsed": v.get("parsed"), "parse_failed": v.get("parse_failed")} for k, v in round2_results.items()} if round2_results else {},
            "merged_issues": merged_issues,
        }
        Path(json_path).write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
        log_progress(f"JSON 已写入：{json_path}", "success")


def _do_dry_run(plan_text: str, active_models: list[str], config: dict, mode: str):
    """Dry-run 模式：打印配置和 prompt 预览。"""
    print("=" * 60)
    print("🔍 Council Dry-Run")
    print("=" * 60)
    print("\n✅ 配置检查：")
    for model_key in active_models:
        key = config[model_key]["api_key"]
        masked = key[:12] + "..." + key[-4:] if len(key) > 16 else "***"
        print(f"   {model_key}: {config[model_key]['model']} @ {config[model_key]['base_url']}")
        print(f"      Key: {masked}")
        print(f"      Auth: {config[model_key]['auth_header']}")
    judge_cfg = config["judge"]
    jkey = judge_cfg["api_key"]
    jmasked = jkey[:12] + "..." + jkey[-4:] if len(jkey) > 16 else "***"
    print(f"   judge: {judge_cfg['model']} @ {judge_cfg['base_url']}")
    print(f"      Key: {jmasked}")
    print(f"\n   审查模式: {mode}")
    print(f"   活跃模型: {', '.join(active_models)}")
    for model_key in active_models:
        messages = build_round1_messages(plan_text, model_key)
        sys_tokens = count_tokens(messages[0]["content"])
        user_tokens = count_tokens(messages[1]["content"])
        print(f"\n📋 {model_key} Round 1 Prompt 预览：")
        print(f"   System: {len(messages[0]['content'])} chars → ~{sys_tokens} tokens")
        print(f"   User: {len(messages[1]['content'])} chars → ~{user_tokens} tokens")
    n_models = len(active_models)
    plan_tokens = count_tokens(plan_text)
    avg_output = 500
    if mode == "fast":
        n_calls = n_models
    elif mode == "debate":
        n_calls = n_models + (n_models if n_models >= 2 else 0)
    else:
        n_calls = n_models + (n_models if n_models >= 2 else 0) + 1
    print(f"\n💰 成本预估（tiktoken={'✅' if HAS_TIKTOKEN else '❌ char/2 估算'}）：")
    print(f"   调用次数: {n_calls}")
    print(f"   估算 input tokens: ~{plan_tokens * n_calls}")
    print(f"   估算 output tokens: ~{avg_output * n_calls}")
    ref_model = config[active_models[0]]["model"]
    rates = COST_PER_M.get(ref_model, {"input": 2.0, "output": 8.0})
    est_input_cost = (plan_tokens * n_calls / 1_000_000) * rates["input"]
    est_output_cost = (avg_output * n_calls / 1_000_000) * rates["output"]
    est_total = est_input_cost + est_output_cost
    print(f"   估算费用: ¥{est_total:.2f}")
    print("\n（未实际调用 API）")


if __name__ == "__main__":
    main()
