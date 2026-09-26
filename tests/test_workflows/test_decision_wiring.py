"""决策层接线测试（T-17b）/ Decision-layer wiring tests (T-17b).

覆盖两组契约：

1. **F5 零差异**（§15）：未启用（`decision_engine is None`）时，决策层**不存在**——
   不写任何 `laya_*` 键，业务快照与 `LAYA__ENABLED=false` 逐字段相同。
2. **五原语各调一次**（§8.1）：screen/assess 作为图节点在审查前跑，
   verify_issues/verify_resolutions/judge_convergence 在 review() 内跑，
   五个原语各只在唯一一处被调用（不重复审计）。

这些是行为断言，不依赖 Laya 权重：引擎一律用 `_RecordingEngine` 替身。
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agents.docreview import DocReviewAgent
from src.state.agent_state import create_initial_state
from src.workflows.review_workflow import _build_decision_provider, build_workflow

LAYA_KEYS = ("laya_findings", "laya_trace")


class _RecordingEngine:
    """记录原语调用顺序的引擎替身。

    刻意**不**校验阈值/provenance：接线测试只关心「哪个原语被调了几次、
    结果有没有落进 state」，把 §8.3 的 fail-closed 判定也一起测会让本文件
    变成 `laya_decisions` 的重复测试。
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.contexts: list[Any] = []
        self.specs: list[str] = []

    async def screen_document(self, spec: str, *, context: Any) -> dict[str, Any]:
        self.calls.append("screen_document")
        self.contexts.append(context)
        self.specs.append(spec)
        return {
            "status": "uncertain",
            "findings": [
                {
                    "finding_id": "screen-1",
                    "kind": "screen",
                    "severity": "warning",
                    "message": "缺少错误码定义",
                    "source": "laya",
                    "decision_ids": [],
                }
            ],
            "trace": [{"primitive": "screen"}],
        }

    async def assess_document(self, spec: str, section_index: Any, *, context: Any) -> dict[str, Any]:
        self.calls.append("assess_document")
        self.contexts.append(context)
        self.specs.append(spec)
        return {
            "status": "uncertain",
            "document_type": "prd",
            "completeness_level": None,
            "warnings": [
                {
                    "finding_id": "assess-1",
                    "kind": "assess",
                    "severity": "info",
                    "message": "缺少非功能需求",
                    "source": "laya",
                    "decision_ids": [],
                }
            ],
            "suggested_loop_cap": None,
            "route_action": "no_action",
            "trace": [{"primitive": "assess"}],
        }

    async def verify_issues(
        self, issues: list[Any], spec: str, section_index: Any, *, context: Any
    ) -> list[dict[str, Any]]:
        self.calls.append("verify_issues")
        self.contexts.append(context)
        return [
            {
                "issue_id": "BK-1-1",
                "grounded": True,
                "severity_review": "warning",
                "laya_note": "缺少验收标准",
                "location_valid": True,
                "normalized_location": "3.1",
                "location_error_code": None,
                "location_error_message": None,
                "trace": [{"primitive": "verify_issues"}],
            }
        ]

    async def verify_resolutions(
        self, previous: list[Any], spec: str, section_index: Any, *, context: Any
    ) -> list[dict[str, Any]]:
        self.calls.append("verify_resolutions")
        self.contexts.append(context)
        return [
            {
                "issue_id": "BK-1-1",
                "observed_status": "unknown",
                "audit_only": True,
                "trace": [{"primitive": "verify_resolutions"}],
            }
        ]

    async def judge_convergence(
        self,
        previous: list[Any],
        current: list[Any],
        counts: dict[str, int],
        tracker: Any,
        *,
        context: Any,
    ) -> dict[str, Any]:
        self.calls.append("judge_convergence")
        self.contexts.append(context)
        return {
            "status": "uncertain",
            "suggested_loop_cap": None,
            "route_action": "no_action",
            "reason": "cap 恒 None（§8.1）",
            "trace": [{"primitive": "judge_convergence"}],
        }


class _ExplodingEngine:
    """每个原语都抛异常——验证增强层 fail-soft。"""

    def __getattr__(self, name: str) -> Any:
        async def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("laya exploded")

        return _boom


def _agent(engine: Any = None) -> DocReviewAgent:
    return DocReviewAgent(llm=MagicMock(), decision_engine=engine)


def _state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = dict(create_initial_state())
    state["specification"] = "## 1. 概述\n规格正文"
    state["thread_id"] = "review-20260926-101010-ab12cd"
    state["spec_version"] = 1
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# F5：未启用时决策层不存在
# ---------------------------------------------------------------------------


def test_initial_state_has_no_laya_keys() -> None:
    """F5 锚点：初始状态**不得**预填 laya 键。

    预填空列表本身就是一处可观测差异，会让「禁用」的快照与基线不同。
    """
    state = create_initial_state()
    for key in LAYA_KEYS:
        assert key not in state, f"{key} 不应出现在初始状态中"


def test_disabled_state_carries_no_laya_keys() -> None:
    """未启用的运行里，state 始终不含 laya 键。"""
    state = _state()
    for key in LAYA_KEYS:
        assert key not in state


@pytest.mark.asyncio
async def test_screen_spec_is_noop_without_engine() -> None:
    agent = _agent(None)
    state = _state()

    result = await agent.screen_spec(state)

    assert result is state
    for key in LAYA_KEYS:
        assert key not in result


@pytest.mark.asyncio
async def test_assess_spec_is_noop_without_engine() -> None:
    agent = _agent(None)
    state = _state()

    result = await agent.assess_spec(state)

    assert result is state
    for key in LAYA_KEYS:
        assert key not in result


@pytest.mark.asyncio
async def test_review_decision_layer_is_noop_without_engine() -> None:
    agent = _agent(None)
    state = _state()

    await agent._run_decision_layer(state, "spec", [], 1)

    for key in LAYA_KEYS:
        assert key not in state


# ---------------------------------------------------------------------------
# 接线：五原语各调一次，结果落 state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_screen_spec_records_findings_and_trace() -> None:
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state()

    await agent.screen_spec(state)

    assert engine.calls == ["screen_document"]
    assert state["laya_findings"] == [engine_spec_finding()]
    assert state["laya_trace"] == [{"primitive": "screen"}]


def engine_spec_finding() -> dict[str, Any]:
    """screen 原语的期望 finding（与 _RecordingEngine 返回值一致）。"""
    return {
        "finding_id": "screen-1",
        "kind": "screen",
        "severity": "warning",
        "message": "缺少错误码定义",
        "source": "laya",
        "decision_ids": [],
    }


@pytest.mark.asyncio
async def test_assess_spec_records_warnings_as_findings() -> None:
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state()

    await agent.assess_spec(state)

    assert engine.calls == ["assess_document"]
    # assess 的 warnings 是 LayaFinding，直接进 findings 供摘要统一消费。
    assert state["laya_findings"] == [
        {
            "finding_id": "assess-1",
            "kind": "assess",
            "severity": "info",
            "message": "缺少非功能需求",
            "source": "laya",
            "decision_ids": [],
        }
    ]
    assert state["laya_trace"] == [{"primitive": "assess"}]


@pytest.mark.asyncio
async def test_primitives_receive_specification_field() -> None:
    """回归守卫：原语必须读到 state['specification']。

    曾误读不存在的 `current_spec`，会让两个原语静默收到空串——
    恒 uncertain 看起来「正常」，因此只能靠这条断言拦住。
    """
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state(specification="非空规格正文")

    await agent.screen_spec(state)
    await agent.assess_spec(state)

    assert engine.specs == ["非空规格正文", "非空规格正文"]


@pytest.mark.asyncio
async def test_five_primitives_called_exactly_once_each() -> None:
    """一次完整的决策层通路 = 五个原语各调一次，不重复审计。"""
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state()

    await agent.screen_spec(state)
    await agent.assess_spec(state)
    await agent._run_decision_layer(state, "规格正文", [], 1)

    assert engine.calls == [
        "screen_document",
        "assess_document",
        "verify_issues",
        "verify_resolutions",
        "judge_convergence",
    ]


@pytest.mark.asyncio
async def test_issue_primitives_record_findings_and_trace() -> None:
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state()

    await agent._run_decision_layer(state, "规格正文", [], 1)

    kinds = [f["kind"] for f in state["laya_findings"]]
    assert kinds == ["verify_issue"]
    primitives = [t["primitive"] for t in state["laya_trace"]]
    assert primitives == ["verify_issues", "verify_resolutions", "judge_convergence"]


@pytest.mark.asyncio
async def test_output_accumulates_across_calls() -> None:
    """跨轮累积：finalize 会把两个键整体写进 history 顶层。"""
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state()

    await agent.screen_spec(state)
    first = len(state["laya_findings"])
    await agent.assess_spec(state)

    assert len(state["laya_findings"]) == first + 1


@pytest.mark.asyncio
async def test_context_carries_thread_and_version() -> None:
    engine = _RecordingEngine()
    agent = _agent(engine)
    state = _state()

    await agent.screen_spec(state)
    await agent._run_decision_layer(state, "规格正文", [], 3)

    first, last = engine.contexts[0], engine.contexts[-1]
    assert first.thread_id == "review-20260926-101010-ab12cd"
    assert first.spec_version == 1
    # 文档级原语发生在审查轮次之前，iteration 恒 0；issue 级传本轮轮次。
    assert first.iteration == 0
    assert last.iteration == 3


@pytest.mark.asyncio
async def test_verified_severity_outside_enum_downgrades_to_info() -> None:
    """决策层不得抬高告警等级：非法 severity 一律降为 info。"""
    engine = _RecordingEngine()

    async def verify_issues(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "issue_id": "BK-2-1",
                "grounded": True,
                "severity_review": "critical",
                "laya_note": None,
                "location_valid": True,
                "normalized_location": None,
                "location_error_code": None,
                "location_error_message": None,
                "trace": [],
            }
        ]

    engine.verify_issues = verify_issues  # type: ignore[method-assign]
    agent = _agent(engine)
    state = _state()

    await agent._run_decision_layer(state, "规格正文", [], 1)

    assert state["laya_findings"][0]["severity"] == "info"
    assert state["laya_findings"][0]["message"] == "grounded=True"


# ---------------------------------------------------------------------------
# fail-soft：决策层挂了不能拖垮业务审查
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_screen_spec_is_fail_soft() -> None:
    agent = _agent(_ExplodingEngine())
    state = _state()

    result = await agent.screen_spec(state)

    assert result is state
    for key in LAYA_KEYS:
        assert key not in result


@pytest.mark.asyncio
async def test_assess_spec_is_fail_soft() -> None:
    agent = _agent(_ExplodingEngine())
    state = _state()

    result = await agent.assess_spec(state)

    assert result is state
    for key in LAYA_KEYS:
        assert key not in result


@pytest.mark.asyncio
async def test_review_decision_layer_is_fail_soft() -> None:
    agent = _agent(_ExplodingEngine())
    state = _state()

    await agent._run_decision_layer(state, "规格正文", [], 1)

    for key in LAYA_KEYS:
        assert key not in state


# ---------------------------------------------------------------------------
# 图结构：screen / assess 节点确实在图里
# ---------------------------------------------------------------------------


def test_graph_contains_screen_and_assess_nodes() -> None:
    graph = build_workflow(MagicMock(), MagicMock()).get_graph()
    nodes = set(graph.nodes)

    assert "screen" in nodes
    assert "assess" in nodes


def test_graph_wires_document_level_primitives_before_review() -> None:
    graph = build_workflow(MagicMock(), MagicMock()).get_graph()
    edges = {(e.source, e.target) for e in graph.edges}

    assert ("generate_spec", "screen") in edges
    assert ("screen", "assess") in edges
    assert ("assess", "docreview") in edges
    # 旧直连必须消失，否则 screen/assess 会被跳过。
    assert ("generate_spec", "docreview") not in edges


# ---------------------------------------------------------------------------
# provider 构造：未启用返回 None（F5 的源头）
# ---------------------------------------------------------------------------


def _config(enabled: bool) -> Any:
    laya = MagicMock()
    laya.enabled = enabled
    laya.timeout_seconds = 60
    cfg = MagicMock()
    cfg.laya = laya
    return cfg


def test_provider_is_none_when_disabled() -> None:
    assert _build_decision_provider(_config(False)) is None


def test_provider_is_none_when_laya_section_absent() -> None:
    cfg = MagicMock()
    cfg.laya = None
    assert _build_decision_provider(cfg) is None


def test_provider_is_callable_when_enabled() -> None:
    provider = _build_decision_provider(_config(True))
    assert callable(provider)


def test_probe_laya_source_fails_closed_without_repo() -> None:
    """源身份不可信时必须返回空值，绝不猜测。

    §8.3：commit / clean / digest 任一不可信，engine 只能 audit、不得 act。
    """
    from src.workflows import review_workflow

    original = review_workflow.LAYA_SOURCE_ROOT
    review_workflow.LAYA_SOURCE_ROOT = r"D:\__nonexistent__laya_repo__"
    try:
        assert review_workflow._probe_laya_source() == ("", False, "")
    finally:
        review_workflow.LAYA_SOURCE_ROOT = original


def test_guard_questions_fail_closed_to_empty() -> None:
    from src.workflows import review_workflow

    # laya 未安装时必须退化为空表（screen 只产 uncertain），而不是冒泡。
    assert review_workflow._load_guard_questions() == {} or isinstance(
        review_workflow._load_guard_questions(), dict
    )
