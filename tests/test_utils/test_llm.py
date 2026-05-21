"""LLM 成本追踪测试模块 / LLM Cost Tracking Test Module"""

import pytest

from src.utils.llm import (
    CostTracker,
    LLM_PRICING,
    track_llm_cost,
    check_budget,
    _extract_tokens,
)


class TestCostTracker:
    """成本追踪器测试 / Cost Tracker Tests"""

    def test_cost_tracker_initialization(self):
        """测试成本追踪器初始化 / Test Cost Tracker Initialization"""
        tracker = CostTracker()
        assert tracker.total_cost == 0.0
        assert tracker.prompt_tokens == 0
        assert tracker.completion_tokens == 0
        assert tracker.request_count == 0

    def test_cost_tracker_to_dict(self, cost_tracker: CostTracker):
        """测试成本追踪器转字典 / Test Cost Tracker to Dict"""
        result = cost_tracker.to_dict()
        assert "total_cost" in result
        assert "prompt_tokens" in result
        assert "completion_tokens" in result
        assert "total_tokens" in result
        assert "request_count" in result

    def test_cost_tracker_reset(self, cost_tracker: CostTracker):
        """测试成本追踪器重置 / Test Cost Tracker Reset"""
        cost_tracker.total_cost = 1.0
        cost_tracker.prompt_tokens = 100
        cost_tracker.request_count = 5
        cost_tracker._request_history.append({"test": "data"})

        cost_tracker.reset()

        assert cost_tracker.total_cost == 0.0
        assert cost_tracker.prompt_tokens == 0
        assert cost_tracker.completion_tokens == 0
        assert cost_tracker.request_count == 0
        assert len(cost_tracker._request_history) == 0


class TestExtractTokens:
    """Token 提取测试 / Token Extraction Tests"""

    def test_extract_openai_format(self):
        """测试 OpenAI 格式提取 / Test OpenAI Format Extraction"""
        metadata = {
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        result = _extract_tokens(metadata)
        assert result == (100, 50)

    def test_extract_anthropic_format(self):
        """测试 Anthropic 格式提取 / Test Anthropic Format Extraction"""
        metadata = {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 100,
            }
        }
        result = _extract_tokens(metadata)
        assert result == (200, 100)

    def test_extract_missing_usage(self):
        """测试缺少 usage 信息 / Test Missing Usage Info"""
        metadata = {"content": "some text"}
        result = _extract_tokens(metadata)
        assert result is None

    def test_extract_empty_metadata(self):
        """测试空元数据 / Test Empty Metadata"""
        result = _extract_tokens({})
        assert result is None


class TestTrackLLMCost:
    """LLM 成本追踪测试 / LLM Cost Tracking Tests"""

    def test_track_openai_response(
        self,
        mock_openai_response_metadata: dict,
        cost_tracker: CostTracker
    ):
        """测试追踪 OpenAI 响应 / Test Tracking OpenAI Response"""
        model = "gpt-4o"
        cost = track_llm_cost(model, mock_openai_response_metadata, cost_tracker)

        expected_prompt_cost = (100 / 1_000_000) * LLM_PRICING["gpt-4o"][0]
        expected_completion_cost = (50 / 1_000_000) * LLM_PRICING["gpt-4o"][1]
        expected_cost = expected_prompt_cost + expected_completion_cost

        assert abs(cost - expected_cost) < 0.0001
        assert cost_tracker.total_cost == expected_cost
        assert cost_tracker.prompt_tokens == 100
        assert cost_tracker.completion_tokens == 50
        assert cost_tracker.request_count == 1

    def test_track_anthropic_response(
        self,
        mock_anthropic_response_metadata: dict,
        cost_tracker: CostTracker
    ):
        """测试追踪 Anthropic 响应 / Test Tracking Anthropic Response"""
        model = "claude-3-5-sonnet"
        cost = track_llm_cost(model, mock_anthropic_response_metadata, cost_tracker)

        expected_prompt_cost = (200 / 1_000_000) * LLM_PRICING["claude-3-5-sonnet"][0]
        expected_completion_cost = (100 / 1_000_000) * LLM_PRICING["claude-3-5-sonnet"][1]
        expected_cost = expected_prompt_cost + expected_completion_cost

        assert abs(cost - expected_cost) < 0.0001
        assert cost_tracker.total_cost == expected_cost
        assert cost_tracker.prompt_tokens == 200
        assert cost_tracker.completion_tokens == 100

    def test_track_without_tracker(self, mock_openai_response_metadata: dict):
        """测试不带追踪器 / Test Without Tracker"""
        cost = track_llm_cost("gpt-4o", mock_openai_response_metadata, None)
        assert cost > 0

    def test_track_unknown_model(
        self,
        mock_openai_response_metadata: dict,
        cost_tracker: CostTracker
    ):
        """测试未知模型（使用默认定价）/ Test Unknown Model (Default Pricing)"""
        cost = track_llm_cost("unknown-model", mock_openai_response_metadata, cost_tracker)
        default_pricing = LLM_PRICING.get("unknown-model", (2.50, 10.00))
        expected_prompt_cost = (100 / 1_000_000) * default_pricing[0]
        expected_completion_cost = (50 / 1_000_000) * default_pricing[1]
        expected_cost = expected_prompt_cost + expected_completion_cost
        assert abs(cost - expected_cost) < 0.0001

    def test_track_streaming_response_fallback(self, cost_tracker: CostTracker):
        """测试流式响应回退估算 / Test Streaming Response Fallback Estimation"""
        metadata = {"content": "x" * 1000}
        cost = track_llm_cost("gpt-4o", metadata, cost_tracker)
        assert cost > 0
        assert cost_tracker.prompt_tokens > 0
        assert cost_tracker.completion_tokens > 0

    def test_multiple_requests_accumulation(
        self,
        mock_openai_response_metadata: dict,
        cost_tracker: CostTracker
    ):
        """测试多次请求累积 / Test Multiple Request Accumulation"""
        track_llm_cost("gpt-4o", mock_openai_response_metadata, cost_tracker)
        track_llm_cost("gpt-4o", mock_openai_response_metadata, cost_tracker)

        assert cost_tracker.request_count == 2
        assert cost_tracker.prompt_tokens == 200
        assert cost_tracker.completion_tokens == 100

    def test_request_history_recorded(
        self,
        mock_openai_response_metadata: dict,
        cost_tracker: CostTracker
    ):
        """测试请求历史记录 / Test Request History Recording"""
        track_llm_cost("gpt-4o", mock_openai_response_metadata, cost_tracker)
        assert len(cost_tracker._request_history) == 1
        assert cost_tracker._request_history[0]["model"] == "gpt-4o"


class TestCheckBudget:
    """预算检查测试 / Budget Check Tests"""

    def test_check_budget_under_limit(
        self,
        cost_tracker: CostTracker
    ):
        """测试预算内 / Test Under Budget"""
        cost_tracker.total_cost = 0.5
        over_budget, message = check_budget(cost_tracker, 1.0)
        assert over_budget is False
        assert message == ""

    def test_check_budget_over_limit(
        self,
        cost_tracker: CostTracker
    ):
        """测试超出预算 / Test Over Budget"""
        cost_tracker.total_cost = 1.5
        over_budget, message = check_budget(cost_tracker, 1.0)
        assert over_budget is True
        assert "超出预算" in message

    def test_check_budget_no_limit(
        self,
        cost_tracker: CostTracker
    ):
        """测试无限制 / Test No Limit"""
        cost_tracker.total_cost = 100.0
        over_budget, message = check_budget(cost_tracker, 0)
        assert over_budget is False

        over_budget, message = check_budget(cost_tracker, -1)
        assert over_budget is False

    def test_check_budget_equal_to_limit(
        self,
        cost_tracker: CostTracker
    ):
        """测试恰好等于限制 / Test Equal to Limit"""
        cost_tracker.total_cost = 1.0
        over_budget, message = check_budget(cost_tracker, 1.0)
        assert over_budget is False


class TestLLMPricing:
    """LLM 定价测试 / LLM Pricing Tests"""

    def test_pricing_contains_common_models(self):
        """测试定价表包含常用模型 / Test Pricing Table Contains Common Models"""
        assert "gpt-4o" in LLM_PRICING
        assert "gpt-4o-mini" in LLM_PRICING
        assert "claude-3-5-sonnet" in LLM_PRICING

    def test_pricing_format(self):
        """测试定价格式 / Test Pricing Format"""
        for model, (prompt_price, completion_price) in LLM_PRICING.items():
            assert prompt_price > 0
            assert completion_price > 0
            assert isinstance(prompt_price, float)
            assert isinstance(completion_price, float)
