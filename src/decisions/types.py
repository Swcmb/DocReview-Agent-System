"""决策层公共类型（规格 §4、§8.3）。

本模块是 v1 **唯一**的公共业务契约面：五个 Protocol 方法、`DecisionContext`、
全部结果类型、`DecisionAudit` 与 Laya 降级码。

**导入纪律**：本模块不得 import laya / torch / transformers。它被工作流节点直接
依赖，必须在任何 Laya 运行时准备之前就可导入（同 T-03 的导入边界）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict, runtime_checkable

from src.schemas.models import IssueStatus, IssueTracker
from src.state.section_index import SectionIndex

__all__ = [
    # Literals
    "DecisionStatus",
    "ResolutionObservation",
    "LayaRouteAction",
    "WorkflowRoute",
    "FindingSeverity",
    # context / protocol
    "DecisionContext",
    "DecisionEngine",
    # 结果类型
    "LayaFinding",
    "ScreenResult",
    "GateDecision",
    "VerifiedIssue",
    "IssueResolution",
    "ConvergenceDecision",
    "DecisionAudit",
    # 降级码
    "LAYA_ERROR_CODES",
    "DEGRADATION_DISABLED",
    "DEGRADATION_UNAVAILABLE",
    "ROUTE_ALLOWLIST",
]


# ---------------------------------------------------------------------------
# §4.1 枚举字面量
# ---------------------------------------------------------------------------
DecisionStatus = Literal["act", "pass", "uncertain"]
ResolutionObservation = Literal["fixed", "partially_fixed", "unfixed", "outdated", "uncertain"]
LayaRouteAction = Literal["no_action", "suggest_cap"]
WorkflowRoute = Literal["execute", "user_approval", "revise_spec", "finalize"]
FindingSeverity = Literal["info", "warning", "high"]


# ---------------------------------------------------------------------------
# §4 决策上下文
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DecisionContext:
    """一次决策调用的身份上下文。

    frozen：审计记录以它为输入身份，事后被改写会破坏 replay 的可复现性
    （§8.4「每个 audit 必须引用 spec_version 和 spec_content_sha256」）。
    """

    thread_id: str
    iteration: int
    spec_version: int


# ---------------------------------------------------------------------------
# §4 结果类型
# ---------------------------------------------------------------------------
class LayaFinding(TypedDict):
    """一条诊断告警。guard 原语只产生它，不拒绝、不改文。"""

    finding_id: str
    kind: str
    severity: FindingSeverity
    message: str
    source: str
    decision_ids: list[str]


class ScreenResult(TypedDict):
    status: DecisionStatus
    findings: list[LayaFinding]
    trace: list[DecisionAudit]


class GateDecision(TypedDict):
    """assess 的结果。

    §4.1：`status` **不得**因 score 变为 `act`；没有其他有效 choice/noul 依据时
    必须 `uncertain`。score 的 argmax 只能记进 `completeness_level` 供审计。
    """

    status: DecisionStatus
    document_type: str | None
    completeness_level: int | None
    warnings: list[LayaFinding]
    suggested_loop_cap: int | None
    route_action: LayaRouteAction
    trace: list[DecisionAudit]


class VerifiedIssue(TypedDict):
    """verify_issues 的逐 issue 标注。

    §4.1：`trace` 必须同时包含 `verify_issues` 与内部 `review_executability`
    两条原语记录。location 相关字段由 SectionIndex 确定性计算，Laya 不回答
    「这个 location 有效吗」（§6.4）。
    """

    issue_id: str
    grounded: bool | None
    severity_review: str | None
    laya_note: str | None
    location_valid: bool
    normalized_location: str | None
    location_error_code: str | None
    location_error_message: str | None
    trace: list[DecisionAudit]


class IssueResolution(TypedDict):
    """verify_resolutions 的结果。

    §4.1：`observed_status` **永远不**写回 `IssueStatus.status`——只审计。
    `audit_only` 恒为 True，是这条纪律在类型层的硬约束。
    """

    issue_id: str
    observed_status: ResolutionObservation
    audit_only: Literal[True]
    trace: list[DecisionAudit]


class ConvergenceDecision(TypedDict):
    status: DecisionStatus
    suggested_loop_cap: int | None
    route_action: LayaRouteAction
    reason: str
    trace: list[DecisionAudit]


# ---------------------------------------------------------------------------
# §8.3 DecisionAudit
# ---------------------------------------------------------------------------
class DecisionAudit(TypedDict):
    """每条 raw answer 一条审计，不可约化为 pass/fail（§8.3）。

    字段分三组：身份（call_*/thread/iteration/spec_version/question_*）、
    输入摘要（state_digest/state_ref/spec_content_sha256/chunk_*）、
    运行时与来源（model/checkpoint_*/laya_runtime_commit/source_tree_clean/
    runtime_source_digest/requested_device/actual_device/route_*）、
    答案与计量（raw_answer/answer_confidence/probabilities/threshold_set/
    calibration_*/status/route_action/value/usage/batch_plan）、
    降级（degraded/degradation_reason/error_code）与时间（created_at）。

    `route_allowlist` 只能含逻辑名，不得含绝对路径、原始 state 片段或文件内容。
    """

    call_id: str
    call_seq: int
    thread_id: str
    iteration: int
    spec_version: int
    primitive: str
    method: str
    question_id: str
    question_schema_hash: str
    state_digest: str
    state_ref: dict[str, str] | None
    spec_content_sha256: str | None
    chunk_id: str | None
    chunk_total: int
    chunk_selected: int
    chunk_omitted: int
    model: str | None
    checkpoint_id: str | None
    checkpoint_manifest_sha256: str | None
    laya_runtime_commit: str
    source_tree_clean: bool
    runtime_source_digest: str
    requested_device: str
    actual_device: str
    route_allowlist: list[str]
    route_reason: str
    raw_answer: dict[str, Any]
    answer_confidence: float | None
    probabilities: dict[str, float]
    threshold_set: dict[str, float] | None
    calibration_id: str | None
    calibration_manifest_sha256: str | None
    status: DecisionStatus
    route_action: LayaRouteAction
    value: Any
    usage: dict[str, int | float | None]
    batch_plan: dict[str, Any]
    degraded: bool
    degradation_reason: str | None
    error_code: str | None
    created_at: str


# ---------------------------------------------------------------------------
# §8.3 Laya 降级码
# ---------------------------------------------------------------------------
#: Laya 降级码的稳定集合。它们**不**替代 `DOCREVIEW_ERR_*` 业务/系统码
#: （§8.3：Laya 降级本身不写 `state["error_code"]`）。
LAYA_ERROR_CODES: frozenset[str] = frozenset(
    {
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
)

#: §4 Null 引擎的两种降级原因。
DEGRADATION_DISABLED = "laya_disabled"
DEGRADATION_UNAVAILABLE = "laya_unavailable"

#: §8.3 `route_allowlist` 允许的逻辑名——只允许逻辑名，禁止绝对路径/原文。
ROUTE_ALLOWLIST: frozenset[str] = frozenset({"auto", "english", "multilingual", "typed-decisions"})


# ---------------------------------------------------------------------------
# §4 DecisionEngine
# ---------------------------------------------------------------------------
@runtime_checkable
class DecisionEngine(Protocol):
    """v1 唯一公共业务入口（§4）。

    五个方法全部 async，且都要求 keyword-only 的 `context`——审计身份不能被
    位置参数顺带传入而漏掉。
    """

    async def screen_document(self, content: str, *, context: DecisionContext) -> ScreenResult:
        """guard 原语：只产生告警，不拒绝、不改文。"""
        ...

    async def assess_document(
        self,
        content: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> GateDecision:
        """永远只产 annotation；`suggested_loop_cap=None`、`route_action=no_action`。"""
        ...

    async def verify_issues(
        self,
        issues: list[IssueStatus],
        spec: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> list[VerifiedIssue]:
        """只加标注；不删 issue、不改 severity。"""
        ...

    async def verify_resolutions(
        self,
        previous_issues: list[IssueStatus],
        spec: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> list[IssueResolution]:
        """只审计；不改状态。"""
        ...

    async def judge_convergence(
        self,
        previous_issues: list[IssueStatus],
        current_issues: list[IssueStatus],
        counts: dict[str, int],
        tracker: IssueTracker,
        *,
        context: DecisionContext,
    ) -> ConvergenceDecision:
        """choice/noul 可建议循环；score 只审计。"""
        ...
