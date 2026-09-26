"""NullDecisionEngine：Laya 缺席时的降级实现（规格 §4）。

契约（§4 原文）：「实现同一协议：不导入 Laya、不启动进程、不写业务错误码；
所有可能动作的结果为 `uncertain`，诊断 trace 标明 `degraded=true`、
`degradation_reason="laya_disabled"` 或 `laya_unavailable`。」

三条纪律各自对应一个可被测试抓住的失败模式：

1. **不 import laya** —— 未启用 Laya 的部署不应被迫加载深度学习栈；加载权重
   还有触发下载的副作用。
2. **不写业务错误码** —— Laya 降级不是业务失败。Null 引擎**不**触碰
   ``state["error_code"]``，因此不会写出 `DOCREVIEW_ERR_*`（§8.3）。它只在
   自己的 audit 里带 Laya 降级码。
3. **一切可能动作都是 `uncertain`** —— Null 没有信号，绝不猜。`assess` 尤其
   不得因 score 变成 `act`（§4.1）。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Final

from src.decisions.types import (
    DEGRADATION_DISABLED,
    DEGRADATION_UNAVAILABLE,
    ConvergenceDecision,
    DecisionAudit,
    DecisionContext,
    DecisionEngine,
    GateDecision,
    IssueResolution,
    ScreenResult,
    VerifiedIssue,
)
from src.schemas.models import IssueStatus, IssueTracker
from src.state.section_index import SectionIndex, normalize_location

__all__ = ["NullDecisionEngine"]

_REASON_TO_ERROR_CODE: Final[dict[str, str]] = {
    DEGRADATION_DISABLED: "LAYA_ERR_DISABLED",
    DEGRADATION_UNAVAILABLE: "LAYA_ERR_IMPORT",
}

#: verify_issues 内部子原语（§5.1）：只加执行性/表面问题标注，不增加公共方法。
_REVIEW_EXECUTABILITY: Final = "review_executability"


def _sha256(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class NullDecisionEngine:
    """满足 `DecisionEngine` 协议的无 Laya 实现。

    Args:
        reason: 降级原因，取 `DEGRADATION_DISABLED`（配置未启用）或
            `DEGRADATION_UNAVAILABLE`（已启用但导入/初始化失败）。二者对应
            §8.3 的不同降级码，因此不能混用。
    """

    def __init__(
        self, reason: str = DEGRADATION_DISABLED
    ) -> None:
        if reason not in _REASON_TO_ERROR_CODE:
            raise ValueError(
                f"未知的降级原因 {reason!r}；只允许 {sorted(_REASON_TO_ERROR_CODE)}"
            )
        self._reason = reason

    @property
    def reason(self) -> str:
        return self._reason

    # -- 审计构造 ---------------------------------------------------------
    def _audit(
        self,
        *,
        context: DecisionContext,
        primitive: str,
        method: str,
        question_id: str,
        state_digest: str,
        spec: str | None = None,
        call_seq: int = 0,
    ) -> DecisionAudit:
        """构造一条 Null 路径的 `DecisionAudit`。

        Laya 相关字段一律留空/None 而不是编造：`source_tree_clean=False` 与缺失的
        `runtime_source_digest` 本身就构成「只能 audit/degraded，不得 act」的判据
        （§8.3），把这两项填成看似正常的值反而会掩盖降级。
        """
        # call_id 由 thread_id + iteration + primitive + question_id + call_seq
        # 的 canonical hash 派生（§8.3）。
        identity = "\x1f".join(
            [context.thread_id, str(context.iteration), primitive, question_id, str(call_seq)]
        )
        call_id = "sha256:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return DecisionAudit(
            call_id=call_id,
            call_seq=call_seq,
            thread_id=context.thread_id,
            iteration=context.iteration,
            spec_version=context.spec_version,
            primitive=primitive,
            method=method,
            question_id=question_id,
            # Null 未经任何 question schema 投影；留空而非伪造 golden hash
            question_schema_hash="",
            state_digest=state_digest,
            state_ref=None,
            spec_content_sha256=_sha256(spec) if spec is not None else None,
            chunk_id=None,
            chunk_total=0,
            chunk_selected=0,
            chunk_omitted=0,
            model=None,
            checkpoint_id=None,
            checkpoint_manifest_sha256=None,
            laya_runtime_commit="",
            source_tree_clean=False,
            runtime_source_digest="",
            requested_device="auto",
            actual_device="",
            # Null 不做任何路由决策，allowlist 必须为空——§8.3 禁止塞绝对路径/原文
            route_allowlist=[],
            route_reason=self._reason,
            raw_answer={},
            answer_confidence=None,
            probabilities={},
            threshold_set=None,
            calibration_id=None,
            calibration_manifest_sha256=None,
            status="uncertain",
            route_action="no_action",
            value=None,
            usage={},
            batch_plan={},
            degraded=True,
            degradation_reason=self._reason,
            error_code=_REASON_TO_ERROR_CODE[self._reason],
            created_at=datetime.now(UTC).isoformat(),
        )

    # -- 五个公共原语 -----------------------------------------------------
    async def screen_document(self, content: str, *, context: DecisionContext) -> ScreenResult:
        """guard 原语。Null 不做 guard：无告警可产，status 只能是 uncertain。"""
        return ScreenResult(
            status="uncertain",
            findings=[],
            trace=[
                self._audit(
                    context=context,
                    primitive="screen",
                    method="screen_document",
                    question_id="",
                    state_digest=_sha256(content),
                )
            ],
        )

    async def assess_document(
        self,
        content: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> GateDecision:
        """assess 永远只产 annotation：cap=None、route_action=no_action。

        `section_index` 在此被刻意忽略——assess 的四个问题都不依赖索引
        （§5.1），而索引缺失只应让依赖索引的确定性标注降级，不该影响 assess。
        """
        del section_index  # 明确记录「不用」，避免后来者以为是漏传
        return GateDecision(
            status="uncertain",
            document_type=None,
            completeness_level=None,
            warnings=[],
            suggested_loop_cap=None,
            route_action="no_action",
            trace=[
                self._audit(
                    context=context,
                    primitive="assess",
                    method="assess_document",
                    question_id="",
                    state_digest=_sha256(content),
                )
            ],
        )

    async def verify_issues(
        self,
        issues: list[IssueStatus],
        spec: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> list[VerifiedIssue]:
        """只加标注，不删 issue、不改 severity。

        location 三元组**不猜**：location 有效性由 SectionIndex 确定性计算
        （§6.4「Laya 不回答 location 是否有效」），所以这里直接复用 T-04 的
        `normalize_location`——它是纯本地逻辑，与 Laya 降级无关。索引不可用时
        才落到 `LOC_INDEX_UNAVAILABLE`。

        每个 `VerifiedIssue.trace` 同时含 `verify_issues` 与 `review_executability`
        两条原语记录（§4.1）。
        """
        state_digest = _sha256(spec)
        verified: list[VerifiedIssue] = []
        for issue in issues:
            issue_id = str(issue.get("issue_id", ""))
            resolution = normalize_location(issue.get("location"), section_index)
            verified.append(
                VerifiedIssue(
                    issue_id=issue_id,
                    grounded=None,
                    severity_review=None,
                    laya_note=None,
                    location_valid=bool(resolution["valid"]),
                    normalized_location=resolution["normalized"],
                    location_error_code=resolution["error_code"],
                    location_error_message=resolution["error_message"],
                    trace=[
                        self._audit(
                            context=context,
                            primitive="verify_issues",
                            method="verify_issues",
                            question_id="grounded",
                            state_digest=state_digest,
                            spec=spec,
                        ),
                        self._audit(
                            context=context,
                            primitive=_REVIEW_EXECUTABILITY,
                            method="verify_issues",
                            question_id="style_only",
                            state_digest=state_digest,
                            spec=spec,
                        ),
                    ],
                )
            )
        return verified

    async def verify_resolutions(
        self,
        previous_issues: list[IssueStatus],
        spec: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> list[IssueResolution]:
        """只审计。`observed_status` 恒为 `uncertain`，且永不写回 IssueStatus。"""
        del section_index  # resolution_status 不依赖索引
        state_digest = _sha256(spec)
        return [
            IssueResolution(
                issue_id=str(issue.get("issue_id", "")),
                observed_status="uncertain",
                audit_only=True,
                trace=[
                    self._audit(
                        context=context,
                        primitive="verify_resolutions",
                        method="verify_resolutions",
                        question_id="resolution_status",
                        state_digest=state_digest,
                        spec=spec,
                    )
                ],
            )
            for issue in previous_issues
        ]

    async def judge_convergence(
        self,
        previous_issues: list[IssueStatus],
        current_issues: list[IssueStatus],
        counts: dict[str, int],
        tracker: IssueTracker,
        *,
        context: DecisionContext,
    ) -> ConvergenceDecision:
        """Null 不建议循环：cap=None、route_action=no_action。

        `counts`/`tracker` 刻意不参与判定——停滞与收敛是既有确定性逻辑的职责
        （§3.2 规则 5：Laya 的 uncertain 不是业务工作流错误）。让 Laya 缺席去
        改写循环上限，等于用降级状态污染业务控制流。
        """
        del previous_issues, current_issues, counts, tracker
        return ConvergenceDecision(
            status="uncertain",
            suggested_loop_cap=None,
            route_action="no_action",
            reason=f"decision engine degraded: {self._reason}",
            trace=[
                self._audit(
                    context=context,
                    primitive="judge_convergence",
                    method="judge_convergence",
                    question_id="",
                    state_digest=_sha256(""),
                )
            ],
        )


# 结构性自证：Null 引擎必须真的满足公共协议（isinstance + 方法齐全）。
_: type[DecisionEngine] = NullDecisionEngine
