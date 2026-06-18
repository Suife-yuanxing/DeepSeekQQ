"""Council Skill API 客户端。

提供 LLM API 调用、JSON 解析、消息构建等功能。
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

# 确保 prompts 和 scripts 目录在路径中
_SCRIPT_DIR = Path(__file__).resolve().parent
_SKILL_DIR = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

# 尝试导入 httpx，失败则回退到 urllib
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from config import (
    DEFAULT_TIMEOUT, MODEL_OVERRIDES, MIMO_MAX_PLAN_CHARS,
)
from utils import log_progress, truncate_to_tokens
from prompts.review_prompts import MODEL_ROLE_MAP, OUTPUT_SCHEMA
from prompts.critique_prompts import CROSS_VALIDATION_SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════════════════════════
# JSON 解析
# ═══════════════════════════════════════════════════════════════════════════════

def extract_json(text: str) -> Optional[dict]:
    """从 LLM 响应中稳健提取 JSON。

    策略：先无修改尝试，失败后逐步应用修复。
    """
    if not text:
        return None

    def _try_parse(raw: str) -> Optional[dict]:
        """核心解析：去 fence → 找大括号 → 字符串感知边界 → json.loads。"""
        fence_match = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
        if fence_match:
            cleaned = fence_match.group(1)
        else:
            cleaned = re.sub(r'```(?:json)?\s*', '', raw)

        start = cleaned.find('{')
        if start < 0:
            return None

        depth = 0
        end_pos = -1
        in_string = False
        escape = False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"' and not in_string:
                in_string = True
                continue
            if ch == '"' and in_string:
                in_string = False
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_pos = i
                    break

        if end_pos < 0:
            end_pos = cleaned.rfind('}')
            if end_pos <= start:
                return None

        candidate = cleaned[start:end_pos + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        try:
            fixed = re.sub(r',\s*}', '}', candidate)
            fixed = re.sub(r',\s*]', ']', fixed)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        try:
            fixed = re.sub(r'"\s*\n\s*"', '",\n  "', candidate)
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        return None

    def _normalize_quotes(s: str) -> str:
        """标准化弯引号为直引号。"""
        s = s.replace('“', '"').replace('”', '"')
        s = s.replace('‘', "'").replace('’', "'")
        s = s.replace('「', '"').replace('」', '"')
        s = s.replace('『', '"').replace('』', '"')
        return s

    result = _try_parse(text)
    if result:
        return result

    normalized = _normalize_quotes(text)
    if normalized != text:
        result = _try_parse(normalized)
        if result:
            return result

    return None


def extract_json_fields_regex(text: str) -> dict:
    """用正则从非结构化/JSON-like 文本中提取关键字段（parse_failed 降级用）。

    当 extract_json 失败时调用，尽力从 LLM 原始输出中恢复结构化数据。
    支持两种输入：JSON-like 文本（最常见——LLM 输出了 JSON 但有小格式错误）、
    纯自然语言文本（少见——LLM 完全没输出 JSON）。
    """
    result: dict = {"raw_text": text, "issues": [], "score": 0, "summary": "", "strengths": [], "suggestions": []}

    # ── 1. 提取 score ──
    score_match = re.search(r'(?:"score"|score|评分|分数)\s*[:：]\s*(\d+)', text, re.IGNORECASE)
    if score_match:
        try:
            result["score"] = int(score_match.group(1))
        except ValueError:
            pass

    # ── 2. 提取 summary ──
    summary_match = re.search(r'(?:"summary"|summary)\s*[:：]\s*"([^"]{10,500})"', text, re.IGNORECASE)
    if not summary_match:
        summary_match = re.search(r'(?:摘要|总结)[：:]\s*(.{20,500})', text)
    if summary_match:
        result["summary"] = summary_match.group(1)

    # ── 3. 提取 issues 数组 ── 从 JSON-like 文本中匹配每个 issue 对象
    # 策略 A：逐对匹配大括号，提取每个 issue 对象
    issue_blocks = _extract_json_objects(text, '"id"') or _extract_json_objects(text, '"severity"')
    if not issue_blocks:
        # 策略 B：尝试匹配 "issues" 数组后的内容
        issues_section = re.search(r'"issues"\s*[:：]\s*\[(.*?)\]\s*[,}]', text, re.DOTALL)
        if issues_section:
            issue_blocks = _extract_json_objects(issues_section.group(1), '"title"')

    if issue_blocks:
        for block in issue_blocks:
            issue = _parse_issue_block(block)
            if issue.get("title"):
                result["issues"].append(issue)

    # ── 4. 兜底：策略 B 也失败时，用标题行提取 ──
    if not result["issues"]:
        # 匹配 Markdown 标题行 + 列表项风格（LLM 完全不用 JSON 的情况）
        raw_issues = re.findall(
            r'(?:^|\n)\s*(?:[-*#]|\d+[.)])\s*\*{0,2}(?:\[([^\]]+)\]\s*)?(.{10,150})',
            text, re.MULTILINE
        )
        for idx, match in enumerate(raw_issues):
            sev_text = (match[0] or "").lower()
            severity = "high" if "high" in sev_text or "🔴" in sev_text else \
                       "medium" if "medium" in sev_text or "🟡" in sev_text else \
                       "low" if "low" in sev_text or "🟢" in sev_text else "medium"
            result["issues"].append({
                "id": f"RX-{idx + 1}",
                "severity": severity,
                "title": match[1].strip(),
                "detail": "",
                "evidence": "",
                "fix_suggestion": "",
                "_extracted_by_regex": True,
            })

    # ── 5. 提取 strengths ──
    strengths_match = re.search(r'"strengths"\s*[:：]\s*\[(.*?)\]', text, re.DOTALL)
    if strengths_match:
        items = re.findall(r'"([^"]{10,200})"', strengths_match.group(1))
        result["strengths"] = items[:10]

    # ── 6. 提取 suggestions ──
    suggestions_match = re.search(r'"suggestions"\s*[:：]\s*\[(.*?)\]', text, re.DOTALL)
    if suggestions_match:
        sug_blocks = _extract_json_objects(suggestions_match.group(1), '"title"')
        for block in sug_blocks:
            title_match = re.search(r'"title"\s*[:：]\s*"([^"]+)"', block)
            detail_match = re.search(r'"detail"\s*[:：]\s*"([^"]+)"', block)
            benefit_match = re.search(r'"benefit"\s*[:：]\s*"([^"]+)"', block)
            if title_match:
                result["suggestions"].append({
                    "title": title_match.group(1),
                    "detail": detail_match.group(1) if detail_match else "",
                    "benefit": benefit_match.group(1) if benefit_match else "",
                })

    return result


def _extract_json_objects(text: str, anchor_field: str) -> list[str]:
    """从文本中提取所有包含 anchor_field 的 JSON 对象块。

    通过大括号配对找到每个 { ... } 对象，检查是否含 anchor_field，
    是则返回该块文本。
    """
    blocks = []
    seen_starts = set()
    for match in re.finditer(r'\{', text):
        start = match.start()
        if start in seen_starts:
            continue
        seen_starts.add(start)
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    block = text[start:i + 1]
                    # 过滤：1) 含 anchor_field 2) 非过大容器 3) anchor_field 在顶层(depth=1)
                    if anchor_field in block and len(block) > 20 and len(block) < 3000:
                        # 检查 anchor_field 是否在深度1出现（非嵌套在子对象中）
                        if _is_top_level_key(block, anchor_field):
                            blocks.append(block)
                    break
        if len(blocks) >= 50:
            break
    return blocks


def _is_top_level_key(json_block: str, key: str) -> bool:
    """检查 key 是否出现在 JSON 块的顶层(depth=1)，而非嵌套在子对象中。

    仅跟踪大括号深度，不跟踪方括号（数组不影响对象层级判断）。
    在进入字符串前检查 key 匹配（key 本身是带引号的，如 '"id"'）。
    """
    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(json_block):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            # 进入字符串前检查：当前深度是否为目标 key
            if not in_string and depth == 1 and json_block[i:i + len(key)] == key:
                return True
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
    return False


def _parse_issue_block(block: str) -> dict:
    """从单个 issue JSON 块中用正则提取字段（容错解析）。"""
    def _field(name: str) -> str:
        # 匹配 "name": "value" 或 "name":"value"
        m = re.search(rf'"{name}"\s*[:：]\s*"((?:[^"\\]|\\.)*)"', block)
        return m.group(1) if m else ""

    issue = {
        "id": _field("id") or "RX-?",
        "severity": _field("severity") or "medium",
        "title": _field("title"),
        "detail": _field("detail"),
        "evidence": _field("evidence"),
        "fix_suggestion": _field("fix_suggestion"),
        "_extracted_by_regex": True,
    }
    # 规范化 severity
    sev = issue["severity"].lower()
    if sev not in ("high", "medium", "low"):
        if "high" in sev or "🔴" in sev or "严重" in sev:
            issue["severity"] = "high"
        elif "low" in sev or "🟢" in sev:
            issue["severity"] = "low"
        else:
            issue["severity"] = "medium"
    return issue


# ═══════════════════════════════════════════════════════════════════════════════
# API 调用
# ═══════════════════════════════════════════════════════════════════════════════

def call_model(model_config: dict, messages: list[dict],
               max_tokens: int = 2048, temperature: float = 0.3,
               max_retries: int = 1, timeout: Optional[int] = None) -> dict:
    """调用 LLM API，返回结果字典。"""
    model = model_config["model"]
    base_url = model_config["base_url"]
    api_key = model_config["api_key"]
    auth_header = model_config["auth_header"]

    if not api_key:
        return {"status": "error", "error": "API key not configured", "raw_text": None}

    effective_timeout = timeout if timeout is not None else DEFAULT_TIMEOUT

    if auth_header == "api-key":
        headers = {"api-key": api_key, "Content-Type": "application/json"}
    else:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    override = MODEL_OVERRIDES.get(model, {})
    payload.update(override)

    url = f"{base_url}/chat/completions"

    for attempt in range(max_retries + 1):
        try:
            t0 = time.time()

            if HAS_HTTPX:
                with httpx.Client(timeout=effective_timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
            else:
                import urllib.request
                import urllib.error
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode(),
                    headers=headers,
                )
                with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                    data = json.loads(resp.read())

            elapsed = time.time() - t0
            content = data["choices"][0]["message"]["content"]
            # 推理模型（如 glm-5.2）content 可能为空，降级到 reasoning_content
            if not content:
                content = data["choices"][0]["message"].get("reasoning_content", "")
            usage = data.get("usage", {})

            return {
                "status": "success",
                "content": content,
                "tokens": {
                    "input": usage.get("prompt_tokens", 0),
                    "output": usage.get("completion_tokens", 0),
                    "total": usage.get("total_tokens", 0),
                },
                "time_s": round(elapsed, 2),
                "finish_reason": data["choices"][0].get("finish_reason", "?"),
                "model": model,
            }

        except Exception as e:
            # Extract detailed error from HTTP response body
            error_detail = str(e)[:300]
            try:
                if hasattr(e, 'response') and e.response is not None:
                    error_detail = str(e.response.json())[:300]
            except Exception:
                try:
                    error_detail = str(e.response.text)[:300] if hasattr(e, 'response') and e.response else error_detail
                except Exception:
                    pass
            try:
                if hasattr(e, 'read') and not error_detail:
                    error_detail = e.read().decode()[:300]
            except Exception:
                pass
            if attempt < max_retries:
                log_progress(f"{model}: 重试 {attempt + 1}/{max_retries}（{error_detail}）", "info")
                time.sleep(2)
                continue
            return {
                "status": "error",
                "error": error_detail,
                "raw_text": None,
                "model": model,
            }


def call_model_with_json_retry(model_config: dict, messages: list[dict],
                              max_tokens: int = 2048, timeout: Optional[int] = None,
                              json_retries: int = 1) -> dict:
    """调用 LLM API，JSON 解析失败时自动发送修正请求重试。"""
    result = call_model(model_config, messages, max_tokens=max_tokens, timeout=timeout)

    if result["status"] != "success":
        return result

    parsed = extract_json(result["content"])
    if parsed:
        result["parsed"] = parsed
        result["parse_failed"] = False
        return result

    for retry_n in range(json_retries):
        log_progress(f"  {model_config['model']}: JSON 格式错误，请求修正 ({(retry_n + 1)}/{json_retries})...", "info")

        correction_msg = (
            "你刚才输出的JSON格式不正确，无法解析。"
            "请严格按照JSON格式重新输出，只输出JSON对象，不要添加任何其他文字。"
            "确保：1) 所有字符串用双引号 2) 没有尾随逗号 3) 大括号配对正确"
        )
        retry_messages = messages + [
            {"role": "assistant", "content": result["content"][:2000]},
            {"role": "user", "content": correction_msg},
        ]

        retry_result = call_model(model_config, retry_messages,
                                  max_tokens=max_tokens, timeout=timeout)
        if retry_result["status"] != "success":
            break

        parsed = extract_json(retry_result["content"])
        if parsed:
            retry_result["parsed"] = parsed
            retry_result["parse_failed"] = False
            retry_result["_json_retries"] = retry_n + 1
            retry_result["tokens"]["input"] += result["tokens"].get("input", 0)
            retry_result["tokens"]["output"] += result["tokens"].get("output", 0)
            retry_result["tokens"]["total"] = (
                retry_result["tokens"].get("input", 0)
                + retry_result["tokens"].get("output", 0)
            )
            return retry_result

    regex_extracted = extract_json_fields_regex(result["content"])
    result["parsed"] = regex_extracted
    result["parse_failed"] = True
    # 优先保留完整 raw_text 供后续分析，仅当内容过大（>16K chars）时截断
    full_content = result["content"]
    if len(full_content) > 16000:
        result["raw_text"] = full_content[:16000] + "\n\n[... raw_text truncated at 16K chars ...]"
    else:
        result["raw_text"] = full_content
    result["_json_extraction"] = "regex" if regex_extracted.get("issues") else "regex_titles"
    result["_json_retries"] = json_retries
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# 消息构建
# ═══════════════════════════════════════════════════════════════════════════════

def build_round1_messages(plan_text: str, model_key: str) -> list[dict]:
    """为指定模型构建 Round 1 审查消息。"""
    role_info = MODEL_ROLE_MAP[model_key]
    system_prompt = role_info["system_prompt"]
    prefix = role_info["prefix"]

    schema_with_prefix = OUTPUT_SCHEMA.replace("{PREFIX}", prefix)

    effective_plan = plan_text
    if model_key == "mimo" and len(plan_text) > MIMO_MAX_PLAN_CHARS:
        half = MIMO_MAX_PLAN_CHARS // 2
        effective_plan = (
            plan_text[:half]
            + f"\n\n[... 中间 {len(plan_text) - MIMO_MAX_PLAN_CHARS} 字符已省略，共 {len(plan_text)} 字符 ...]\n\n"
            + plan_text[-half:]
        )
        log_progress(f"  Mimo 方案截断: {len(plan_text)} → {len(effective_plan)} 字符", "info")

    return [
        {"role": "system", "content": system_prompt + "\n\n" + schema_with_prefix},
        {"role": "user", "content": f"请审查以下方案：\n\n{effective_plan}"},
    ]


def build_cross_validation_messages(plan_text: str, reviewer_key: str,
                                    target_key: str,
                                    target_report: dict) -> list[dict]:
    """为交叉验证构建消息。"""
    role_info = MODEL_ROLE_MAP[reviewer_key]
    target_role_info = MODEL_ROLE_MAP[target_key]

    target_parsed = target_report.get("parsed", {})
    if target_parsed and not target_report.get("parse_failed"):
        target_text = json.dumps(target_parsed, ensure_ascii=False, indent=2)
    else:
        target_text = target_report.get("raw_text", "") or target_report.get("content", "")

    system_prompt = CROSS_VALIDATION_SYSTEM_PROMPT
    system_prompt = system_prompt.replace("{reviewer_role}", role_info["persona"])
    system_prompt = system_prompt.replace("{reviewer_persona}", role_info["persona"])
    system_prompt = system_prompt.replace("{target_model}", target_key)
    system_prompt = system_prompt.replace("{target_role}", target_role_info["persona"])
    system_prompt = system_prompt.replace("{original_plan}", plan_text)
    system_prompt = system_prompt.replace("{target_report}", target_text)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请逐条验证上述审查报告中的每个 issue。"},
    ]
