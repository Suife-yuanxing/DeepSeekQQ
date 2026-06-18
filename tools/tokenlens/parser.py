"""JSONL 流式解析器 + 聚合引擎 — TokenLens 核心模块"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .pricing import calc_cost

logger = logging.getLogger("tokenlens.parser")


def _iso_date(ts: str | None) -> str:
    """从 ISO 8601 时间戳提取日期（YYYY-MM-DD）"""
    if not ts:
        return "unknown"
    try:
        return ts[:10]
    except (TypeError, IndexError):
        return "unknown"


class Aggregator:
    """JSONL 数据聚合器（单例模式，内存缓存）"""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        # 文件追踪：{filepath: mtime} 用于增量刷新检测
        self._file_mtimes: dict[str, float] = {}
        # 文件列表缓存：用于检测新增文件
        self._known_files: set[str] = set()
        # 解析后的记录列表
        self._records: list[dict] = []
        # 聚合缓存
        self._cache: dict | None = None
        # 扫描统计
        self.skip_stats: dict[str, int] = defaultdict(int)
        self.last_scan_time: str = ""
        self.total_files: int = 0

    # ─── 文件扫描 ────────────────────────────────────────

    def _walk_jsonl_files(self) -> list[tuple[str, str]]:
        """递归扫描所有 JSONL 文件

        返回 [(filepath, source)] 列表
        source: "main" | "subagent"
        """
        result: list[tuple[str, str]] = []
        data_dir_str = str(self.data_dir)

        for root, dirs, _files in os.walk(self.data_dir):
            # 跳过 tool-results 目录
            if "tool-results" in dirs:
                dirs.remove("tool-results")

            for f in _files:
                if not f.endswith(".jsonl"):
                    self.skip_stats["non_jsonl"] += 1
                    continue
                filepath = os.path.join(root, f)
                # 判断 source
                rel = os.path.relpath(filepath, data_dir_str)
                if "subagents" in rel.replace("\\", "/").split("/"):
                    source = "subagent"
                else:
                    source = "main"
                result.append((filepath, source))

        return result

    def _extract_project(self, filepath: str) -> str:
        """从文件路径提取项目名

        ~/.claude/projects/<project>/... → <project>
        """
        rel = os.path.relpath(filepath, str(self.data_dir))
        parts = rel.replace("\\", "/").split("/")
        if parts:
            return parts[0]
        return "unknown"

    # ─── 单文件解析 ──────────────────────────────────────

    def _parse_file(self, filepath: str, source: str, project: str) -> list[dict]:
        """逐行解析单个 JSONL 文件，返回 record dict 列表"""
        records: list[dict] = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    # 跳过注释行和空行
                    if line.startswith("#") or not line.strip():
                        continue

                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        self.skip_stats["bad_lines"] += 1
                        continue

                    # 只处理 assistant 事件
                    if record.get("type") != "assistant":
                        continue

                    message = record.get("message", {})
                    if not message:
                        self.skip_stats["no_message"] += 1
                        continue

                    model = message.get("model")
                    if model is None:
                        self.skip_stats["no_model"] += 1
                        continue

                    # 过滤 <synthetic> 内部合成消息（usage 全 0）
                    if model == "<synthetic>":
                        self.skip_stats["synthetic"] += 1
                        continue

                    usage = message.get("usage", {})
                    if not usage:
                        self.skip_stats["no_usage"] += 1
                        continue

                    # 检查是否为 API 错误事件
                    if record.get("isApiErrorMessage"):
                        self.skip_stats["api_error"] += 1
                        continue

                    input_tokens = usage.get("input_tokens") or 0
                    cache_read = usage.get("cache_read_input_tokens") or 0
                    cache_create = usage.get("cache_creation_input_tokens") or 0
                    output_tokens = usage.get("output_tokens") or 0

                    # 全 0 跳过
                    if input_tokens == 0 and cache_read == 0 and output_tokens == 0:
                        self.skip_stats["zero_usage"] += 1
                        continue

                    entry = {
                        "uuid": record.get("uuid", ""),
                        "input_tokens": input_tokens,
                        "cache_read_tokens": cache_read,
                        "cache_create_tokens": cache_create,
                        "output_tokens": output_tokens,
                        "model": model,
                        "timestamp": record.get("timestamp", ""),
                        "project": project,
                        "cwd": (record.get("cwd") or "").lower(),
                        "session_id": record.get("sessionId", ""),
                        "source": source,
                    }
                    records.append(entry)

        except OSError as e:
            logger.warning(f"无法读取文件 {filepath}: {e}")
            self.skip_stats["unreadable"] += 1

        return records

    # ─── 去重 ───────────────────────────────────────────

    @staticmethod
    def _deduplicate(records: list[dict]) -> list[dict]:
        """按 uuid 去重，优先保留 usage 总和更大的记录"""
        # 按 (uuid, -usage_sum) 排序，非零版本排前面
        records.sort(key=lambda r: (
            r["uuid"],
            -(r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"]),
        ))
        seen: set[str] = set()
        result: list[dict] = []
        ghost_count = 0
        for r in records:
            uid = r["uuid"]
            if uid not in seen:
                seen.add(uid)
                result.append(r)
            else:
                # 重复记录——检查是否为幽灵记录（第一个 usage=0，第二个有数据）
                ghost_count += 1

        if ghost_count > 0:
            logger.debug(f"去重: 删除 {ghost_count} 条重复记录 (总计 {len(records)} → {len(result)})")

        return result

    # ─── 增量检测 ────────────────────────────────────────

    def _check_file_changes(self, all_files: list[tuple[str, str]]) -> list[tuple[str, str, str, str]]:
        """检测需要重新解析的文件

        返回 [(filepath, source, project, reason)] 列表
        reason: "new" | "modified"
        """
        current_files: set[str] = set()
        changed: list[tuple[str, str, str, str]] = []

        for filepath, source in all_files:
            current_files.add(filepath)
            project = self._extract_project(filepath)

            try:
                mtime = os.path.getmtime(filepath)
            except OSError:
                continue

            if filepath not in self._known_files:
                # 新文件
                changed.append((filepath, source, project, "new"))
                self._file_mtimes[filepath] = mtime
            elif self._file_mtimes.get(filepath, 0) < mtime:
                # 文件已修改
                changed.append((filepath, source, project, "modified"))
                self._file_mtimes[filepath] = mtime

        self._known_files = current_files
        return changed

    # ─── 聚合查询 ────────────────────────────────────────

    def _aggregate(self) -> dict:
        """全量聚合计算"""
        projects: dict[str, dict] = defaultdict(lambda: {"models": defaultdict(lambda: {
            "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
            "count": 0, "sessions": set(), "daily": defaultdict(lambda: {
                "input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "count": 0,
            }),
            "source_main": 0, "source_subagent": 0,
            "source_main_tokens": 0, "source_subagent_tokens": 0,
        })})

        for r in self._records:
            proj = r["project"]
            model = r["model"]
            date = _iso_date(r["timestamp"])

            m = projects[proj]["models"][model]
            m["input"] += r["input_tokens"]
            m["output"] += r["output_tokens"]
            m["cache_read"] += r["cache_read_tokens"]
            m["cache_create"] += r["cache_create_tokens"]
            m["count"] += 1
            m["sessions"].add(r["session_id"])

            # source 维度
            src_key = f"source_{r['source']}"
            m[src_key] += 1
            m[f"{src_key}_tokens"] += r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"]

            # daily 维度
            m["daily"][date]["input"] += r["input_tokens"]
            m["daily"][date]["output"] += r["output_tokens"]
            m["daily"][date]["cache_read"] += r["cache_read_tokens"]
            m["daily"][date]["cache_create"] += r["cache_create_tokens"]
            m["daily"][date]["count"] += 1

        # 计算汇总
        result: dict[str, dict] = {}
        for proj_name, proj_data in projects.items():
            models_out: list[dict] = []
            total_cost = 0.0
            total_tokens = 0
            total_cache_read = 0
            total_input = 0

            for model_name, md in proj_data["models"].items():
                sessions = md.pop("sessions")
                daily = dict(md.pop("daily"))
                src_main = md.pop("source_main")
                src_subagent = md.pop("source_subagent")
                src_main_tokens = md.pop("source_main_tokens")
                src_subagent_tokens = md.pop("source_subagent_tokens")

                cost = calc_cost(model_name, md["input"], md["cache_read"], md["output"])
                cache_hit_rate = 0.0
                true_input = md["input"] + md["cache_read"]
                if true_input > 0:
                    cache_hit_rate = md["cache_read"] / true_input

                total_input += md["input"]
                total_cache_read += md["cache_read"]
                total_tokens += true_input + md["output"]

                model_info = {
                    "model": model_name,
                    **md,
                    "cost": cost,
                    "cost_rmb": cost,
                    "cache_hit_rate": cache_hit_rate,
                    "session_count": len(sessions),
                    "daily": daily,
                    "source_main": src_main,
                    "source_subagent": src_subagent,
                    "source_main_tokens": src_main_tokens,
                    "source_subagent_tokens": src_subagent_tokens,
                }
                if cost is not None:
                    total_cost += cost
                models_out.append(model_info)

            # 按费用降序
            models_out.sort(key=lambda x: x.get("cost") or 0, reverse=True)

            result[proj_name] = {
                "project": proj_name,
                "models": models_out,
                "total_cost": total_cost,
                "total_tokens": total_tokens,
                "total_input": total_input,
                "total_cache_read": total_cache_read,
                "total_output": total_tokens - total_input - total_cache_read,
                "overall_cache_hit_rate": (
                    total_cache_read / (total_input + total_cache_read)
                    if (total_input + total_cache_read) > 0
                    else 0.0
                ),
            }

        return result

    # ─── 公开 API ────────────────────────────────────────

    def scan(self, force: bool = False) -> str:
        """扫描数据目录

        返回刷新摘要字符串
        """
        all_files = self._walk_jsonl_files()

        if force or not self._records:
            # 全量扫描
            self._records = []
            self.skip_stats.clear()
            self._file_mtimes = {}
            self._known_files = set()

            for filepath, source in all_files:
                project = self._extract_project(filepath)
                records = self._parse_file(filepath, source, project)
                self._records.extend(records)
                try:
                    self._file_mtimes[filepath] = os.path.getmtime(filepath)
                except OSError:
                    pass
                self._known_files.add(filepath)

            self._records = self._deduplicate(self._records)
        else:
            # 增量刷新
            changed = self._check_file_changes(all_files)

            if changed:
                # 移除旧记录（按文件路径匹配，不可行——记录不追踪来源文件）
                # 简化：移除所有与被修改文件同项目的记录，然后重新扫描相关文件
                affected_projects = {p for _, _, p, _ in changed}
                self._records = [r for r in self._records if r["project"] not in affected_projects]

                for filepath, source, project, reason in changed:
                    records = self._parse_file(filepath, source, project)
                    self._records.extend(records)

                self._records = self._deduplicate(self._records)

        # 清除聚合缓存
        self._cache = None
        self.total_files = len(all_files)
        self.last_scan_time = datetime.now(timezone.utc).isoformat()

        return (
            f"扫描完成: {len(all_files)} 文件, "
            f"{len(self._records)} 条记录 (去重后), "
            f"跳过: {dict(self.skip_stats)}"
        )

    def get_stats(self, project: str | None = None) -> dict:
        """获取聚合统计数据"""
        if self._cache is None:
            self._cache = self._aggregate()

        if project:
            return self._cache.get(project, {})
        return self._cache

    def get_projects(self) -> list[str]:
        """返回所有项目名"""
        if self._cache is None:
            self._cache = self._aggregate()
        return sorted(self._cache.keys())

    def get_models(self, project: str | None = None) -> list[dict]:
        """获取模型列表及统计"""
        stats = self.get_stats(project)
        if project:
            return stats.get("models", [])
        # 全项目汇总
        all_models: dict[str, dict] = defaultdict(lambda: {
            "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
            "count": 0, "sessions": set(),
            "source_main": 0, "source_subagent": 0,
            "source_main_tokens": 0, "source_subagent_tokens": 0,
        })
        for proj_data in self._cache.values():
            for m in proj_data.get("models", []):
                key = m["model"]
                all_models[key]["input"] += m["input"]
                all_models[key]["output"] += m["output"]
                all_models[key]["cache_read"] += m["cache_read"]
                all_models[key]["cache_create"] += m["cache_create"]
                all_models[key]["count"] += m["count"]
                all_models[key]["source_main"] += m.get("source_main", 0)
                all_models[key]["source_subagent"] += m.get("source_subagent", 0)
                all_models[key]["source_main_tokens"] += m.get("source_main_tokens", 0)
                all_models[key]["source_subagent_tokens"] += m.get("source_subagent_tokens", 0)

        result = []
        for model_name, md in all_models.items():
            cost = calc_cost(model_name, md["input"], md["cache_read"], md["output"])
            true_input = md["input"] + md["cache_read"]
            hit_rate = md["cache_read"] / true_input if true_input > 0 else 0.0
            result.append({
                "model": model_name,
                **{k: v for k, v in md.items() if k != "sessions"},
                "session_count": len(md["sessions"]),
                "cost": cost,
                "cache_hit_rate": hit_rate,
            })

        result.sort(key=lambda x: x.get("cost") or 0, reverse=True)
        return result

    def get_models_by_period(
        self, period: str, tz: int = 8, project: str | None = None,
        source: str = "all",
    ) -> list[dict]:
        """获取按时间周期过滤后的模型统计

        这是核心查询方法——修复了原 get_models() 不按时间过滤的 bug。
        source: "all" | "main" | "subagent" — 在聚合前按来源过滤记录
        """
        from .format_utils import is_in_period

        # 过滤 records
        filtered = [
            r for r in self._records
            if is_in_period(r["timestamp"], period, tz)
            and (project is None or r["project"] == project)
            and (source == "all" or r["source"] == source)
        ]

        if not filtered:
            return []

        # 按模型聚合
        models: dict[str, dict] = defaultdict(lambda: {
            "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
            "count": 0, "sessions": set(),
            "source_main": 0, "source_subagent": 0,
            "source_main_tokens": 0, "source_subagent_tokens": 0,
            "daily": defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "count": 0}),
        })

        for r in filtered:
            m = models[r["model"]]
            m["input"] += r["input_tokens"]
            m["output"] += r["output_tokens"]
            m["cache_read"] += r["cache_read_tokens"]
            m["cache_create"] += r["cache_create_tokens"]
            m["count"] += 1
            m["sessions"].add(r["session_id"])

            src_key = f"source_{r['source']}"
            m[src_key] += 1
            m[f"{src_key}_tokens"] += (
                r["input_tokens"] + r["cache_read_tokens"] + r["output_tokens"]
            )

            date = _iso_date(r["timestamp"])
            m["daily"][date]["input"] += r["input_tokens"]
            m["daily"][date]["output"] += r["output_tokens"]
            m["daily"][date]["cache_read"] += r["cache_read_tokens"]
            m["daily"][date]["count"] += 1

        result = []
        for model_name, md in models.items():
            cost = calc_cost(model_name, md["input"], md["cache_read"], md["output"])
            true_input = md["input"] + md["cache_read"]
            hit_rate = md["cache_read"] / true_input if true_input > 0 else 0.0
            daily = dict(md.pop("daily"))

            result.append({
                "model": model_name,
                "input": md["input"],
                "output": md["output"],
                "cache_read": md["cache_read"],
                "cache_create": md["cache_create"],
                "count": md["count"],
                "session_count": len(md["sessions"]),
                "source_main": md["source_main"],
                "source_subagent": md["source_subagent"],
                "source_main_tokens": md["source_main_tokens"],
                "source_subagent_tokens": md["source_subagent_tokens"],
                "cost": cost,
                "cache_hit_rate": hit_rate,
                "daily": {k: dict(v) for k, v in daily.items()},
            })

        result.sort(key=lambda x: x.get("cost") or 0, reverse=True)
        return result

    def get_daily_trend(
        self, period: str, tz: int = 8, project: str | None = None
    ) -> list[dict]:
        """获取每日 Token 趋势数据（供图表使用）"""
        from .format_utils import is_in_period

        filtered = [
            r for r in self._records
            if is_in_period(r["timestamp"], period, tz)
            and (project is None or r["project"] == project)
        ]

        daily: dict[str, dict] = defaultdict(lambda: {
            "input": 0, "output": 0, "cache_read": 0, "cost": 0.0,
        })

        for r in filtered:
            date = _iso_date(r["timestamp"])
            d = daily[date]
            d["input"] += r["input_tokens"]
            d["output"] += r["output_tokens"]
            d["cache_read"] += r["cache_read_tokens"]
            cost = calc_cost(r["model"], r["input_tokens"], r["cache_read_tokens"], r["output_tokens"])
            if cost is not None:
                d["cost"] += cost

        result = []
        for date in sorted(daily.keys()):
            d = daily[date]
            result.append({
                "date": date,
                "input": d["input"],
                "output": d["output"],
                "cache_read": d["cache_read"],
                "total": d["input"] + d["output"] + d["cache_read"],
                "cost": round(d["cost"], 4),
            })

        return result
