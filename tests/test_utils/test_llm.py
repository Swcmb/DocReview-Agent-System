"""LLM 成本追踪测试模块 / LLM Cost Tracking Test Module"""

import pytest

from src.utils.llm import (
    CostTracker,
    LLM_PRICING,
    check_budget,
    invoke_with_cost,
    resolve_cost_model,
    track_llm_cost,
    _extract_tokens,
)


async def _acoro(value):
    """把同步值包成 awaitable，模拟 `invoke_with_cost` 的 `invoke` 契约。"""
    return value


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


class _FakeGeneration:
    """最小 Generation 替身：需要 .text，以及可挂 usage 的 .message。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.message: _FakeMessage | None = None


class _FakeMessage:
    """最小 message 替身：承载 LangChain 新版 usage_metadata。"""

    def __init__(self, usage: dict | None = None) -> None:
        self.usage_metadata = usage


class _FakeResponse:
    """最小 LLM 响应替身，形状对齐 LangChain 的 generations[0][0]。"""

    def __init__(self, text: str, llm_output: dict | None = None, usage: dict | None = None):
        self.generations = [[_FakeGeneration(text)]]
        self.llm_output = llm_output
        message = _FakeMessage(usage)
        self.generations[0][0].message = message


class TestInvokeWithCost:
    """`invoke_with_cost` 统一调用契约（§11.3）。"""

    async def test_returns_response_and_cost(self):
        """返回 (response, 本次美元成本)，响应原样透传。"""
        tracker = CostTracker()
        response = _FakeResponse("hi", llm_output={"token_usage": {"prompt_tokens": 1000, "completion_tokens": 500}})

        returned, cost = await invoke_with_cost(
            lambda: _acoro(response),
            tracker=tracker,
            model="gpt-4o",
        )

        assert returned is response
        assert cost > 0
        assert tracker.total_cost == cost

    async def test_openai_metadata_is_read(self):
        """OpenAI 风格：llm_output.token_usage(prompt_tokens/completion_tokens)。"""
        tracker = CostTracker()
        response = _FakeResponse("x", llm_output={"token_usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}})

        _, cost = await invoke_with_cost(lambda: _acoro(response), tracker=tracker, model="gpt-4o")

        # 1M prompt tokens @ gpt-4o 的 $2.50/M
        assert cost == pytest.approx(2.50)

    async def test_anthropic_metadata_is_read(self):
        """Anthropic 风格：llm_output.usage(input_tokens/output_tokens)。"""
        tracker = CostTracker()
        response = _FakeResponse("x", llm_output={"usage": {"input_tokens": 0, "output_tokens": 1_000_000}})

        _, cost = await invoke_with_cost(
            lambda: _acoro(response), tracker=tracker, model="claude-3-5-sonnet"
        )

        # 1M completion tokens @ claude-3-5-sonnet 的 $15.00/M
        assert cost == pytest.approx(15.00)

    async def test_usage_metadata_fallback_path(self):
        """LangChain 新版：usage_metadata 挂在 message 上。"""
        tracker = CostTracker()
        response = _FakeResponse("x", usage={"input_tokens": 1_000_000, "output_tokens": 0})

        _, cost = await invoke_with_cost(lambda: _acoro(response), tracker=tracker, model="gpt-4o")

        assert cost == pytest.approx(2.50)

    async def test_no_usage_uses_existing_estimate_fallback(self):
        """无 usage 时走既有「按内容长度估算」fallback，而不是记 0 成本。

        记 0 会让预算闸门形同虚设——这是本测试存在的唯一理由。
        """
        tracker = CostTracker()
        response = _FakeResponse("x" * 4000)

        _, cost = await invoke_with_cost(lambda: _acoro(response), tracker=tracker, model="gpt-4o")

        assert cost > 0, "无 usage 时必须按内容长度估算，不能静默记 0"
        assert tracker.request_count == 1

    async def test_plain_string_response_is_tolerated(self):
        """响应是纯字符串时不得抛异常。"""
        tracker = CostTracker()

        returned, cost = await invoke_with_cost(
            lambda: _acoro("纯文本响应"), tracker=tracker, model="gpt-4o"
        )

        assert returned == "纯文本响应"
        assert cost >= 0

    async def test_tracker_accumulates_across_calls(self):
        """同一局部 tracker 跨多次调用累计（review 的六步共用一个）。"""
        tracker = CostTracker()
        response = _FakeResponse("x", llm_output={"token_usage": {"prompt_tokens": 1000, "completion_tokens": 1000}})

        for _ in range(3):
            await invoke_with_cost(lambda: _acoro(response), tracker=tracker, model="gpt-4o")

        assert tracker.request_count == 3
        assert tracker.total_cost > 0

    async def test_exception_propagates_without_recording_cost(self):
        """调用失败时异常上抛，且不记账——没拿到 usage 就不该有成本条目。"""
        tracker = CostTracker()

        async def _boom():
            raise RuntimeError("provider 挂了")

        with pytest.raises(RuntimeError):
            await invoke_with_cost(_boom, tracker=tracker, model="gpt-4o")

        assert tracker.request_count == 0
        assert tracker.total_cost == 0.0


class TestResolveCostModel:
    """定价表键名必须来自同一处解析。"""

    def test_resolve_cost_model_returns_string(self):
        assert isinstance(resolve_cost_model(), str)

    def test_resolve_cost_model_is_in_pricing_table(self):
        """默认模型名必须命中 LLM_PRICING，否则会静默落到兜底价。"""
        assert resolve_cost_model() in LLM_PRICING
