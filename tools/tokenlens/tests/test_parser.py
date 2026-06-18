"""parser.py 单元测试"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tools.tokenlens.parser import Aggregator, _iso_date


class TestIsoDate:
    def test_normal(self):
        assert _iso_date("2026-06-15T10:30:00Z") == "2026-06-15"

    def test_with_offset(self):
        assert _iso_date("2026-06-15T10:30:00+08:00") == "2026-06-15"

    def test_empty(self):
        assert _iso_date("") == "unknown"

    def test_none(self):
        assert _iso_date(None) == "unknown"  # type: ignore


class TestDeduplicate:
    def test_unique_uuids_preserved(self):
        records = [
            {"uuid": "a", "input_tokens": 100, "cache_read_tokens": 0, "output_tokens": 50},
            {"uuid": "b", "input_tokens": 200, "cache_read_tokens": 0, "output_tokens": 100},
        ]
        result = Aggregator._deduplicate(records)
        assert len(result) == 2

    def test_duplicate_keeps_higher_usage(self):
        """幽灵记录（全 0）vs 真实记录 → 保留真实记录"""
        records = [
            {"uuid": "same", "input_tokens": 0, "cache_read_tokens": 0, "output_tokens": 0},
            {"uuid": "same", "input_tokens": 100, "cache_read_tokens": 50, "output_tokens": 200},
        ]
        result = Aggregator._deduplicate(records)
        assert len(result) == 1
        assert result[0]["input_tokens"] == 100

    def test_duplicate_identical(self):
        """完全相同的记录只保留一个"""
        records = [
            {"uuid": "same", "input_tokens": 100, "cache_read_tokens": 50, "output_tokens": 200},
            {"uuid": "same", "input_tokens": 100, "cache_read_tokens": 50, "output_tokens": 200},
        ]
        result = Aggregator._deduplicate(records)
        assert len(result) == 1

    def test_empty_list(self):
        result = Aggregator._deduplicate([])
        assert result == []


class TestParserWithTempFile:
    """使用临时文件测试解析器"""

    @staticmethod
    def _assistant_line(**overrides) -> dict:
        """生成一个标准的 assistant 记录"""
        ts = (datetime.now(timezone.utc)).isoformat()
        return {
            "type": "assistant",
            "uuid": "test-uuid-001",
            "timestamp": ts,
            "sessionId": "test-session",
            "cwd": "D:\\QQmaonian",
            "isApiErrorMessage": False,
            "message": {
                "model": "deepseek-v4-pro",
                "role": "assistant",
                "usage": {
                    "input_tokens": 1000,
                    "cache_read_input_tokens": 500,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 200,
                },
            },
            **overrides,
        }

    def test_parse_valid_assistant(self):
        """解析标准 assistant 记录"""
        entry = self._assistant_line()
        line = json.dumps(entry) + "\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(line)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 1
            r = records[0]
            assert r["model"] == "deepseek-v4-pro"
            assert r["input_tokens"] == 1000
            assert r["cache_read_tokens"] == 500
            assert r["output_tokens"] == 200
            assert r["project"] == "test-project"
            assert r["source"] == "main"
        finally:
            os.unlink(tmp_path)

    def test_skip_non_assistant(self):
        """跳过非 assistant 记录"""
        entry = self._assistant_line()
        entry["type"] = "user"
        line = json.dumps(entry) + "\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(line)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 0
        finally:
            os.unlink(tmp_path)

    def test_skip_synthetic(self):
        """跳过 <synthetic> 模型"""
        entry = self._assistant_line()
        entry["message"]["model"] = "<synthetic>"
        line = json.dumps(entry) + "\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(line)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 0
        finally:
            os.unlink(tmp_path)

    def test_skip_no_model(self):
        """跳过无 model 的记录"""
        entry = self._assistant_line()
        del entry["message"]["model"]
        line = json.dumps(entry) + "\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(line)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 0
        finally:
            os.unlink(tmp_path)

    def test_skip_bad_json(self):
        """JSON 解析错误不崩溃"""
        lines = '{"valid": "json"}\n' + "not valid json\n" + '{"valid": "json"}\n'

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(lines)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 0  # 没有 assistant 记录
            assert agg.skip_stats["bad_lines"] == 1
        finally:
            os.unlink(tmp_path)

    def test_skip_comment_lines(self):
        """跳过 # 注释行"""
        entry = self._assistant_line()
        lines = (
            "# metadata line\n"
            + json.dumps(entry) + "\n"
            + "   \n"  # blank line
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(lines)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 1
        finally:
            os.unlink(tmp_path)

    def test_zero_usage_skipped(self):
        """全 0 usage 跳过"""
        entry = self._assistant_line()
        entry["message"]["usage"] = {
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "output_tokens": 0,
        }
        line = json.dumps(entry) + "\n"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(line)
            tmp_path = f.name

        try:
            agg = Aggregator(data_dir=os.path.dirname(tmp_path))
            records = agg._parse_file(tmp_path, "main", "test-project")
            assert len(records) == 0
        finally:
            os.unlink(tmp_path)

    def test_extract_project_from_path(self):
        """从路径提取项目名"""
        agg = Aggregator(data_dir="/home/user/.claude/projects")
        proj = agg._extract_project(
            "/home/user/.claude/projects/d--QQmaonian/some-session.jsonl"
        )
        assert proj == "d--QQmaonian"


class TestAggregatorScan:
    """集成测试：完整扫描流程"""

    def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agg = Aggregator(data_dir=tmpdir)
            summary = agg.scan(force=True)
            assert agg.total_files == 0
            assert "0 文件" in summary

    def test_scan_with_data(self):
        """端到端扫描测试"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建模拟项目目录
            proj_dir = os.path.join(tmpdir, "d--test")
            os.makedirs(proj_dir)

            entry = {
                "type": "assistant",
                "uuid": "end-to-end-test",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sessionId": "test-session",
                "cwd": "/home/user/test",
                "isApiErrorMessage": False,
                "message": {
                    "model": "deepseek-v4-pro",
                    "role": "assistant",
                    "usage": {
                        "input_tokens": 5000,
                        "cache_read_input_tokens": 1000,
                        "cache_creation_input_tokens": 0,
                        "output_tokens": 500,
                    },
                },
            }

            jsonl_path = os.path.join(proj_dir, "test-session.jsonl")
            with open(jsonl_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

            agg = Aggregator(data_dir=tmpdir)
            summary = agg.scan(force=True)
            assert agg.total_files == 1
            assert len(agg._records) == 1
            assert agg._records[0]["project"] == "d--test"
            assert agg._records[0]["source"] == "main"

            # 测试聚合
            models = agg.get_models()
            assert len(models) == 1
            assert models[0]["model"] == "deepseek-v4-pro"
            assert models[0]["input"] == 5000
            assert models[0]["cache_read"] == 1000

            # 测试项目列表
            projects = agg.get_projects()
            assert "d--test" in projects
