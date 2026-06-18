"""FastAPI 服务 + 静态文件"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .advisor import get_cached_or_none, get_llm_advice, get_rule_advice, set_llm_cache
from .config import Config
from .format_utils import format_cost, format_tokens, format_tokens_short, get_period_boundary, get_period_label, is_in_period, short_id
from .parser import Aggregator
from .pricing import CACHE_PATH, PRICING, calc_cost, reload_pricing
from .summary import generate_summary, get_cached_summary, set_cached_summary

logger = logging.getLogger("tokenlens.server")

# 全局 Aggregator 单例
_aggregator: Aggregator | None = None

app = FastAPI(
    title="TokenLens",
    description="自建 Token 用量看板",
    version="1.0.0",
)

# ─── 请求超时中间件 ────────────────────────────────────
# M5: 防止长时间运行的端点（/api/refresh, /api/summary）无限挂起

_REQUEST_TIMEOUT = 60  # 秒


@app.middleware("http")
async def timeout_middleware(request: Request, call_next):
    """为每个请求添加超时保护。"""
    try:
        return await asyncio.wait_for(call_next(request), timeout=_REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"请求超时 ({_REQUEST_TIMEOUT}s): {request.method} {request.url.path}")
        return JSONResponse(
            status_code=504,
            content={"error": "请求超时", "timeout_seconds": _REQUEST_TIMEOUT},
        )

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"


def get_aggregator() -> Aggregator:
    if _aggregator is None:
        raise HTTPException(503, "数据聚合器未初始化，请稍后重试")
    return _aggregator


# ─── API 端点 ────────────────────────────────────────


@app.get("/api/health")
async def api_health():
    """健康检查 + 项目列表"""
    if _aggregator is None:
        return {
            "status": "initializing",
            "last_scan": "",
            "projects": 0,
            "projects_list": [],
        }
    return {
        "status": "ok",
        "last_scan": _aggregator.last_scan_time,
        "projects": len(_aggregator.get_projects()),
        "projects_list": _aggregator.get_projects(),
    }


@app.get("/api/refresh")
async def api_refresh():
    """强制全量刷新数据"""
    agg = get_aggregator()
    loop = asyncio.get_running_loop()
    summary = await loop.run_in_executor(None, agg.scan, True)
    return {"status": "ok", "summary": summary, "records": len(agg._records)}


@app.get("/api/stats")
async def api_stats(
    period: str = Query("week", description="day|week|month|3month|year"),
    tz: int = Query(8, description="时区偏移"),
    project: str | None = Query(None, description="项目名过滤"),
):
    """核心统计 — 现在正确按 period 过滤"""
    agg = get_aggregator()

    # 使用 period 过滤的查询（核心修复）
    models = agg.get_models_by_period(period, tz, project)

    if not models:
        return {
            "project": project or "all",
            "period": period,
            "models": [],
            "total_cost": 0,
            "total_tokens": 0,
            "total_input": 0,
            "total_cache_read": 0,
            "total_output": 0,
            "overall_cache_hit_rate": 0.0,
            "session_count": 0,
        }

    total_input = sum(m["input"] for m in models)
    total_cache_read = sum(m["cache_read"] for m in models)
    total_output = sum(m["output"] for m in models)
    total_cost = sum(m.get("cost") or 0 for m in models)

    return {
        "models": models,
        "total_cost": total_cost,
        "total_tokens": total_input + total_cache_read + total_output,
        "total_input": total_input,
        "total_cache_read": total_cache_read,
        "total_output": total_output,
        "overall_cache_hit_rate": (
            total_cache_read / (total_input + total_cache_read)
            if (total_input + total_cache_read) > 0
            else 0.0
        ),
        "session_count": sum(m.get("session_count", 0) for m in models),
        "project": project or "all",
        "period": period,
        "period_label": get_period_label(period, tz),
    }


@app.get("/api/stats/compare")
async def api_stats_compare(
    period: str = Query("week", description="day|week|month|3month|year"),
    tz: int = Query(8, description="时区偏移"),
    project: str | None = Query(None, description="项目名过滤"),
):
    """周期对比 — 返回当前周期 + 上一周期统计数据"""
    from datetime import timedelta

    agg = get_aggregator()

    # 当前周期
    current = agg.get_models_by_period(period, tz, project)

    # 上一周期：使用 boundary 差值作为偏移
    boundary = get_period_boundary(period, tz)
    # 计算上一周期的边界：取当前 boundary 和下一个更小 period 的差值
    period_days = {"day": 1, "week": 7, "month": 30, "3month": 90, "year": 365}
    delta_days = period_days.get(period, 7)
    prev_start = boundary - timedelta(days=delta_days)
    prev_end = boundary

    # 手动过滤上一周期的记录
    prev_filtered = [
        r for r in agg._records
        if (project is None or r["project"] == project)
    ]
    prev_in_range = []
    for r in prev_filtered:
        ts = r["timestamp"]
        if not ts:
            continue
        try:
            ts_clean = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts_clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError, AttributeError):
            continue
        if prev_start <= dt < prev_end:
            prev_in_range.append(r)

    # 聚合上一周期
    prev_models: dict[str, dict] = {}
    for r in prev_in_range:
        m = prev_models.setdefault(r["model"], {
            "input": 0, "output": 0, "cache_read": 0, "count": 0,
        })
        m["input"] += r["input_tokens"]
        m["output"] += r["output_tokens"]
        m["cache_read"] += r["cache_read_tokens"]
        m["count"] += 1

    prev_total = sum(
        m["input"] + m["cache_read"] + m["output"]
        for m in prev_models.values()
    )
    prev_cost = sum(
        (calc_cost(model, m["input"], m["cache_read"], m["output"]) or 0)
        for model, m in prev_models.items()
    )
    prev_input = sum(m["input"] for m in prev_models.values())
    prev_cache_read = sum(m["cache_read"] for m in prev_models.values())

    prev_cache_hit = (
        prev_cache_read / (prev_input + prev_cache_read)
        if (prev_input + prev_cache_read) > 0
        else 0.0
    )

    # 当前周期汇总
    cur_total_input = sum(m["input"] for m in current)
    cur_total_cache = sum(m["cache_read"] for m in current)
    cur_total_output = sum(m["output"] for m in current)
    cur_total_cost = sum(m.get("cost") or 0 for m in current)
    cur_total_tokens = cur_total_input + cur_total_cache + cur_total_output
    cur_session_count = sum(m.get("session_count", 0) for m in current)
    cur_cache_hit = (
        cur_total_cache / (cur_total_input + cur_total_cache)
        if (cur_total_input + cur_total_cache) > 0
        else 0.0
    )

    def _delta(cur, prev):
        """计算变化率，prev=0 时返回 None"""
        if prev and prev != 0:
            return round((cur - prev) / prev, 4)
        return None

    return {
        "period": period,
        "period_label": get_period_label(period, tz),
        "current": {
            "total_tokens": cur_total_tokens,
            "total_cost": cur_total_cost,
            "cache_hit_rate": cur_cache_hit,
            "session_count": cur_session_count,
            "model_count": len(current),
        },
        "previous": {
            "total_tokens": prev_total,
            "total_cost": prev_cost,
            "cache_hit_rate": prev_cache_hit,
            "session_count": len(set(
                r.get("session_id", "") for r in prev_in_range if r.get("session_id")
            )),
            "model_count": len(prev_models),
        },
        "delta": {
            "total_tokens": _delta(cur_total_tokens, prev_total),
            "total_cost": _delta(cur_total_cost, prev_cost),
            "cache_hit_rate": _delta(cur_cache_hit, prev_cache_hit),
            "session_count": _delta(cur_session_count,
                len(set(r.get("session_id", "") for r in prev_in_range if r.get("session_id")))),
        },
        "project": project or "all",
    }


@app.get("/api/models")
async def api_models(
    period: str = Query("week", description="day|week|month|3month|year"),
    tz: int = Query(8, description="时区偏移"),
    project: str | None = Query(None, description="项目名过滤"),
    source: str = Query("all", description="main|subagent|all"),
):
    """各模型用量 + 缓存命中率 — 在聚合层面按 source 过滤"""
    agg = get_aggregator()
    models = agg.get_models_by_period(period, tz, project, source)

    return {
        "models": models,
        "period": period,
        "source": source,
        "project": project or "all",
    }


@app.get("/api/cache-advice")
async def api_cache_advice(
    model: str | None = Query(None, description="按模型过滤"),
    period: str = Query("week"),
    tz: int = Query(8, description="时区偏移"),
    project: str | None = Query(None, description="项目名过滤"),
):
    """缓存 AI 建议（规则 + LLM 可选）— 现在正确按 period 过滤"""
    agg = get_aggregator()
    models = agg.get_models_by_period(period, tz, project)

    if model:
        models = [m for m in models if m["model"] == model]

    if not models:
        return {"severity": "🟢 正常", "advice": "暂无数据", "warnings": [], "llm_enhanced": False}

    # 使用最主要模型的数据
    primary = models[0]
    hit_rate = primary.get("cache_hit_rate", 0)
    input_tokens = primary.get("input", 0)
    cache_read = primary.get("cache_read", 0)

    rule_result = get_rule_advice(hit_rate, cache_read, input_tokens)

    # LLM 增强（仅在启用且有 API Key 时）
    if Config.llm_enabled and os.getenv("DEEPSEEK_API_KEY"):
        cache_key = f"{primary['model']}:{hit_rate:.4f}"
        cached = get_cached_or_none(cache_key)
        if cached:
            rule_result["llm_enhanced"] = True
            rule_result["llm_advice"] = cached
        else:
            # 异步获取 LLM 建议（不阻塞响应）
            try:
                llm_advice = await get_llm_advice(
                    primary, hit_rate,
                    timeout=Config.llm_timeout,
                    max_retries=Config.llm_max_retries,
                )
                if llm_advice:
                    set_llm_cache(cache_key, llm_advice)
                    rule_result["llm_enhanced"] = True
                    rule_result["llm_advice"] = llm_advice
            except Exception as e:
                logger.warning(f"LLM 建议生成失败: {e}")

    return rule_result


@app.get("/api/sessions")
async def api_sessions(
    limit: int = Query(20, ge=1, le=100),
    period: str = Query("week"),
    project: str | None = Query(None),
    tz: int = Query(8),
):
    """最近会话列表 — 聚合所有消息的 token/cost 到 session 级别"""
    agg = get_aggregator()

    # 聚合：session_id → {tokens, cost, models: set, ...}
    sessions: dict[str, dict] = {}
    for r in agg._records:
        sid = r["session_id"]
        if not sid:
            continue
        if not is_in_period(r["timestamp"], period, tz):
            continue
        if project and r["project"] != project:
            continue

        cost = calc_cost(r["model"], r["input_tokens"], r["cache_read_tokens"], r["output_tokens"])

        if sid not in sessions:
            sessions[sid] = {
                "session_id": sid,
                "short_id": short_id(sid),
                "project": r["project"],
                "models": set(),
                "model_msgs": {},  # model → msg_count for primary detection
                "first_ts": r["timestamp"],
                "last_ts": r["timestamp"],
                "tokens": 0,
                "cost": 0.0,
                "cwd": r["cwd"],
                "msg_count": 0,
            }

        s = sessions[sid]
        s["tokens"] += r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"]
        if cost is not None:
            s["cost"] += cost
        s["models"].add(r["model"])
        s["model_msgs"][r["model"]] = s["model_msgs"].get(r["model"], 0) + 1
        s["msg_count"] += 1
        # 追踪最早和最晚时间戳
        if r["timestamp"] and (not s["first_ts"] or r["timestamp"] < s["first_ts"]):
            s["first_ts"] = r["timestamp"]
        if r["timestamp"] and r["timestamp"] > s["last_ts"]:
            s["last_ts"] = r["timestamp"]

    # 转换输出
    session_list = []
    for s in sessions.values():
        session_list.append({
            "session_id": s["session_id"],
            "short_id": s["short_id"],
            "project": s["project"],
            "primary_model": max(s["model_msgs"], key=lambda m: s["model_msgs"][m]) if s["model_msgs"] else "",
            "models_used": sorted(s["models"]),
            "timestamp": s["first_ts"],
            "last_ts": s["last_ts"],
            "tokens": s["tokens"],
            "cost": s["cost"],
            "cwd": s["cwd"],
            "msg_count": s["msg_count"],
        })

    # 按时间排序，取最近 N 条
    sorted_sessions = sorted(
        session_list,
        key=lambda s: s.get("timestamp", ""),
        reverse=True,
    )
    return {
        "sessions": sorted_sessions[:limit],
        "total": len(sorted_sessions),
    }


@app.get("/api/summary")
async def api_summary(
    session: str = Query(..., description="session ID"),
):
    """生成工作摘要 — 从 JSONL 提取 user 消息，调用 LLM 生成摘要"""
    # 检查缓存
    cached = get_cached_summary(session)
    if cached:
        return {"session": session, "summary": cached, "cached": True}

    if not Config.llm_enabled:
        return {"session": session, "summary": None, "error": "llm_disabled"}

    if not os.getenv("DEEPSEEK_API_KEY"):
        return {"session": session, "summary": None, "error": "no_api_key"}

    agg = get_aggregator()

    # 从 JSONL 文件中提取该 session 的 user 消息
    user_messages: list[dict] = []
    seen_files = set()
    for r in agg._records:
        if r["session_id"] == session and r.get("_filepath"):
            seen_files.add(r["_filepath"])

    # 如果 records 中没有 _filepath，回退到扫描所有 JSONL 文件
    if not seen_files:
        for filepath, _source in agg._walk_jsonl_files():
            seen_files.add(filepath)

    for filepath in seen_files:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("#") or not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("session_id") != session:
                        continue
                    if record.get("type") != "user":
                        continue
                    msg = record.get("message", {})
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        user_messages.append({
                            "content": content,
                            "timestamp": record.get("timestamp", ""),
                            "session_id": session,
                        })
        except Exception:
            continue

    if not user_messages:
        return {"session": session, "summary": None, "error": "no_user_messages"}

    # 调用 LLM 生成摘要
    try:
        summary = await generate_summary(user_messages)
        if summary:
            set_cached_summary(session, summary)
            return {"session": session, "summary": summary, "cached": False}
        return {"session": session, "summary": None, "error": "llm_failed"}
    except Exception as e:
        logger.warning(f"摘要生成失败: {e}")
        return {"session": session, "summary": None, "error": str(e)}


@app.get("/api/export")
async def api_export(
    format: str = Query("csv", description="csv|json"),
    period: str = Query("week"),
    project: str | None = Query(None),
    tz: int = Query(8),
):
    """数据导出（CSV/JSON）"""
    agg = get_aggregator()

    rows = []
    for r in agg._records:
        if not is_in_period(r["timestamp"], period, tz):
            continue
        if project and r["project"] != project:
            continue
        cost = calc_cost(r["model"], r["input_tokens"], r["cache_read_tokens"], r["output_tokens"])
        rows.append({
            "date": r["timestamp"][:10] if r["timestamp"] else "unknown",
            "model": r["model"],
            "project": r["project"],
            "source": r["source"],
            "input_tokens": r["input_tokens"],
            "cache_read_tokens": r["cache_read_tokens"],
            "output_tokens": r["output_tokens"],
            "cost_rmb": cost,
        })

    if format == "csv":
        if not rows:
            return Response(
                "date,model,project,source,input_tokens,cache_read_tokens,output_tokens,cost_rmb\n",
                media_type="text/csv",
            )

        csv_lines = ["date,model,project,source,input_tokens,cache_read_tokens,output_tokens,cost_rmb"]
        for row in rows:
            cost_str = f"{row['cost_rmb']:.6f}" if row['cost_rmb'] is not None else ""
            csv_lines.append(
                f"{row['date']},{row['model']},{row['project']},{row['source']},"
                f"{row['input_tokens']},{row['cache_read_tokens']},{row['output_tokens']},"
                f"{cost_str}"
            )
        return Response("\n".join(csv_lines) + "\n", media_type="text/csv")

    return {"export": rows, "format": "json", "count": len(rows)}


@app.get("/api/trend")
async def api_trend(
    period: str = Query("week", description="day|week|month|3month|year"),
    tz: int = Query(8, description="时区偏移"),
    project: str | None = Query(None, description="项目名过滤"),
):
    """每日 Token 趋势数据（供图表使用）"""
    agg = get_aggregator()
    daily = agg.get_daily_trend(period, tz, project)
    return {
        "daily": daily,
        "period": period,
        "project": project or "all",
    }


@app.get("/api/hourly")
async def api_hourly(
    period: str = Query("week", description="day|week|month|3month|year"),
    tz: int = Query(8, description="时区偏移"),
    project: str | None = Query(None, description="项目名过滤"),
):
    """按小时聚合的 Token 用量（供热力图使用）"""
    agg = get_aggregator()

    hourly = [0] * 24
    for r in agg._records:
        if not is_in_period(r["timestamp"], period, tz):
            continue
        if project and r["project"] != project:
            continue
        try:
            # 提取小时（本地时间）
            dt = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
            local_hour = (dt.hour + tz) % 24
            tokens = r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"]
            hourly[local_hour] += tokens
        except (ValueError, AttributeError):
            continue

    # 找出最大值用于前端配色
    max_val = max(hourly) if max(hourly) > 0 else 1

    return {
        "hourly": hourly,
        "max": max_val,
        "period": period,
        "project": project or "all",
    }


@app.get("/api/tools")
async def api_tools(
    period: str = Query("week", description="day|week|month|3month|year"),
    project: str | None = Query(None, description="项目名过滤"),
    tz: int = Query(8),
):
    """工具调用统计（从 JSONL 中提取 tool_use 信息）"""
    agg = get_aggregator()

    tool_counts: dict[str, int] = {}
    # 需要重新扫描 JSONL 获取 tool_use 信息
    for filepath, source in agg._walk_jsonl_files():
        if project:
            proj = agg._extract_project(filepath)
            if proj != project:
                continue
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("#") or not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("type") != "assistant":
                        continue
                    ts = record.get("timestamp", "")
                    if not is_in_period(ts, period, tz):
                        continue
                    message = record.get("message", {})
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                name = block.get("name", "unknown")
                                tool_counts[name] = tool_counts.get(name, 0) + 1
        except Exception:
            continue

    tools = [{"name": k, "count": v} for k, v in sorted(
        tool_counts.items(), key=lambda x: -x[1]
    )[:12]]

    return {
        "tools": tools,
        "period": period,
        "project": project or "all",
    }


@app.get("/api/network")
async def api_network(request: Request):
    """返回服务器网络信息（供移动端访问引导）"""
    import socket

    host = request.client.host if request.client else "unknown"
    port = request.url.port or 8090

    # 获取本机 LAN IP
    lan_ip = "unknown"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("10.254.254.254", 1))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            lan_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass

    return {
        "client_host": host,
        "server_port": port,
        "lan_ip": lan_ip,
        "mobile_url": f"http://{lan_ip}:{port}" if lan_ip != "unknown" else None,
    }


# ─── 静态文件 ────────────────────────────────────────


@app.get("/")
async def index():
    """首页"""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(
            index_path,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    return HTMLResponse("<h1>TokenLens</h1><p>index.html not found</p>")


@app.get("/static/{filename:path}")
async def static_files(filename: str):
    """静态文件服务"""
    file_path = STATIC_DIR / filename
    if not file_path.exists():
        raise HTTPException(404)
    return FileResponse(
        file_path,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/pricing")
async def api_pricing():
    """返回当前使用的定价表和元信息"""
    meta = {
        "source": "hardcoded_defaults",
        "fetched_at": None,
        "cache_path": str(CACHE_PATH),
    }

    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("pricing"):
                meta["source"] = "cache"
                meta["fetched_at"] = cached.get("_fetched_iso")
        except Exception:
            pass

    return {
        "pricing": PRICING,
        "meta": meta,
        "usd_to_rmb": float(os.getenv("TOKENLENS_USD_TO_RMB", "7.25")),
    }


@app.post("/api/pricing/refresh")
async def api_pricing_refresh():
    """强制刷新定价缓存"""
    from .pricing_fetcher import refresh_pricing

    try:
        new_pricing = await refresh_pricing(force=True)
        reload_pricing()
        return {
            "status": "ok",
            "models": len(new_pricing),
            "pricing": new_pricing,
        }
    except Exception as e:
        raise HTTPException(500, f"定价刷新失败: {e}")


@app.get("/api/billing")
async def api_billing():
    """获取官方余额 + 实际花费（通过余额变化计算）

    返回:
      - 各平台当前余额
      - 历史追踪的实际花费（余额减少量）
      - 与本地估算的对比
      - 首次运行警告（无历史数据，需多次查询才能累积花费）

    环境变量: DEEPSEEK_API_KEY / MOONSHOT_API_KEY / KIMI_API_KEY
    """
    from .billing_fetcher import fetch_all_billing

    billing = await fetch_all_billing()

    # 计算本地估算
    agg = get_aggregator()
    local_total = 0.0
    for r in agg._records:
        cost = calc_cost(r["model"], r["input_tokens"], r["cache_read_tokens"], r["output_tokens"])
        if cost is not None:
            local_total += cost

    billing.local_estimate = local_total

    return {
        "platforms": {
            platform: {
                "current_balance": s.current_balance,
                "previous_balance": s.previous_balance,
                "spent_since_last": s.spent_since_last,
                "total_spent_tracked": s.total_spent_tracked,
                "error": s.error,
            }
            for platform, s in billing.platforms.items()
        },
        "total_official_spend": billing.total_official_spend,
        "total_balance": billing.total_balance,
        "local_estimate": billing.local_estimate,
        "discrepancy_pct": billing.discrepancy_pct,
        "is_first_run": billing.is_first_run,
        "fetched_at": billing.fetched_at,
        "note": (
            "实际花费通过余额变化计算（余额减少 → 花费）。"
            "首次运行无历史数据，需多次查询才能累积花费。"
            "余额增加（充值）不会被计入花费。"
        ),
    }


# ─── 生命周期 ────────────────────────────────────────


def init_aggregator(data_dir: str | Path) -> Aggregator:
    """初始化聚合器并执行首次扫描"""
    global _aggregator
    _aggregator = Aggregator(data_dir=data_dir)
    _aggregator.scan(force=True)
    return _aggregator
