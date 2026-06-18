"""pricing.py 单元测试"""

import json
import os

import pytest

from tools.tokenlens.pricing import PRICING, _load_pricing, calc_cost, get_price


class TestPricing:
    def test_all_models_have_required_fields(self):
        """所有模型定价包含 input/cache_read/output"""
        for model, prices in PRICING.items():
            assert "input" in prices, f"{model} 缺少 input"
            assert "cache_read" in prices, f"{model} 缺少 cache_read"
            assert "output" in prices, f"{model} 缺少 output"

    def test_get_known_price(self):
        """已知模型返回价格"""
        price = get_price("deepseek-v4-pro", "input")
        assert price == 3.00  # DeepSeek 官方人民币定价 ¥3/M

    def test_get_unknown_price(self):
        """未知模型返回 None"""
        price = get_price("nonexistent-model", "input")
        assert price is None

    def test_get_unknown_token_type(self):
        """未知 token 类型返回 None"""
        price = get_price("deepseek-v4-pro", "unknown_type")
        assert price is None

    def test_calc_cost_known_model(self):
        """计算已知模型费用"""
        cost = calc_cost("deepseek-v4-pro", 1_000_000, 0, 0)
        assert cost == pytest.approx(3.00)  # ¥3.00/M input

    def test_calc_cost_unknown_model(self):
        """未知模型费用为 None"""
        cost = calc_cost("unknown-model", 1_000_000, 0, 0)
        assert cost is None

    def test_calc_cost_with_cache(self):
        """含缓存读取的费用计算"""
        cost = calc_cost("deepseek-v4-pro", 1_000_000, 500_000, 100_000)
        # input: 1M * 3.00 = 3.00
        # cache_read: 0.5M * 0.025 = 0.0125
        # output: 0.1M * 6.00 = 0.60
        # total = 3.6125
        assert cost == pytest.approx(3.6125)

    def test_calc_cost_claude(self):
        """Claude 模型费用"""
        cost = calc_cost("claude-sonnet-4-20250514", 1_000_000, 0, 1_000_000)
        # input: 1M * 21.75 = 21.75
        # output: 1M * 108.75 = 108.75
        # total = 130.50
        assert cost == pytest.approx(130.50)


class TestPricingOverride:
    def setup_method(self):
        self._old_env = os.environ.get("TOKENLENS_PRICING_JSON", "")

    def teardown_method(self):
        if self._old_env:
            os.environ["TOKENLENS_PRICING_JSON"] = self._old_env
        elif "TOKENLENS_PRICING_JSON" in os.environ:
            del os.environ["TOKENLENS_PRICING_JSON"]

    def test_env_override_adds_new_model(self):
        """环境变量可添加新模型"""
        os.environ["TOKENLENS_PRICING_JSON"] = json.dumps({
            "new-model-v1": {"input": 5.0, "cache_read": 0.5, "output": 10.0},
        })
        pricing = _load_pricing()
        assert "new-model-v1" in pricing
        assert pricing["new-model-v1"]["input"] == 5.0

    def test_env_override_preserves_defaults(self):
        """环境变量覆盖不丢失默认模型"""
        os.environ["TOKENLENS_PRICING_JSON"] = json.dumps({
            "new-model-v1": {"input": 5.0, "cache_read": 0.5, "output": 10.0},
        })
        pricing = _load_pricing()
        assert "deepseek-v4-pro" in pricing

    def test_invalid_json_falls_back(self):
        """无效 JSON 回退到默认"""
        os.environ["TOKENLENS_PRICING_JSON"] = "not valid json"
        pricing = _load_pricing()
        assert "deepseek-v4-pro" in pricing

    def test_usd_to_rmb_default(self):
        """默认汇率为 7.25"""
        from tools.tokenlens.pricing import USD_TO_RMB
        assert USD_TO_RMB == 7.25
