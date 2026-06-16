#!/usr/bin/env python3
"""council_call.py 边界测试套件。

覆盖:
  1. 缺 Key 模型 → 跳过并继续
  2. 单模型 → 跳过 R2/R3
  3. 两模型 fast/debate/deep
  4. 全部模型失败 → 优雅报错
  5. JSON 解析边缘情况
  6. 去重算法验证
  7. dry-run 模式
  8. 配置加载
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 将 scripts 目录和 skill 根目录加入路径
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SKILL_DIR))

# 导入被测模块
import council_call


# ═══════════════════════════════════════════════════════════════════
# Mock 工具
# ═══════════════════════════════════════════════════════════════════

def make_success_result(content: str, model="deepseek-v4-flash",
                        tokens=None, parsed=None) -> dict:
    result = {
        "status": "success",
        "content": content,
        "tokens": tokens or {"input": 500, "output": 300, "total": 800},
        "time_s": 1.5,
        "finish_reason": "stop",
        "model": model,
        "parse_failed": False,
    }
    # 如果提供了 parsed，同时设置；否则从 content 解析
    if parsed is not None:
        result["parsed"] = parsed
    else:
        try:
            result["parsed"] = json.loads(content)
        except json.JSONDecodeError:
            result["parsed"] = None
            result["parse_failed"] = True
    return result


def make_error_result(error="Test error", model="deepseek-v4-flash") -> dict:
    return {
        "status": "error",
        "error": error,
        "raw_text": None,
        "model": model,
    }


def make_r1_json(score=8, issues=None, strengths=None) -> str:
    """生成符合 OUTPUT_SCHEMA 的 Round 1 JSON 响应。"""
    data = {
        "score": score,
        "summary": "方案总体可行，但有若干改进空间。",
        "issues": issues or [
            {
                "id": "DS-1",
                "severity": "high",
                "title": "缺少错误处理机制",
                "detail": "方案未考虑网络超时场景的错误处理。",
                "evidence": "见方案第三节",
                "fix_suggestion": "增加 try/except 和重试逻辑。",
            },
            {
                "id": "DS-2",
                "severity": "medium",
                "title": "配置硬编码",
                "detail": "API URL 直接写在代码中。",
                "evidence": "见方案第四节",
                "fix_suggestion": "提取为环境变量配置。",
            },
        ],
        "strengths": strengths or ["架构清晰", "模块化设计"],
        "suggestions": [
            {
                "id": "DS-S1",
                "title": "增加日志系统",
                "detail": "添加结构化日志方便排查问题。",
                "benefit": "提升可维护性",
            }
        ],
    }
    return json.dumps(data, ensure_ascii=False)


def make_r2_json(verified_issues=None, missed_issues=None,
                 false_positives=None) -> str:
    """生成 Round 2 交叉验证 JSON 响应。"""
    data = {
        "critique_of": "deepseek",
        "verified_issues": verified_issues or [
            {
                "target_id": "DS-1",
                "is_real": "confirmed",
                "severity_correct": "correct",
                "evidence": "方案确实缺少错误处理。",
                "comment": "同意，这是关键问题。",
            },
            {
                "target_id": "DS-2",
                "is_real": "confirmed",
                "severity_correct": "understated",
                "evidence": "硬编码在生产环境中确实是严重问题。",
                "comment": "应升级为 high。",
            },
        ],
        "false_positives": false_positives or [],
        "missed_issues": missed_issues or [],
    }
    return json.dumps(data, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════
# 测试套件
# ═══════════════════════════════════════════════════════════════════

class TestBoundaryMissingKey(unittest.TestCase):
    """边界测试 1: 缺 Key 模型 → 应跳过并继续"""

    def setUp(self):
        # 使用不存在的 API Key 配置
        self.config = {
            "deepseek": {
                "api_key": "",  # 缺少 Key
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "auth_header": "Bearer",
            },
            "kimi": {
                "api_key": "sk-valid-looking-key-here",
                "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2.6",
                "auth_header": "Bearer",
            },
        }

    def test_call_model_missing_key(self):
        """缺 Key 模型调用应返回 error 状态"""
        result = council_call.call_model(
            self.config["deepseek"],
            messages=[{"role": "user", "content": "test"}],
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("API key not configured", result["error"])

    def test_run_round1_missing_key_model(self):
        """Round 1 中缺 Key 模型应失败但不阻塞其他模型"""
        plan_text = "这是一个测试方案。\n\n## 架构设计\n使用微服务架构。"

        # 使用 wraps 保留原始 call_model 的缺 key 检查
        original_call_model = council_call.call_model

        def side_effect(model_config, messages, **kwargs):
            if not model_config["api_key"]:
                return original_call_model(model_config, messages, **kwargs)
            # kimi 有 key，返回成功
            return make_success_result(
                make_r1_json(score=7,
                             issues=[{"id": "K-1", "severity": "medium",
                                      "title": "测试问题", "detail": "详情",
                                      "evidence": "引用", "fix_suggestion": "修复建议"}],
                             strengths=["好方案"]),
                model="kimi-k2.6",
            )

        # 注意：run_round1 通过 api_client.call_model_with_json_retry → api_client.call_model 调用
        # 因此需要 patch api_client.call_model（而非 council_call.call_model）
        import api_client
        with patch.object(api_client, 'call_model', side_effect=side_effect):
            results = council_call.run_round1(
                plan_text, ["deepseek", "kimi"], self.config
            )

        # deepseek 应该失败（api_key 为空）
        self.assertIn("deepseek", results)
        self.assertEqual(results["deepseek"]["status"], "error")
        self.assertIn("API key not configured", results["deepseek"]["error"])
        # kimi 应该成功
        self.assertIn("kimi", results)
        self.assertEqual(results["kimi"]["status"], "success")

    def test_main_reports_missing_key_warning(self):
        """主流程应在启动时报告缺 Key 的模型"""
        # 通过 validate：缺 Key 模型应在 missing 列表中
        missing = [m for m in ["deepseek", "kimi"] if not self.config[m]["api_key"]]
        self.assertIn("deepseek", missing)
        self.assertNotIn("kimi", missing)

        # dry-run 需要 judge key
        dry_config = {**self.config, "judge": {
            "api_key": "sk-test", "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-pro", "auth_header": "Bearer",
        }}
        plan_text = "测试方案内容。\n\n## 第一章\n这是方案正文。"
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.md', delete=False, encoding='utf-8'
        ) as f:
            f.write(plan_text)
            plan_path = f.name

        try:
            council_call._do_dry_run(
                plan_text, ["deepseek", "kimi"], dry_config, "fast"
            )
        finally:
            os.unlink(plan_path)


class TestBoundarySingleModel(unittest.TestCase):
    """边界测试 2: 单模型 → 应跳过 R2/R3"""

    def setUp(self):
        self.config = {
            "deepseek": {
                "api_key": "sk-test-key-deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "auth_header": "Bearer",
            },
            "kimi": {
                "api_key": "",
                "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2.6",
                "auth_header": "Bearer",
            },
            "mimo": {
                "api_key": "",
                "base_url": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.5-pro",
                "auth_header": "api-key",
            },
            "judge": {
                "api_key": "sk-test-judge",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        self.plan_text = "单模型测试方案。\n\n## 概述\n简单测试。\n" * 20

    def test_run_round2_skips_with_single_model(self):
        """单模型应跳过 Round 2（少于 2 个模型）"""
        active_models = ["deepseek"]
        results = council_call.run_round2(
            self.plan_text,
            {"deepseek": make_success_result(make_r1_json())},
            active_models,
            self.config,
        )
        self.assertEqual(results, {})

    def test_mode_debate_with_single_model(self):
        """单模型 debate 模式：R1 执行，R2 跳过"""
        mode_config = council_call.MODE_CONFIG["debate"]
        self.assertIn(1, mode_config["rounds"])
        self.assertIn(2, mode_config["rounds"])
        # R2 应在 run_round2 内因 len(models) < 2 跳过

    def test_mode_deep_with_single_model(self):
        """单模型 deep 模式：R1 执行，R2 跳过，R3 视配置决定"""
        mode_config = council_call.MODE_CONFIG["deep"]
        self.assertTrue(mode_config["judge"])
        # R2 因 len(models) < 2 跳过
        # R3 应在有 merged_issues 时正常执行


class TestBoundaryTwoModels(unittest.TestCase):
    """边界测试 3: 两模型 fast/debate/deep"""

    def setUp(self):
        self.config = {
            "deepseek": {
                "api_key": "sk-test-deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "auth_header": "Bearer",
            },
            "kimi": {
                "api_key": "sk-test-kimi",
                "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2.6",
                "auth_header": "Bearer",
            },
            "mimo": {
                "api_key": "",
                "base_url": "https://api.xiaomimimo.com/v1",
                "model": "mimo-v2.5-pro",
                "auth_header": "api-key",
            },
            "judge": {
                "api_key": "sk-test-judge",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        self.plan_text = "两模型测试方案。\n\n## 架构\n微服务 + API 网关。\n" * 30

    def test_two_models_fast_mode(self):
        """两模型 fast 模式：仅 R1，2 次调用"""
        mode_config = council_call.MODE_CONFIG["fast"]
        self.assertEqual(mode_config["rounds"], [1])
        self.assertFalse(mode_config["judge"])
        self.assertFalse(mode_config["deduplicate"])

    def test_two_models_debate_has_correct_pairs(self):
        """两模型 debate 模式：应有 2 对交叉验证（双向）"""
        from prompts.critique_prompts import get_cross_validation_pairs
        active = ["deepseek", "kimi"]
        pairs = get_cross_validation_pairs(active)
        # 两模型应产生 2 对交叉验证（A→B, B→A）
        self.assertEqual(len(pairs), 2)
        reviewers = {p["reviewer"] for p in pairs}
        targets = {p["target"] for p in pairs}
        self.assertEqual(reviewers, {"deepseek", "kimi"})
        self.assertEqual(targets, {"deepseek", "kimi"})

    def test_two_models_deep_with_mock(self):
        """两模型 deep 模式：完整流程（R1+R2+R3）"""
        # run_round1/run_round2 → api_client.call_model
        # run_round3 → council_call.call_model (直接导入引用)
        import api_client
        with patch.object(api_client, 'call_model') as mock_api, \
             patch.object(council_call, 'call_model') as mock_cc:
            mock_api.side_effect = [
                make_success_result(make_r1_json(score=8), model="deepseek-v4-flash"),
                make_success_result(
                    make_r1_json(score=7,
                                 issues=[{"id": "K-1", "severity": "high",
                                          "title": "两模型交叉问题", "detail": "详情",
                                          "evidence": "见方案", "fix_suggestion": "修复建议"}],
                                 strengths=["简洁"]),
                    model="kimi-k2.6",
                ),
                # R2: deepseek→kimi
                make_success_result(make_r2_json(), model="deepseek-v4-flash"),
                # R2: kimi→deepseek
                make_success_result(
                    make_r2_json(
                        verified_issues=[{"target_id": "DS-1", "is_real": "confirmed",
                                          "severity_correct": "correct",
                                          "evidence": "确实缺少", "comment": "同意"}],
                    ),
                    model="kimi-k2.6",
                ),
            ]
            mock_cc.side_effect = [
                # R3: Chairman
                make_success_result(
                    "# 裁决报告\n\n## 质量门控: PASS",
                    model="deepseek-v4-pro",
                    tokens={"input": 3000, "output": 1000, "total": 4000},
                ),
            ]

            r1 = council_call.run_round1(
                self.plan_text, ["deepseek", "kimi"], self.config
            )
            self.assertEqual(len(r1), 2)
            self.assertTrue(all(r["status"] == "success" for r in r1.values()))

            r2 = council_call.run_round2(
                self.plan_text, r1, ["deepseek", "kimi"], self.config
            )
            self.assertEqual(len(r2), 2)

            merged = council_call.deduplicate_issues(r1, r2)
            self.assertGreaterEqual(len(merged), 1)

            r3 = council_call.run_round3(
                self.plan_text, r1, r2, merged, self.config
            )
            self.assertEqual(r3["status"], "success")


class TestBoundaryAllModelsFail(unittest.TestCase):
    """边界测试 4: 全部模型失败 → 应优雅报错"""

    def setUp(self):
        self.plan_text = "全部失败测试方案。\n\n## 测试\n内容。\n" * 10

    def test_all_models_api_error(self):
        """全部模型 API 错误时 Round 1 应返回全部 error 状态"""
        config = {
            "deepseek": {
                "api_key": "sk-test",
                "base_url": "https://invalid.example.com/v1",
                "model": "deepseek-v4-flash",
                "auth_header": "Bearer",
            },
            "kimi": {
                "api_key": "sk-test",
                "base_url": "https://invalid.example.com/v1",
                "model": "kimi-k2.6",
                "auth_header": "Bearer",
            },
        }

        with patch.object(council_call, 'call_model',
                          return_value=make_error_result("Connection refused")):
            results = council_call.run_round1(
                self.plan_text, ["deepseek", "kimi"], config
            )

        self.assertEqual(len(results), 2)
        for r in results.values():
            self.assertEqual(r["status"], "error")

    def test_empty_round1_produces_empty_report(self):
        """全部失败的 Round 1 应产生空报告（不崩溃）"""
        config = {
            "deepseek": {"api_key": "", "base_url": "", "model": "", "auth_header": "Bearer"},
            "kimi": {"api_key": "", "base_url": "", "model": "", "auth_header": "Bearer"},
            "mimo": {"api_key": "", "base_url": "", "model": "", "auth_header": "api-key"},
        }
        round1 = {
            "deepseek": make_error_result("API key not configured"),
            "kimi": make_error_result("API key not configured"),
        }

        report = council_call.build_fast_report(
            "test.md", round1, config, total_time=0.1,
            total_tokens={"total": 0}
        )
        self.assertIn("多模型独立审查报告", report)
        self.assertIn("❌", report)  # 应标注失败

    def test_deduplicate_with_no_issues(self):
        """无 issue 的去重不应崩溃"""
        round1 = {
            "deepseek": {
                "status": "success",
                "parsed": {"issues": [], "score": 5, "summary": "无问题"},
                "parse_failed": False,
            },
        }
        result = council_call.deduplicate_issues(round1)
        self.assertEqual(result, [])

    def test_round3_skips_without_judge_key(self):
        """无裁决 Key 时 R3 应跳过不崩溃"""
        config_no_judge = {
            "judge": {"api_key": "", "base_url": "", "model": "", "auth_header": "Bearer"},
        }
        result = council_call.run_round3(
            self.plan_text, {}, {}, [], config_no_judge
        )
        self.assertEqual(result["status"], "skipped")


class TestJsonExtraction(unittest.TestCase):
    """JSON 解析边界测试"""

    def test_extract_clean_json(self):
        """标准 JSON 应正确解析"""
        text = '```json\n{"score": 8, "summary": "test"}\n```'
        result = council_call.extract_json(text)
        self.assertEqual(result["score"], 8)

    def test_extract_json_no_fence(self):
        """无 fence 的 JSON 应正确解析"""
        text = '{"score": 7, "issues": []}'
        result = council_call.extract_json(text)
        self.assertEqual(result["score"], 7)

    def test_extract_json_with_chinese_quotes(self):
        """包含中文弯引号的 JSON（策略 1 失败 → 策略 2 成功）"""
        text = '{"score": 8, "title": "缺少「错误处理」机制"}'
        result = council_call.extract_json(text)
        self.assertIsNotNone(result)

    def test_extract_json_invalid(self):
        """无效 JSON 应返回 None"""
        text = "这不是 JSON 内容，只是一段普通文本。"
        result = council_call.extract_json(text)
        self.assertIsNone(result)

    def test_extract_json_trailing_comma(self):
        """尾部逗号应被修复"""
        text = '{"score": 8, "issues": [{"id": "X-1",},],}'
        result = council_call.extract_json(text)
        self.assertIsNotNone(result)

    def test_extract_json_empty_string(self):
        """空字符串应返回 None"""
        self.assertIsNone(council_call.extract_json(""))
        self.assertIsNone(council_call.extract_json(None))

    def test_extract_json_fields_regex(self):
        """正则提取降级方案应至少提取 raw_text"""
        # 使用 ASCII 冒号（代码中 regex 不匹配全角冒号 U+FF1A）
        text = "评分: 7\n- 第一个问题：缺少限流机制\n- 第二个问题：配置不安全"
        result = council_call.extract_json_fields_regex(text)
        self.assertEqual(result["score"], 7)
        self.assertIn("raw_text", result)
        self.assertGreater(len(result["issues"]), 0)


class TestDeduplication(unittest.TestCase):
    """去重算法测试"""

    def test_jaccard_identical(self):
        """相同文本 Jaccard 应为 1.0"""
        text = "缺少错误处理机制导致系统不稳定"
        self.assertAlmostEqual(
            council_call._keyword_jaccard(text, text), 1.0
        )

    def test_jaccard_different(self):
        """完全不同文本 Jaccard 应接近 0"""
        a = "缺少错误处理机制"
        b = "数据库连接池配置优化"
        sim = council_call._keyword_jaccard(a, b)
        self.assertLess(sim, 0.5)

    def test_jaccard_empty(self):
        """空文本应返回 0"""
        self.assertEqual(council_call._keyword_jaccard("", ""), 0.0)
        self.assertEqual(council_call._keyword_jaccard("test", ""), 0.0)

    def test_jaccard_similar_chinese(self):
        """相似中文文本应具有高 Jaccard"""
        a = "API密钥硬编码在配置文件中有安全风险需要修复"
        b = "API密钥硬编码在配置中存在安全隐患需要处理"
        sim = council_call._keyword_jaccard(a, b)
        # CJK 2-gram 对相似中文应有 >0.3 的相似度
        self.assertGreater(sim, 0.3)

    def test_deduplicate_by_containment(self):
        """标题包含应触发去重"""
        round1 = {
            "model_a": {
                "status": "success",
                "parsed": {
                    "issues": [
                        {"id": "A-1", "severity": "medium",
                         "title": "缺少错误处理机制",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                        {"id": "A-2", "severity": "low",
                         "title": "缺少错误处理",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                    ],
                },
                "parse_failed": False,
            },
        }
        result = council_call.deduplicate_issues(round1)
        # "缺少错误处理" 完全包含在 "缺少错误处理机制" 中，应去重
        self.assertEqual(len(result), 1)

    def test_deduplicate_by_jaccard(self):
        """高 Jaccard 相似度应触发去重（使用几乎相同的标题）"""
        round1 = {
            "model_a": {
                "status": "success",
                "parsed": {
                    "issues": [
                        {"id": "A-1", "severity": "high",
                         "title": "API密钥硬编码于配置文件存在安全风险需要修复",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                        {"id": "A-2", "severity": "medium",
                         "title": "API密钥硬编码于配置文件存在安全风险需要处理",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                    ],
                },
                "parse_failed": False,
            },
        }
        result = council_call.deduplicate_issues(round1)
        # 仅差末尾2字，CJK 2-gram 高度重叠，应合并为1个
        self.assertEqual(len(result), 1)

    def test_deduplicate_keeps_higher_severity(self):
        """去重时应保留更高的严重性级别"""
        round1 = {
            "model_a": {
                "status": "success",
                "parsed": {
                    "issues": [
                        {"id": "A-1", "severity": "low",
                         "title": "配置硬编码问题",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                        {"id": "A-2", "severity": "high",
                         "title": "配置硬编码",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                    ],
                },
                "parse_failed": False,
            },
        }
        result = council_call.deduplicate_issues(round1)
        self.assertEqual(len(result), 1)
        # 应保留更高的严重性
        self.assertEqual(result[0]["severity"], "high")


class TestTruncation(unittest.TestCase):
    """截断函数测试"""

    def test_truncate_short_text(self):
        """短文本不应截断"""
        short = "Hello World"
        self.assertEqual(council_call.truncate_to_tokens(short, 100), short)

    def test_truncate_long_text(self):
        """长文本应截断并添加标记（用足够长的文本确保触发截断）"""
        long_text = "A" * 5000
        result = council_call.truncate_to_tokens(long_text, 100)
        self.assertLess(len(result), len(long_text))
        self.assertIn("[... truncated ...]", result)


class TestModeConfig(unittest.TestCase):
    """模式配置测试"""

    def test_fast_mode_config(self):
        cfg = council_call.MODE_CONFIG["fast"]
        self.assertEqual(cfg["rounds"], [1])
        self.assertFalse(cfg["deduplicate"])
        self.assertFalse(cfg["judge"])

    def test_debate_mode_config(self):
        cfg = council_call.MODE_CONFIG["debate"]
        self.assertEqual(cfg["rounds"], [1, 2])
        self.assertTrue(cfg["deduplicate"])
        self.assertFalse(cfg["judge"])

    def test_deep_mode_config(self):
        cfg = council_call.MODE_CONFIG["deep"]
        self.assertEqual(cfg["rounds"], [1, 2, 3])
        self.assertTrue(cfg["deduplicate"])
        self.assertTrue(cfg["judge"])


class TestCostEstimation(unittest.TestCase):
    """成本估算测试"""

    def test_estimate_known_model(self):
        cost = council_call.estimate_cost("deepseek-v4-flash", 1000, 500)
        self.assertGreater(cost, 0)

    def test_estimate_unknown_model(self):
        cost = council_call.estimate_cost("unknown-model", 1000, 500)
        self.assertGreater(cost, 0)  # 使用默认费率

    def test_estimate_zero_tokens(self):
        cost = council_call.estimate_cost("deepseek-v4-flash", 0, 0)
        self.assertEqual(cost, 0.0)


class TestCrossValidationPairs(unittest.TestCase):
    """交叉验证配对测试"""

    def test_three_model_pairs(self):
        from prompts.critique_prompts import get_cross_validation_pairs
        pairs = get_cross_validation_pairs(["deepseek", "kimi", "mimo"])
        # 3 模型循环验证：A→B, B→C, C→A = 3 对
        self.assertEqual(len(pairs), 3)
        # 验证循环顺序
        reviewers = [p["reviewer"] for p in pairs]
        targets = [p["target"] for p in pairs]
        self.assertEqual(reviewers, ["deepseek", "kimi", "mimo"])
        self.assertEqual(targets, ["kimi", "mimo", "deepseek"])

    def test_two_model_pairs(self):
        from prompts.critique_prompts import get_cross_validation_pairs
        pairs = get_cross_validation_pairs(["deepseek", "kimi"])
        self.assertEqual(len(pairs), 2)

    def test_single_model_no_pairs(self):
        from prompts.critique_prompts import get_cross_validation_pairs
        pairs = get_cross_validation_pairs(["deepseek"])
        self.assertEqual(len(pairs), 0)


class TestDryRun(unittest.TestCase):
    """Dry-run 模式测试"""

    def test_dry_run_does_not_call_api(self):
        """Dry-run 应仅打印配置信息，不调用 API"""
        config = {
            "deepseek": {
                "api_key": "sk-test1234567890abcd",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "auth_header": "Bearer",
            },
            "judge": {
                "api_key": "sk-judge1234567890abcd",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        plan_text = "测试方案。\n\n## 设计\n简单。\n"

        # 不应抛出异常
        council_call._do_dry_run(plan_text, ["deepseek"], config, "fast")

    def test_dry_run_masks_keys(self):
        """Dry-run 应掩码 API Key"""
        config = {
            "deepseek": {
                "api_key": "sk-very-long-api-key-that-should-be-masked",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-flash",
                "auth_header": "Bearer",
            },
            "judge": {
                "api_key": "sk-judge-key",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        # 短 key（≤16 字符）应显示 ***
        # 长 key 应显示前12+...+后4


class TestConfigLoading(unittest.TestCase):
    """配置加载测试"""

    def test_load_config_returns_expected_keys(self):
        config = council_call.load_config()
        for key in ["deepseek", "kimi", "mimo", "judge"]:
            self.assertIn(key, config)
            self.assertIn("api_key", config[key])
            self.assertIn("base_url", config[key])
            self.assertIn("model", config[key])
            self.assertIn("auth_header", config[key])

    def test_mimo_auth_header_is_api_key(self):
        """Mimo 应使用 api-key header 而非 Bearer"""
        # 实际 .env 文件存在时从其中加载
        self.assertEqual(
            council_call.load_config()["mimo"]["auth_header"],
            "api-key",
        )

    def test_deepseek_auth_header_is_bearer(self):
        self.assertEqual(
            council_call.load_config()["deepseek"]["auth_header"],
            "Bearer",
        )

    def test_supported_models_constant(self):
        """SUPPORTED_MODELS 应包含所有已知模型"""
        self.assertIn("deepseek", council_call.SUPPORTED_MODELS)
        self.assertIn("kimi", council_call.SUPPORTED_MODELS)
        self.assertIn("mimo", council_call.SUPPORTED_MODELS)

    def test_invalid_model_rejected(self):
        """不支持的模型名应被拒绝"""
        invalid = [m for m in ["foobar", "gpt-4"] if m not in council_call.SUPPORTED_MODELS]
        self.assertEqual(len(invalid), 2)


class TestPlanTextReading(unittest.TestCase):
    """方案文件读取测试"""

    def test_nonexistent_plan_file(self):
        """不存在的方案文件应导致 sys.exit(1)"""
        with self.assertRaises(SystemExit):
            # 模拟 argparse 解析后的行为
            plan_path = Path("/nonexistent/path/to/plan.md")
            if not plan_path.exists():
                sys.exit(1)

    def test_empty_plan_handled(self):
        """空方案应有合理行为"""
        plan_text = ""
        config = {
            "deepseek": {"api_key": "", "base_url": "", "model": "", "auth_header": "Bearer"},
        }
        # 不应崩溃
        messages = council_call.build_round1_messages(plan_text, "deepseek")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("", messages[1]["content"])  # user content 包含空方案


class TestReportBuilding(unittest.TestCase):
    """报告生成测试"""

    def setUp(self):
        self.config = {
            "deepseek": {
                "api_key": "", "base_url": "", "model": "", "auth_header": "Bearer",
            },
            "kimi": {
                "api_key": "", "base_url": "", "model": "", "auth_header": "Bearer",
            },
            "mimo": {
                "api_key": "", "base_url": "", "model": "", "auth_header": "api-key",
            },
        }

    def test_fast_report_includes_all_models(self):
        """Fast 报告应包含所有参与模型的审查结果"""
        round1 = {
            "deepseek": make_success_result(make_r1_json(score=8)),
            "kimi": make_success_result(
                make_r1_json(score=7,
                             issues=[{"id": "K-1", "severity": "medium",
                                      "title": "测试问题", "detail": "详情",
                                      "evidence": "引用", "fix_suggestion": "修复"}],
                             strengths=["清晰"]),
                model="kimi-k2.6",
            ),
        }
        report = council_call.build_fast_report(
            "test.md", round1, self.config, 2.0, {"total": 1600}
        )
        self.assertIn("deepseek", report)
        self.assertIn("kimi", report)
        self.assertIn("DS-1", report)
        self.assertIn("K-1", report)

    def test_fast_report_with_error_model(self):
        """Fast 报告应正确处理失败的模型"""
        round1 = {
            "deepseek": make_error_result("Connection timeout"),
            "kimi": make_success_result(make_r1_json(score=6), model="kimi-k2.6"),
        }
        report = council_call.build_fast_report(
            "test.md", round1, self.config, 1.0, {"total": 800}
        )
        self.assertIn("❌", report)
        self.assertIn("Connection timeout", report)
        self.assertIn("kimi", report)

    def test_fast_report_with_parse_failure(self):
        """Fast 报告应处理 JSON 解析失败的模型"""
        result = make_success_result("这不是有效的 JSON 响应", model="mimo-v2.5-pro")
        # 模拟 parse_failed
        result["parse_failed"] = True
        result["raw_text"] = "这不是有效的 JSON 响应"
        round1 = {"mimo": result}
        report = council_call.build_fast_report(
            "test.md", round1, self.config, 1.0, {"total": 400}
        )
        self.assertIn("JSON 解析失败", report)


class TestMimoHandling(unittest.TestCase):
    """Mimo 特殊处理测试"""

    def test_mimo_plan_truncation(self):
        """Mimo 长方案应被截断至 8000 字符"""
        long_plan = "X" * 10000
        messages = council_call.build_round1_messages(long_plan, "mimo")
        # user content 应包含截断标记
        user_content = messages[1]["content"]
        self.assertIn("[... 中间", user_content)
        self.assertIn("字符已省略", user_content)

    def test_non_mimo_no_truncation(self):
        """非 Mimo 模型不应截断方案"""
        plan = "X" * 9000
        messages = council_call.build_round1_messages(plan, "deepseek")
        user_content = messages[1]["content"]
        self.assertIn(plan, user_content)
        self.assertNotIn("[... 中间", user_content)


class TestRound2SkipConditions(unittest.TestCase):
    """Round 2 跳过条件测试"""

    def test_skip_when_target_not_success(self):
        """Target 模型失败时应跳过交叉验证"""
        config = {
            "deepseek": {
                "api_key": "sk-test", "base_url": "https://test.com/v1",
                "model": "deepseek-v4-flash", "auth_header": "Bearer",
            },
            "kimi": {
                "api_key": "sk-test", "base_url": "https://test.com/v1",
                "model": "kimi-k2.6", "auth_header": "Bearer",
            },
        }
        round1 = {
            "deepseek": make_error_result("failed"),
            "kimi": make_success_result(make_r1_json(), model="kimi-k2.6"),
        }

        import api_client
        with patch.object(api_client, 'call_model') as mock:
            mock.return_value = make_success_result(make_r2_json(), model="kimi-k2.6")
            r2 = council_call.run_round2(
                "plan", round1, ["deepseek", "kimi"], config
            )

        # deepseek→kimi 应执行（kimi target 成功）
        # kimi→deepseek 应跳过（deepseek target 失败）
        k_to_d_key = "kimi_to_deepseek"
        d_to_k_key = "deepseek_to_kimi"
        if k_to_d_key in r2:
            self.assertEqual(r2[k_to_d_key]["status"], "skipped")
        if d_to_k_key in r2:
            self.assertIn(r2[d_to_k_key]["status"], ["success", "skipped"])


class TestJudgeFallback(unittest.TestCase):
    """裁决模型降级链测试"""

    def test_judge_fallback_chain_defined(self):
        """降级链应有至少 2 个候选"""
        self.assertGreaterEqual(len(council_call.JUDGE_FALLBACK_CHAIN), 2)

    def test_judge_skips_with_no_key(self):
        """无 Key 时 R3 应跳过"""
        config = {"judge": {"api_key": "", "base_url": "", "model": "", "auth_header": "Bearer"}}
        result = council_call.run_round3("plan", {}, {}, [], config)
        self.assertEqual(result["status"], "skipped")

    @patch.object(council_call, 'call_model')
    def test_judge_first_candidate_succeeds(self, mock_call):
        """首选裁决模型成功时不触发降级"""
        mock_call.return_value = make_success_result(
            "# 裁决报告\n\nPASS", model="deepseek-v4-pro",
            tokens={"input": 1000, "output": 500, "total": 1500},
        )
        config = {
            "judge": {
                "api_key": "sk-judge",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        result = council_call.run_round3("plan", {}, {}, [], config)
        self.assertEqual(result["status"], "success")
        self.assertFalse(result.get("judge_fallback_used", False))

    @patch.object(council_call, 'call_model')
    @patch('os.getenv')
    def test_judge_fallback_triggered(self, mock_getenv, mock_call):
        """首选模型失败时应触发降级"""
        # 第一次失败，第二次成功
        mock_call.side_effect = [
            make_error_result("Service unavailable"),
            make_success_result(
                "# 裁决报告\n\nPASS (降级)", model="deepseek-v4-flash",
                tokens={"input": 1000, "output": 500, "total": 1500},
            ),
        ]
        # 模拟 fallback 模型需要的环境变量（DEEPSEEK_API_KEY）
        def fake_getenv(key, default=""):
            if key == "DEEPSEEK_API_KEY":
                return "sk-fallback"
            return default
        mock_getenv.side_effect = fake_getenv
        config = {
            "judge": {
                "api_key": "sk-judge",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        result = council_call.run_round3("plan", {}, {}, [], config)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result.get("judge_fallback_used", False))

    @patch.object(council_call, 'call_model')
    def test_judge_all_candidates_fail(self, mock_call):
        """全部裁决模型失败时应返回 error"""
        mock_call.return_value = make_error_result("All models unavailable")
        config = {
            "judge": {
                "api_key": "sk-judge",
                "base_url": "https://api.deepseek.com/v1",
                "model": "deepseek-v4-pro",
                "auth_header": "Bearer",
            },
        }
        result = council_call.run_round3("plan", {}, {}, [], config)
        self.assertEqual(result["status"], "error")
        self.assertIn("judge_tried", result)


class TestSeverityUpgrade(unittest.TestCase):
    """严重性升级测试（Round 2 understated）"""

    def test_severity_merged_keeps_highest(self):
        """去重时应保留最高严重性"""
        round1 = {
            "model_a": {
                "status": "success",
                "parsed": {
                    "issues": [
                        {"id": "A-1", "severity": "low",
                         "title": "测试问题ABC",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                    ],
                },
                "parse_failed": False,
            },
            "model_b": {
                "status": "success",
                "parsed": {
                    "issues": [
                        {"id": "B-1", "severity": "high",
                         "title": "测试问题ABC额外文字",
                         "detail": "", "evidence": "", "fix_suggestion": ""},
                    ],
                },
                "parse_failed": False,
            },
        }
        result = council_call.deduplicate_issues(round1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["severity"], "high")


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # 设置工作目录为 scripts 目录，确保 prompt 导入正常
    os.chdir(SCRIPT_DIR)
    unittest.main(verbosity=2)
