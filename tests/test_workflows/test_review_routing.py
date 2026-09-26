"""T-15：路由真值表、issue 指纹唯一实现（规格 §10.1／§13.2）。

两条主线：

1. ``has_unresolved_blocking()`` 的**唯一真值表**——只读 state，只看当前轮与
   紧邻上一轮，更早轮次一律忽略。
2. ``issue_fingerprint`` 是**唯一**内容指纹实现，``review_workflow`` 不再内联
   计算；指纹只由稳定字段投影决定，与轮次／措辞标点无关。
"""

from typing import Any, cast

import pytest

from src.schemas.models import AgentState
from src.state.issue_fingerprint import (
    FINGERPRINT_FIELDS,
    fingerprint_set,
    issue_fingerprint,
    issue_projection,
    normalize_description,
)
from src.workflows.review_routing import (
    has_unresolved_blocking,
    route_after_generate_spec,
    route_after_load_document,
    route_after_revise_spec,
)

# ─────────────────────────── 测试替身 ───────────────────────────


def _issue(
    severity: str = "Medium",
    issue_type: str = "ConsistencyCheck",
    description: str = "描述",
    status: str | None = "open",
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "issue_id": "",
        "severity": severity,
        "issue_type": issue_type,
        "description": description,
        "suggestion": "s",
        "location": "l",
    }
    if status is not None:
        issue["status"] = status
    return issue


def _report(*issues: dict[str, Any]) -> dict[str, Any]:
    return {"issues": list(issues)}


def _state(*reports: dict[str, Any]) -> AgentState:
    return cast("AgentState", {"review_reports": list(reports)})


def _blocking(issue_id: str = "BK-1-1", **kw: Any) -> dict[str, Any]:
    kw.setdefault("status", "open")
    return _issue(severity="Blocking", description=issue_id, **kw)


# ────────────── 1. has_unresolved_blocking 真值表（§10.1） ──────────────


def test_current_round_blocking_is_true():
    """当前轮有 open Blocking/High → True（上一轮任意）。"""
    assert has_unresolved_blocking(_state(_report(_blocking()), _report())) is True


def test_current_round_high_counts_as_blocking():
    """High 与 Blocking 同等构成阻塞。"""
    high = _issue(severity="High", description="high-1")
    assert has_unresolved_blocking(_state(_report(high))) is True


def test_current_clean_previous_blocking_is_true():
    """当前轮干净、紧邻上一轮有 unresolved Blocking/High → True。"""
    state = _state(_report(), _report(_blocking("prev-1")))
    assert has_unresolved_blocking(state) is True


def test_earlier_rounds_are_ignored():
    """当前轮与上一轮都干净时，**更早轮次**的 Blocking 不阻塞。"""
    state = _state(_report(_blocking("ancient-1")), _report(), _report())
    assert has_unresolved_blocking(state) is False


def test_clean_reports_grace_is_false():
    """两轮都无 Blocking/High → False（clean report grace）。"""
    low = _issue(severity="Low", description="low-1")
    state = _state(_report(low), _report(low))
    assert has_unresolved_blocking(state) is False


def test_empty_state_and_empty_reports():
    """空 state / 空报告都不构成阻塞。"""
    assert has_unresolved_blocking(cast("AgentState", {})) is False
    assert has_unresolved_blocking(_state()) is False
    assert has_unresolved_blocking(_state(_report(), _report())) is False


def test_single_report_has_no_previous():
    """只有一轮报告时没有「上一轮」，只看当前轮。"""
    assert has_unresolved_blocking(_state(_report(_blocking()))) is True
    assert has_unresolved_blocking(_state(_report(_issue()))) is False


@pytest.mark.parametrize("status", ["open", "partially_fixed", "unfixed", "MISSING"])
def test_conservative_unresolved_statuses(status: str):
    """§10.1 保守定义：只有 ``fixed``/``outdated`` 算已解决。

    ``MISSING`` 表示 dict 里根本没有 ``status`` 键——同样按未解决处理。
    """
    issue = _blocking(status=None if status == "MISSING" else status)
    assert has_unresolved_blocking(_state(_report(issue))) is True


@pytest.mark.parametrize("status", ["fixed", "outdated"])
def test_resolved_statuses_do_not_block(status: str):
    """``fixed`` / ``outdated`` 是唯一不算阻塞的 status。"""
    issue = _blocking(status=status)
    assert has_unresolved_blocking(_state(_report(issue))) is False


def test_does_not_mutate_state():
    """只读函数：不得修改传入的 state。"""
    state = _state(_report(_blocking()))
    before = repr(state)
    has_unresolved_blocking(state)
    assert repr(state) == before


# ────────────── 2. 新增路径函数（§10.1 approval/legacy 守卫） ──────────────


def test_route_after_load_document_success_and_failure():
    state = cast("AgentState", {})
    assert route_after_load_document(state) == "generate_spec"

    failed = cast("AgentState", {"error_code": "DOCREVIEW_ERR_DOC_001"})
    assert route_after_load_document(failed) == "finalize"


def test_route_after_generate_spec_success_and_failure():
    assert route_after_generate_spec(cast("AgentState", {})) == "docreview"

    failed = cast("AgentState", {"error_code": "DOCREVIEW_ERR_GEN_001"})
    assert route_after_generate_spec(failed) == "finalize"


def test_route_after_revise_spec_success_and_failure():
    assert route_after_revise_spec(cast("AgentState", {})) == "docreview"

    failed = cast("AgentState", {"error_code": "DOCREVIEW_ERR_REV_001"})
    assert route_after_revise_spec(failed) == "finalize"


def test_non_fatal_error_code_does_not_short_circuit():
    """非生成/修订/加载类的错误码不应误判为致命。"""
    state = cast("AgentState", {"error_code": "DOCREVIEW_ERR_SYS_001"})
    assert route_after_generate_spec(state) == "docreview"
    assert route_after_revise_spec(state) == "docreview"


# ────────────── 3. issue 指纹：唯一实现与稳定投影 ──────────────


def test_fingerprint_ignores_round_and_issue_id():
    """``issue_id`` 内嵌轮次号，跨轮必然不同，不能参与身份。"""
    a = _issue(description="缺少验收标准")
    a["issue_id"] = "BK-1-1"
    b = _issue(description="缺少验收标准")
    b["issue_id"] = "BK-9-7"

    assert issue_fingerprint(a) == issue_fingerprint(b)


def test_fingerprint_normalizes_punctuation_and_case():
    """同义改写（标点/空白/大小写）仍判定为同一问题。"""
    a = _issue(description="缺少 AC。")
    b = _issue(description=" 缺少ac  ")

    assert issue_fingerprint(a) == issue_fingerprint(b)


def test_fingerprint_separates_severity_and_type():
    """severity / issue_type 是问题性质的一部分，必须区分。"""
    base = _issue(description="同一个描述")
    hi = _issue(severity="High", description="同一个描述")
    other_type = _issue(issue_type="FeasibilityCheck", description="同一个描述")

    assert issue_fingerprint(base) != issue_fingerprint(hi)
    assert issue_fingerprint(base) != issue_fingerprint(other_type)


def test_projection_excludes_volatile_fields():
    """投影只含三个稳定字段，排除 status/suggestion/location。"""
    issue = _issue(description="内容")
    issue["status"] = "fixed"
    issue["suggestion"] = "改这里"
    issue["location"] = "第 9 页"

    assert set(issue_projection(issue)) == set(FINGERPRINT_FIELDS)
    assert "status" not in issue_projection(issue)


def test_projection_is_status_insensitive():
    """status 变化（open→fixed）不得改变内容指纹。"""
    a = _issue(description="内容", status="open")
    b = _issue(description="内容", status="fixed")
    assert issue_fingerprint(a) == issue_fingerprint(b)


def test_fingerprint_set_dedupes_identical_content():
    """重复内容在集合中折叠为一个元素。"""
    issues = [_issue(description="重复"), _issue(description="重复"), _issue(description="不同")]
    assert len(fingerprint_set(issues)) == 2


def test_normalize_description_handles_non_string():
    """非字符串 description 退化为空串而不是抛错。"""
    assert normalize_description(None) == ""
    assert normalize_description(123) == ""
    assert normalize_description("  A_B-C  ") == "abc"


def test_fingerprint_is_sha256_prefixed():
    """canonical 摘要带 ``sha256:`` 前缀，与项目其他 digest 一致。"""
    digest = issue_fingerprint(_issue())
    assert digest.startswith("sha256:")
    assert len(digest) == len("sha256:") + 64


def test_review_workflow_does_not_compute_fingerprint_inline():
    """§13.2 验收：指纹是唯一实现，``review_workflow`` 不得内联计算。"""
    import inspect

    from src.workflows import review_workflow

    source = inspect.getsource(review_workflow)
    assert "_issue_fingerprint" not in source
    assert "hashlib" not in source
    # 停滞检测必须走唯一实现。
    assert "fingerprint_set" in source
