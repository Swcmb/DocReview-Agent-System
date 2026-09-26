"""决策层公共类型与 Null 引擎契约测试（规格 T-06）。

覆盖：五 public Protocol、DecisionContext、结果类型、DecisionAudit 字段、
Null 引擎的降级语义，以及最关键的**无 Laya import** 纪律。
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from src.decisions.null_decisions import NullDecisionEngine
from src.decisions.types import (
    DEGRADATION_DISABLED,
    DEGRADATION_UNAVAILABLE,
    LAYA_ERROR_CODES,
    ROUTE_ALLOWLIST,
    ConvergenceDecision,
    DecisionAudit,
    DecisionContext,
    DecisionEngine,
    GateDecision,
    IssueResolution,
    LayaFinding,
    ScreenResult,
    VerifiedIssue,
)
from src.schemas.models import IssueStatus, IssueTracker
from src.state.section_index import build_section_index

_SPEC = "# Alpha\n\nbody text\n\n## Beta\n\nmore body\n"
_ISSUE: IssueStatus = {
    "issue_id": "BK-1-1",
    "severity": "Blocking",
    "issue_type": "missing_requirement",
    "description": "缺少验收标准",
    "suggestion": "补充 AC",
    "location": "S1",
    "status": "open",
}


def _ctx() -> DecisionContext:
    return DecisionContext(thread_id="review-20260926-101500", iteration=2, spec_version=3)


def _tracker() -> IssueTracker:
    """真实构造 IssueTracker——judge_convergence 的契约要求 tracker 而非 None。"""
    return IssueTracker(
        all_issues=[dict(_ISSUE)],  # type: ignore[typeddict-item]
        fixed_count=0,
        partially_fixed_count=0,
        unfixed_count=1,
        new_in_current_round=["BK-1-1"],
    )


# ---------------------------------------------------------------------------
# 导入纪律：T-06 验收「无 Laya import」
# ---------------------------------------------------------------------------
def test_importing_decision_types_does_not_load_laya_or_torch() -> None:
    """导入决策层公共类型不得拉起 laya / torch / transformers。"""
    script = (
        "import sys;"
        "import src.decisions.types, src.decisions.null_decisions;"
        "leaked=[m for m in ('laya','torch','transformers') if m in sys.modules];"
        "assert not leaked, leaked;"
        "print('CLEAN')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "CLEAN" in proc.stdout


def test_null_engine_source_never_imports_laya() -> None:
    """Null 引擎源码里不得真的 import laya（防回归）。

    用 AST 判定而非字符串查找：模块 docstring 里以中文散文提到「不 import laya」，
    子串匹配会把它当成违规。这里只认真正的 import 语句。
    """
    import ast

    import src.decisions.null_decisions as module

    assert module.__file__ is not None
    with open(module.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert "laya" not in imported
    assert "torch" not in imported
    assert "transformers" not in imported


# ---------------------------------------------------------------------------
# DecisionContext
# ---------------------------------------------------------------------------
def test_decision_context_fields() -> None:
    """§4：thread_id / iteration / spec_version。"""
    context = _ctx()
    assert context.thread_id == "review-20260926-101500"
    assert context.iteration == 2
    assert context.spec_version == 3


def test_decision_context_is_frozen() -> None:
    """frozen：审计输入身份不可事后改写，否则 replay 不可复现。"""
    import dataclasses

    context = _ctx()
    with pytest.raises(dataclasses.FrozenInstanceError):
        context.iteration = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §4 五 public Protocol
# ---------------------------------------------------------------------------
def test_null_engine_satisfies_protocol() -> None:
    """NullDecisionEngine 必须实现同一协议。"""
    assert isinstance(NullDecisionEngine(), DecisionEngine)


def test_protocol_exposes_exactly_five_public_methods() -> None:
    """§4：五个方法是 v1 唯一公共业务入口。"""
    public = {
        name
        for name in vars(DecisionEngine)
        if not name.startswith("_")
    }
    assert public == {
        "screen_document",
        "assess_document",
        "verify_issues",
        "verify_resolutions",
        "judge_convergence",
    }


def test_all_public_methods_are_async_and_require_keyword_context() -> None:
    """五个方法全部 async，且 context 为 keyword-only——审计身份不能被漏传。"""
    import inspect

    for name in (
        "screen_document",
        "assess_document",
        "verify_issues",
        "verify_resolutions",
        "judge_convergence",
    ):
        method = getattr(DecisionEngine, name)
        assert inspect.iscoroutinefunction(method), f"{name} 必须是 async"
        signature = inspect.signature(method)
        assert (
            signature.parameters["context"].kind is inspect.Parameter.KEYWORD_ONLY
        ), f"{name} 的 context 必须是 keyword-only"


# ---------------------------------------------------------------------------
# Null 引擎降级语义
# ---------------------------------------------------------------------------
def test_unknown_degradation_reason_is_rejected() -> None:
    """降级原因只允许两个取值，否则降级码映射会失真。"""
    with pytest.raises(ValueError):
        NullDecisionEngine(reason="whatever")


@pytest.mark.parametrize(
    "reason,error_code",
    [
        (DEGRADATION_DISABLED, "LAYA_ERR_DISABLED"),
        (DEGRADATION_UNAVAILABLE, "LAYA_ERR_IMPORT"),
    ],
)
async def test_degradation_reason_maps_to_stable_error_code(
    reason: str, error_code: str
) -> None:
    """§8.3：disabled→LAYA_ERR_DISABLED，import/初始化失败→LAYA_ERR_IMPORT。"""
    engine = NullDecisionEngine(reason=reason)
    result = await engine.screen_document("doc", context=_ctx())
    audit = result["trace"][0]
    assert audit["degraded"] is True
    assert audit["degradation_reason"] == reason
    assert audit["error_code"] == error_code


async def test_screen_result_shape() -> None:
    """screen 只产告警；Null 无告警可产。"""
    result = await NullDecisionEngine().screen_document("doc", context=_ctx())
    assert isinstance(result, dict)
    assert set(result) == {"status", "findings", "trace"}
    assert result["status"] == "uncertain"
    assert result["findings"] == []
    assert result["trace"][0]["primitive"] == "screen"


async def test_assess_never_acts_and_never_suggests_cap() -> None:
    """§4.1/§5.1：assess 永远只产 annotation，score 不得让 status 变 act。"""
    index = build_section_index(_SPEC)
    gate = await NullDecisionEngine().assess_document("doc", index, context=_ctx())
    assert isinstance(gate, dict)
    assert gate["status"] != "act"
    assert gate["status"] == "uncertain"
    assert gate["suggested_loop_cap"] is None
    assert gate["route_action"] == "no_action"
    assert gate["document_type"] is None
    assert gate["completeness_level"] is None
    assert gate["warnings"] == []


async def test_assess_tolerates_missing_section_index() -> None:
    """无索引时 assess 仍须正常工作——它的四个问题都不依赖索引（§5.1）。"""
    gate = await NullDecisionEngine().assess_document("doc", None, context=_ctx())
    assert gate["status"] == "uncertain"


async def test_verify_issues_returns_annotation_per_issue() -> None:
    """只加标注：不删 issue、不改 severity。"""
    issues = [_ISSUE, {**_ISSUE, "issue_id": "HI-1-2"}]
    verified = await NullDecisionEngine().verify_issues(
        issues, _SPEC, build_section_index(_SPEC), context=_ctx()
    )
    assert [v["issue_id"] for v in verified] == ["BK-1-1", "HI-1-2"]
    for item in verified:
        assert item["grounded"] is None
        assert item["severity_review"] is None
        assert item["laya_note"] is None


async def test_verify_issues_trace_has_both_primitives() -> None:
    """§4.1：每个 VerifiedIssue.trace 必须同时含 verify_issues 与 review_executability。"""
    verified = await NullDecisionEngine().verify_issues(
        [_ISSUE], _SPEC, build_section_index(_SPEC), context=_ctx()
    )
    primitives = [audit["primitive"] for audit in verified[0]["trace"]]
    assert "verify_issues" in primitives
    assert "review_executability" in primitives
    assert len(primitives) == 2


async def test_verify_issues_location_is_computed_deterministically() -> None:
    """§6.4：location 有效性由 SectionIndex 计算，Null 不猜、也不因降级而放弃计算。"""
    index = build_section_index(_SPEC)
    engine = NullDecisionEngine()
    good = await engine.verify_issues([_ISSUE], _SPEC, index, context=_ctx())
    assert good[0]["location_valid"] is True
    assert good[0]["normalized_location"] == "S1"
    assert good[0]["location_error_code"] is None

    bad = await engine.verify_issues(
        [{**_ISSUE, "location": "S999"}], _SPEC, index, context=_ctx()
    )
    assert bad[0]["location_valid"] is False
    assert bad[0]["location_error_code"] == "LOC_UNKNOWN_SECTION"


async def test_verify_issues_reports_index_unavailable() -> None:
    """索引缺失时如实报 LOC_INDEX_UNAVAILABLE，不伪造位置。"""
    verified = await NullDecisionEngine().verify_issues(
        [_ISSUE], _SPEC, None, context=_ctx()
    )
    assert verified[0]["location_valid"] is False
    assert verified[0]["location_error_code"] == "LOC_INDEX_UNAVAILABLE"


async def test_verify_resolutions_is_audit_only() -> None:
    """§4.1：observed_status 恒 uncertain，audit_only 恒 True，且不改 IssueStatus。"""
    before = dict(_ISSUE)
    resolutions = await NullDecisionEngine().verify_resolutions(
        [_ISSUE], _SPEC, build_section_index(_SPEC), context=_ctx()
    )
    assert resolutions[0]["observed_status"] == "uncertain"
    assert resolutions[0]["audit_only"] is True
    # 原始 IssueStatus 不得被写回
    assert _ISSUE == before
    assert _ISSUE["status"] == "open"


async def test_judge_convergence_never_suggests_cap() -> None:
    """Null 不用降级状态污染循环控制流：cap=None、route_action=no_action。"""
    decision = await NullDecisionEngine().judge_convergence(
        [_ISSUE], [], {"Blocking": 1}, _tracker(), context=_ctx()
    )
    assert decision["status"] == "uncertain"
    assert decision["suggested_loop_cap"] is None
    assert decision["route_action"] == "no_action"
    assert DEGRADATION_DISABLED in decision["reason"]


async def test_every_result_is_uncertain_across_all_five_methods() -> None:
    """§4 总纲：所有可能动作的结果为 uncertain。"""
    engine = NullDecisionEngine()
    context = _ctx()
    index = build_section_index(_SPEC)
    statuses = [
        (await engine.screen_document("doc", context=context))["status"],
        (await engine.assess_document("doc", index, context=context))["status"],
        (await engine.judge_convergence([], [], {}, _tracker(), context=context))["status"],
    ]
    assert all(status == "uncertain" for status in statuses)
    resolutions = await engine.verify_resolutions([_ISSUE], _SPEC, index, context=context)
    assert all(r["observed_status"] == "uncertain" for r in resolutions)


async def test_null_never_writes_business_error_codes() -> None:
    """§8.3：Laya 降级不写 DOCREVIEW_ERR_*，只带 LAYA 降级码。"""
    engine = NullDecisionEngine()
    context = _ctx()
    index = build_section_index(_SPEC)
    traces = [
        (await engine.screen_document("doc", context=context))["trace"],
        (await engine.assess_document("doc", index, context=context))["trace"],
        (await engine.verify_issues([_ISSUE], _SPEC, index, context=context))[0]["trace"],
        (await engine.verify_resolutions([_ISSUE], _SPEC, index, context=context))[0]["trace"],
        (await engine.judge_convergence([], [], {}, _tracker(), context=context))["trace"],
    ]
    codes = [audit["error_code"] for group in traces for audit in group]
    assert codes, "应当至少产生一条 audit"
    for code in codes:
        assert code in LAYA_ERROR_CODES
        assert not code.startswith("DOCREVIEW_ERR_")


# ---------------------------------------------------------------------------
# §8.3 DecisionAudit
# ---------------------------------------------------------------------------
def test_decision_audit_has_all_spec_fields() -> None:
    """§8.3「最低字段」逐项对齐——缺字段会让离线重算无法定位。"""
    assert set(DecisionAudit.__annotations__) == {
        "call_id",
        "call_seq",
        "thread_id",
        "iteration",
        "spec_version",
        "primitive",
        "method",
        "question_id",
        "question_schema_hash",
        "state_digest",
        "state_ref",
        "spec_content_sha256",
        "chunk_id",
        "chunk_total",
        "chunk_selected",
        "chunk_omitted",
        "model",
        "checkpoint_id",
        "checkpoint_manifest_sha256",
        "laya_runtime_commit",
        "source_tree_clean",
        "runtime_source_digest",
        "requested_device",
        "actual_device",
        "route_allowlist",
        "route_reason",
        "raw_answer",
        "answer_confidence",
        "probabilities",
        "threshold_set",
        "calibration_id",
        "calibration_manifest_sha256",
        "status",
        "route_action",
        "value",
        "usage",
        "batch_plan",
        "degraded",
        "degradation_reason",
        "error_code",
        "created_at",
    }


def test_laya_error_codes_is_the_stable_set() -> None:
    """§8.3：11 个降级码的稳定集合。"""
    assert LAYA_ERROR_CODES == {
        "LAYA_ERR_DISABLED",
        "LAYA_ERR_IMPORT",
        "LAYA_ERR_RUNTIME_COMMIT",
        "LAYA_ERR_SOURCE",
        "LAYA_ERR_CHECKPOINT_MANIFEST",
        "LAYA_ERR_CALIBRATION",
        "LAYA_ERR_API_COMPAT",
        "LAYA_ERR_TIMEOUT",
        "LAYA_ERR_PARSE",
        "LAYA_ERR_BATCH",
        "LAYA_ERR_OOM",
    }


def test_route_allowlist_only_logical_names() -> None:
    """§8.3：allowlist 只能含逻辑名，禁止绝对路径/原文。"""
    assert ROUTE_ALLOWLIST == {"auto", "english", "multilingual", "typed-decisions"}
    for name in ROUTE_ALLOWLIST:
        assert "/" not in name and "\\" not in name


async def test_null_audit_route_allowlist_is_empty() -> None:
    """Null 不做路由决策，allowlist 必须为空。"""
    audit = (await NullDecisionEngine().screen_document("doc", context=_ctx()))["trace"][0]
    assert audit["route_allowlist"] == []


async def test_call_id_is_derived_and_deterministic() -> None:
    """§8.3：call_id 由 thread_id+iteration+primitive+question_id+call_seq 派生。"""
    engine = NullDecisionEngine()
    context = _ctx()
    first = (await engine.screen_document("doc", context=context))["trace"][0]
    second = (await engine.screen_document("doc", context=context))["trace"][0]
    assert first["call_id"] == second["call_id"]

    other = DecisionContext(thread_id="other", iteration=2, spec_version=3)
    third = (await engine.screen_document("doc", context=other))["trace"][0]
    assert third["call_id"] != first["call_id"]


async def test_distinct_primitives_yield_distinct_call_ids() -> None:
    """同 context 下不同 question_id 不得撞 call_id。"""
    engine = NullDecisionEngine()
    context = _ctx()
    index = build_section_index(_SPEC)
    verified = await engine.verify_issues([_ISSUE], _SPEC, index, context=context)
    ids = [audit["call_id"] for audit in verified[0]["trace"]]
    assert ids[0] != ids[1]


async def test_source_provenance_forces_audit_only() -> None:
    """§8.3：source_tree_clean=false 与缺失 runtime_source_digest 只能 degraded。

    把这两项填成看似正常的值会掩盖降级，因此 Null 必须如实留空。
    """
    audit = (await NullDecisionEngine().screen_document("doc", context=_ctx()))["trace"][0]
    assert audit["source_tree_clean"] is False
    assert audit["runtime_source_digest"] == ""
    assert audit["degraded"] is True


async def test_null_audit_does_not_fabricate_measurements() -> None:
    """Null 无信号：raw_answer / probabilities / threshold_set 等必须留空而非编造。"""
    audit = (await NullDecisionEngine().screen_document("doc", context=_ctx()))["trace"][0]
    assert audit["raw_answer"] == {}
    assert audit["probabilities"] == {}
    assert audit["threshold_set"] is None
    assert audit["answer_confidence"] is None
    assert audit["calibration_id"] is None
    assert audit["value"] is None
    assert audit["status"] == "uncertain"


async def test_audit_carries_context_identity() -> None:
    """每个 audit 必须能回溯到 thread/iteration/spec_version（§8.4）。"""
    context = _ctx()
    audit = (await NullDecisionEngine().screen_document("doc", context=context))["trace"][0]
    assert audit["thread_id"] == context.thread_id
    assert audit["iteration"] == context.iteration
    assert audit["spec_version"] == context.spec_version


async def test_state_digest_is_recorded_for_replay() -> None:
    """state_digest 是输入身份摘要，Null 也必须记录，否则 replay 无法定位输入。"""
    audit = (
        await NullDecisionEngine().screen_document("some content", context=_ctx())
    )["trace"][0]
    assert audit["state_digest"].startswith("sha256:")


async def test_spec_content_sha256_recorded_when_spec_supplied() -> None:
    """§8.4：audit 引用 spec_content_sha256 以便按 key 找回 snapshot。"""
    verified = await NullDecisionEngine().verify_issues(
        [_ISSUE], _SPEC, build_section_index(_SPEC), context=_ctx()
    )
    audit = verified[0]["trace"][0]
    assert audit["spec_content_sha256"] is not None
    assert audit["spec_content_sha256"].startswith("sha256:")


async def test_created_at_is_iso_timestamp() -> None:
    """created_at 需可排序的 ISO 时间戳。"""
    audit = (await NullDecisionEngine().screen_document("doc", context=_ctx()))["trace"][0]
    assert "T" in audit["created_at"]
    assert audit["created_at"].endswith("+00:00")


# ---------------------------------------------------------------------------
# 结果类型字段
# ---------------------------------------------------------------------------
def test_result_type_fields() -> None:
    """§4.1 各结果类型的字段集合。"""
    assert set(ScreenResult.__annotations__) == {"status", "findings", "trace"}
    assert set(GateDecision.__annotations__) == {
        "status",
        "document_type",
        "completeness_level",
        "warnings",
        "suggested_loop_cap",
        "route_action",
        "trace",
    }
    assert set(VerifiedIssue.__annotations__) == {
        "issue_id",
        "grounded",
        "severity_review",
        "laya_note",
        "location_valid",
        "normalized_location",
        "location_error_code",
        "location_error_message",
        "trace",
    }
    assert set(IssueResolution.__annotations__) == {
        "issue_id",
        "observed_status",
        "audit_only",
        "trace",
    }
    assert set(ConvergenceDecision.__annotations__) == {
        "status",
        "suggested_loop_cap",
        "route_action",
        "reason",
        "trace",
    }
    assert set(LayaFinding.__annotations__) == {
        "finding_id",
        "kind",
        "severity",
        "message",
        "source",
        "decision_ids",
    }
