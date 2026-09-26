"""T-13：五原语策略、DecisionAudit、CPU-only act 与 OOM 语义。

本模块实现 :class:`~src.decisions.types.DecisionEngine` 的五个 async 方法。核心纪律：

**§8.1 固定映射**——每个原语的唯一有效 ``act`` 效果是**标注**，不是业务动作：

============ ================================ ============ ===============
primitive     唯一有效 act 效果                loop cap     route_action
============ ================================ ============ ===============
screen        写 warning finding                ``None``     ``no_action``
assess        document type/验收/模糊词标注     ``None``     ``no_action``
verify_issues grounded/severity 标注            ``None``     ``no_action``
verify_resolutions observed_status 审计         ``None``     ``no_action``
judge_convergence convergence 建议              仅两个 noul  ``suggest_cap``
============ ================================ ============ ===============

- ``assess`` 的 choice/noul 即使 ``act`` 也**只是标注**：``suggested_loop_cap`` 在 v1
  固定为 ``None``；``completeness`` score 永不生成 cap。
- ``screen`` 整体 status **只**由 ``jailbreak``/``prompt_injection``/``sensitive_data``
  的 noul 与 ``topic`` 的 choice 聚合；``harm_severity`` score 不参与。
- ``judge_convergence`` 只有 ``blocking_high_resolved=act(true)`` **且**
  ``converged=act(false)`` 同时成立才可 ``suggest_cap``；任一 uncertain/解析失败/
  calibration 不匹配均为 ``no_action``；``improvement`` score 永不参与该谓词。

**§8.2 门控**复用 :func:`src.decisions.calibration._decide`（唯一三态实现）：门控只读
``answer_confidence``，``confidence``/``act_probability``/score 期望值**只记录**。
没有匹配有效 calibration record 时三态全为 ``uncertain``。

**§8.3 act 附加守卫**：``source_tree_clean=false``、``runtime_source_digest`` 缺失或
``spec_content_sha256`` 缺失时**只能 audit/degraded，不得 act**。

**CPU-only act**：``act`` 还要求显式合法 model（``auto``/``typed-decisions`` 不算）且
``device == "cpu"``（见 :func:`~src.decisions.laya_adapter.act_allowed`）。

导入纪律：本模块不得 import laya / torch / transformers。推理经**注入的**
``predict`` 可调用对象完成，故全部逻辑可在无权重的环境测试。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, cast

from src.config import LayaConfig
from src.decisions.calibration import Thresholds, _decide, canonical_digest
from src.decisions.factory import await_thread
from src.decisions.laya_adapter import (
    AdapterError,
    BatchItem,
    act_allowed,
    act_refusal_reason,
    build_requests,
    plan_batches,
)
from src.decisions.question_contracts import (
    FROZEN_BUSINESS_QUESTIONS,
    build_assess_state,
    build_judge_convergence_state,
    build_review_executability_state,
    build_screen_state,
    build_verify_issues_state,
    build_verify_resolutions_state,
    sha256_canonical,
    to_laya_questions,
)
from src.decisions.types import (
    ROUTE_ALLOWLIST,
    ConvergenceDecision,
    DecisionAudit,
    DecisionContext,
    DecisionStatus,
    GateDecision,
    IssueResolution,
    LayaFinding,
    LayaRouteAction,
    ScreenResult,
    VerifiedIssue,
)
from src.schemas.models import IssueStatus, IssueTracker
from src.state.section_index import SectionIndex

__all__ = [
    "RuntimeProvenance",
    "LayaDecisionEngine",
    "NullDecisionEngine",
    "GatedAnswer",
    "SCREEN_GATING_QUESTIONS",
    "CONVERGENCE_NOOL_QUESTIONS",
]

#: §8.1：screen 整体 status 只由这些 noul + topic choice 聚合。
SCREEN_GATING_QUESTIONS: Final[tuple[str, ...]] = (
    "jailbreak",
    "prompt_injection",
    "sensitive_data",
)

#: §8.1：只有这两个 noul 可建议整数 loop cap。
CONVERGENCE_NOOL_QUESTIONS: Final[tuple[str, ...]] = (
    "blocking_high_resolved",
    "converged",
)

#: 推理结果：Laya Agent.predict_batch 返回的行序列（本项目按 question 顺序对齐）。
PredictCallable = Callable[[Sequence[Mapping[str, Any]]], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class RuntimeProvenance:
    """运行时与来源证明（§8.3）。

    ``source_tree_clean`` / ``runtime_source_digest`` / ``spec_content_sha256``
    三者任一不可信，engine 只能 audit/degraded，不得 act。
    """

    laya_runtime_commit: str = ""
    source_tree_clean: bool = False
    runtime_source_digest: str = ""
    requested_device: str = "cpu"
    actual_device: str = "cpu"
    checkpoint_id: str | None = None
    checkpoint_manifest_sha256: str | None = None
    calibration_id: str | None = None
    calibration_manifest_sha256: str | None = None
    route_allowlist: list[str] = field(default_factory=lambda: sorted(ROUTE_ALLOWLIST))
    route_reason: str = "laya_route_v1"

    def act_provenance_ok(self) -> bool:
        """§8.3：来源不可信时禁止 act。"""
        return bool(self.source_tree_clean) and bool(self.runtime_source_digest)


@dataclass(frozen=True)
class GatedAnswer:
    """一条 raw answer 的门控结果 + 其 audit。"""

    primitive: str
    question_id: str
    status: DecisionStatus
    value: Any
    confidence: float | None
    audit: DecisionAudit


def _as_status(value: str) -> DecisionStatus:
    """把 :func:`calibration._decide` 的 ``str`` 收窄为 ``DecisionStatus``。

    ``_decide`` 只可能返回三态之一；此处的兜底让类型边界显式化，非法值一律
    降级为 ``uncertain``（fail-safe：绝不把未知状态当成 act）。
    """
    if value in ("act", "pass", "uncertain"):
        return cast(DecisionStatus, value)
    return "uncertain"


def _noul_value(gate: GatedAnswer | None) -> bool | None:
    """把 noul 门控结果归一为 ``True`` / ``False`` / ``None``（None = 未解析）。

    §8.2：``noul >= pos`` → ``act``（取值 true）；``noul <= neg`` → ``pass``
    （取值 false）；其余 ``uncertain``。这里按**取值**归一，忽略 act/pass 的
    表述差异——§8.1 用 ``act(true)`` / ``act(false)`` 描述谓词，但 false 侧在
    §8.2 中落在 ``pass``，死抠 status 字面会让 false 分支永不成立。
    """
    if gate is None or gate.status not in ("act", "pass"):
        return None
    value = gate.value
    if value is True or value == "true":
        return True
    if value is False or value == "false":
        return False
    return None


def _noul_resolved_true(gate: GatedAnswer | None) -> bool:
    return _noul_value(gate) is True


def _noul_resolved_false(gate: GatedAnswer | None) -> bool:
    return _noul_value(gate) is False


def _issue_id_of(issue: Any, fallback: str) -> str:
    """取 issue_id。``IssueStatus`` 是 TypedDict（运行时即 dict），故须按映射读，
    不能用 ``getattr``——那会永远落到 fallback，丢失真实 ID。"""
    if isinstance(issue, Mapping):
        value = issue.get("issue_id")
        if isinstance(value, str) and value:
            return value
    value = getattr(issue, "issue_id", None)
    return value if isinstance(value, str) and value else fallback


def _probabilities_of(raw_answer: Mapping[str, Any]) -> dict[str, float]:
    """提取 raw answer 的概率表，非有限/非数值项一律丢弃（§8.2 末句）。"""
    source = raw_answer.get("probabilities")
    if not isinstance(source, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, value in source.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            continue
        result[str(key)] = number
    return result


def _threshold_set_of(thresholds: Thresholds | None) -> dict[str, float] | None:
    """把本次门控实际用的阈值快照进 audit（§8.3 要求完整保存 threshold）。"""
    if thresholds is None:
        return None
    result: dict[str, float] = {}
    for key, value in thresholds.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        result[str(key)] = float(value)
    return result


class LayaDecisionEngine:
    """真实 Laya 决策引擎。

    推理经注入的 ``predict`` 完成（生产由 :mod:`src.decisions.factory` 注入真实
    Router/Agent），故本类不 import laya，可在无权重环境完整测试。
    """

    def __init__(
        self,
        config: LayaConfig,
        *,
        provenance: RuntimeProvenance,
        predict: PredictCallable,
        guard_questions: Mapping[str, Mapping[str, Any]] | None = None,
        thresholds: Mapping[str, Thresholds] | None = None,
        spec_content_sha256: str | None = None,
    ) -> None:
        self._config = config
        self._provenance = provenance
        self._predict = predict
        # §8.3：spec_content_sha256 缺失时只能 audit/degraded，不得 act。五个原语的
        # 签名（§4 DecisionEngine Protocol）不带该值——它标识**被评审的规格内容**，
        # 而非某次调用的属性，故由构造期注入（规格修订后须重建 engine 或改此值）。
        self._spec_content_sha256 = spec_content_sha256
        # guard 原语的 question 来自 Laya 自己的 `laya.presets.guard_questions()`，
        # **不在**本项目冻结契约内（§5.2：guard 的 noul 无 criteria 键）。故经注入
        # 传入——既守住「本模块不 import laya」的边界，也让无权重环境可测。
        self._guard_questions: Mapping[str, Mapping[str, Any]] = guard_questions or {}
        # 无匹配 calibration record → 空表 → 全部 uncertain（§8.2 末句）
        self._thresholds: dict[str, Thresholds] = dict(thresholds or {})

    # ------------------------------------------------------------------
    # 内部：门控 + 审计
    # ------------------------------------------------------------------
    def _threshold_for(self, primitive: str, question_id: str) -> Thresholds | None:
        return self._thresholds.get(f"{primitive}.{question_id}")

    def _thresholds_ok(self) -> bool:
        return bool(self._thresholds)

    def _act_permitted(self, spec_content_sha256: str | None) -> tuple[bool, str]:
        """聚合 §8.3 来源守卫 + §7 CPU-only/显式 model 守卫。"""
        if not self._provenance.act_provenance_ok():
            return False, "source_tree_clean=false 或 runtime_source_digest 缺失"
        if not spec_content_sha256:
            return False, "spec_content_sha256 缺失"
        if not self._thresholds_ok():
            return False, "无有效 calibration record（§8.2 三态全 uncertain）"
        if not act_allowed(self._config):
            return False, act_refusal_reason(self._config) or "act 被拒绝"
        return True, "ok"

    def _gated(
        self,
        primitive: str,
        method: str,
        question_id: str,
        raw_answer: Mapping[str, Any],
        state: Mapping[str, Any],
        *,
        context: DecisionContext,
        call_seq: int,
        spec_content_sha256: str | None,
        act_permitted: bool,
        chunk_id: str | None,
        chunk_total: int,
        chunk_selected: int,
        chunk_omitted: int,
        batch_plan: Mapping[str, Any],
        request_model: str | None,
        act_refusal: str | None = None,
    ) -> GatedAnswer:
        """对单条 raw answer 门控并生成 audit（§8.2 + §8.3）。"""
        # guard 原语（screen）的 question 不在冻结契约内（§5.2：guard 的 noul 无
        # criteria 键，来自 Laya preset），故先查冻结契约、再回落注入的 guard 表。
        question = FROZEN_BUSINESS_QUESTIONS.get(primitive, {}).get(question_id)
        if question is None:
            question = self._guard_questions.get(question_id)
        shape = str((question or {}).get("type", ""))
        schema_hash = sha256_canonical(
            {
                "primitive": primitive,
                "questions": [question_id],
                "shape": shape,
            }
        )
        thresholds = self._threshold_for(primitive, question_id)
        status: DecisionStatus
        value: Any
        confidence: float | None
        if thresholds is None:
            status, value, confidence = "uncertain", None, None
        else:
            inference = {
                "raw_answer": dict(raw_answer),
            "probabilities": _probabilities_of(raw_answer),
            }
            decided, value, confidence, _ = _decide(
                primitive,
                question_id,
                {},
                inference,
                thresholds,
                question=question,
            )
            status = _as_status(decided)

        # §8.3 act 附加守卫：来源不可信时强制降级为 uncertain。
        degraded_reason: str | None = None
        if status == "act" and not act_permitted:
            status, value = "uncertain", None
            # 记录**具体**拒绝原因（auto/非 cpu/来源脏/spec sha 缺失…），
            # 而不是笼统的「被拒绝」——§8.3 要求降级原因完整可复盘。
            degraded_reason = f"act 被 §8.3/§7 守卫拒绝：{act_refusal or '未说明原因'}"
        if thresholds is None:
            degraded_reason = degraded_reason or "无匹配 calibration record"

        route_action: LayaRouteAction = "no_action"
        call_id = canonical_digest(
            {
                "thread_id": context.thread_id,
                "iteration": context.iteration,
                "primitive": primitive,
                "question_id": question_id,
                "call_seq": call_seq,
            }
        )
        audit: DecisionAudit = {
            "call_id": call_id,
            "call_seq": call_seq,
            "thread_id": context.thread_id,
            "iteration": context.iteration,
            "spec_version": context.spec_version,
            "primitive": primitive,
            "method": method,
            "question_id": question_id,
            "question_schema_hash": schema_hash,
            "state_digest": canonical_digest(state),
            "state_ref": None,
            "spec_content_sha256": spec_content_sha256,
            "chunk_id": chunk_id,
            "chunk_total": chunk_total,
            "chunk_selected": chunk_selected,
            "chunk_omitted": chunk_omitted,
            "model": request_model,
            "checkpoint_id": self._provenance.checkpoint_id,
            "checkpoint_manifest_sha256": self._provenance.checkpoint_manifest_sha256,
            "laya_runtime_commit": self._provenance.laya_runtime_commit,
            "source_tree_clean": self._provenance.source_tree_clean,
            "runtime_source_digest": self._provenance.runtime_source_digest,
            "requested_device": self._provenance.requested_device,
            "actual_device": self._provenance.actual_device,
            "route_allowlist": list(self._provenance.route_allowlist),
            "route_reason": self._provenance.route_reason,
            "raw_answer": dict(raw_answer),
            "answer_confidence": raw_answer.get("answer_confidence"),
            "probabilities": _probabilities_of(raw_answer),
            "threshold_set": _threshold_set_of(thresholds),
            "calibration_id": self._provenance.calibration_id,
            "calibration_manifest_sha256": self._provenance.calibration_manifest_sha256,
            "status": status,  # type: ignore[typeddict-item]
            "route_action": route_action,
            "value": value,
            "usage": {},
            "batch_plan": dict(batch_plan),
            "degraded": degraded_reason is not None,
            "degradation_reason": degraded_reason,
            "error_code": None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        return GatedAnswer(
            primitive=primitive,
            question_id=question_id,
            status=status,
            value=value,
            confidence=confidence,
            audit=audit,
        )

    # ------------------------------------------------------------------
    # 内部：统一推理流水线（预算前置 → 推理 → 逐条门控）
    # ------------------------------------------------------------------
    async def _run_primitive(
        self,
        primitive: str,
        method: str,
        items: Sequence[tuple[dict[str, Any], Sequence[tuple[str, dict[str, Any]]]]],
        *,
        context: DecisionContext,
        spec_content_sha256: str | None,
        call_seq: int,
    ) -> list[GatedAnswer]:
        """统一流水线：atomic items → 预算前置 → 推理 → 逐条门控。

        每个 item 是 ``(state, [(question_id, question), ...])``——**完整 question
        set 不可拆分**（§11.2 atomic）。任何 Laya 失败都降级为 uncertain +
        no_action + 对应 ``LAYA_ERR_*``，绝不向上冒泡（决策层是增强项）。
        """
        if not items:
            return []
        act_ok, act_refusal = self._act_permitted(spec_content_sha256)

        # 展平为 (item_index, question_id, question, state)。
        flat: list[tuple[int, str, dict[str, Any], dict[str, Any]]] = []
        for i, (state, questions) in enumerate(items):
            for qid, question in questions:
                flat.append((i, qid, question, state))

        batch_items = [
            BatchItem(
                index=i,
                primitive=primitive,
                question_schema_hash=sha256_canonical({"p": primitive, "i": i}),
                model=None,
                state=items[i][0],
                questions=[q for _qid, q in items[i][1]],
            )
            for i in range(len(items))
        ]

        try:
            plans = plan_batches(batch_items, self._config)  # 超预算 → LAYA_ERR_BATCH
            requests = build_requests(
                [(state, [q for _qid, q in questions]) for state, questions in items],
                self._config,
            )
        except AdapterError as exc:
            return self._degraded_all(
                primitive, flat, exc.code, exc.detail, context, spec_content_sha256, act_ok
            )

        try:
            answers = await await_thread(
                lambda: list(self._predict(requests)),
                timeout_seconds=self._config.timeout_seconds,
            )
        except TimeoutError:
            return self._degraded_all(
                primitive, flat, "LAYA_ERR_TIMEOUT", "推理超时", context, spec_content_sha256, act_ok
            )
        except AdapterError as exc:
            return self._degraded_all(
                primitive, flat, exc.code, exc.detail, context, spec_content_sha256, act_ok
            )
        except (MemoryError, RuntimeError) as exc:  # CPU OOM 语义
            if "out of memory" in str(exc).lower() or isinstance(exc, MemoryError):
                return self._degraded_all(
                    primitive, flat, "LAYA_ERR_OOM", str(exc), context, spec_content_sha256, act_ok
                )
            raise

        plan_for_index = {}
        for plan in plans:
            for idx in plan["item_indexes"]:
                plan_for_index[idx] = plan

        gated: list[GatedAnswer] = []
        for (i, qid, _question, state), answer in zip(flat, answers, strict=False):
            plan = plan_for_index.get(i, {})
            gated.append(
                self._gated(
                    primitive,
                    method,
                    qid,
                    answer if isinstance(answer, Mapping) else {},
                    state,
                    context=context,
                    call_seq=call_seq,
                    spec_content_sha256=spec_content_sha256,
                    act_permitted=act_ok,
                    chunk_id=None,
                    chunk_total=1,
                    chunk_selected=1,
                    chunk_omitted=0,
                    batch_plan=plan,
                    request_model=None,
                    act_refusal=act_refusal,
                )
            )
        return gated

    def _degraded_all(
        self,
        primitive: str,
        flat: Sequence[tuple[int, str, dict[str, Any], dict[str, Any]]],
        error_code: str,
        detail: str,
        context: DecisionContext,
        spec_content_sha256: str | None,
        act_ok: bool,
    ) -> list[GatedAnswer]:
        """全部降级为 uncertain + no_action + 指定 error_code（不重试、不循环）。"""
        gated: list[GatedAnswer] = []
        for _i, qid, _question, state in flat:
            answer = self._gated(
                primitive,
                "degraded",
                qid,
                {},
                state,
                context=context,
                call_seq=0,
                spec_content_sha256=spec_content_sha256,
                act_permitted=False,
                chunk_id=None,
                chunk_total=1,
                chunk_selected=0,
                chunk_omitted=1,
                batch_plan={},
                request_model=None,
            )
            answer.audit["error_code"] = error_code
            answer.audit["degraded"] = True
            answer.audit["degradation_reason"] = detail
            gated.append(answer)
        return gated

    # ------------------------------------------------------------------
    # 五个原语（§8.1 固定映射）
    # ------------------------------------------------------------------
    async def screen_document(self, content: str, *, context: DecisionContext) -> ScreenResult:
        """guard：只写 warning finding，不拒绝、不改文。"""
        state = build_screen_state(content)
        questions = [
            (qid, to_laya_questions({qid: question})[0])
            for qid, question in self._guard_questions.items()
        ]
        gated = await self._run_primitive(
            "screen", "screen_document", [(state, questions)],
            context=context, spec_content_sha256=self._spec_content_sha256, call_seq=0,
        )
        # §8.1：整体 status 只由 3 个 noul + topic choice 聚合；harm_severity score 不参与。
        statuses = [g.status for g in gated if g.question_id in SCREEN_GATING_QUESTIONS or g.question_id == "topic"]
        if any(s == "act" for s in statuses):
            overall = "act"
        elif statuses and all(s == "pass" for s in statuses):
            overall = "pass"
        else:
            overall = "uncertain"
        findings: list[LayaFinding] = []
        if overall == "act":
            findings.append(
                LayaFinding(
                    finding_id="screen-0",
                    kind="guard",
                    severity="warning",
                    message="screen 原语判定为 act：仅产生 warning，不拒绝、不改文",
                    source="laya.screen",
                    decision_ids=[g.audit["call_id"] for g in gated],
                )
            )
        return ScreenResult(
            status=overall,  # type: ignore[typeddict-item]
            findings=findings,
            trace=[g.audit for g in gated],
        )

    async def assess_document(
        self, content: str, section_index: SectionIndex | None, *, context: DecisionContext
    ) -> GateDecision:
        """永远只产 annotation：suggested_loop_cap=None、route_action=no_action。"""
        state = build_assess_state(content, "", "")
        questions = [
            (qid, to_laya_questions({qid: FROZEN_BUSINESS_QUESTIONS["assess"][qid]})[0])
            for qid in FROZEN_BUSINESS_QUESTIONS["assess"]
        ]
        gated = await self._run_primitive(
            "assess", "assess_document", [(state, questions)],
            context=context, spec_content_sha256=self._spec_content_sha256, call_seq=0,
        )
        by_q = {g.question_id: g for g in gated}
        doc_type = by_q["document_type"].value if "document_type" in by_q else None
        # §8.1：assess 即使 act 也只是标注，suggested_loop_cap 固定 None。
        return GateDecision(
            status=by_q["document_type"].status if "document_type" in by_q else "uncertain",
            document_type=doc_type if isinstance(doc_type, str) else None,
            completeness_level=None,
            warnings=[],
            suggested_loop_cap=None,
            route_action="no_action",
            trace=[g.audit for g in gated],
        )

    async def verify_issues(
        self,
        issues: list[IssueStatus],
        spec: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> list[VerifiedIssue]:
        """只加标注（grounded/severity/laya_note）；不删 issue、不改 severity。"""
        results: list[VerifiedIssue] = []
        for idx, issue in enumerate(issues):
            state = build_verify_issues_state(dict(issue), spec, "", {}, "")
            questions = [
                (qid, to_laya_questions({qid: FROZEN_BUSINESS_QUESTIONS["verify_issues"][qid]})[0])
                for qid in FROZEN_BUSINESS_QUESTIONS["verify_issues"]
            ]
            gated = await self._run_primitive(
                "verify_issues", "verify_issues", [(state, questions)],
                context=context, spec_content_sha256=self._spec_content_sha256, call_seq=idx,
            )
            by_q = {g.question_id: g for g in gated}
            grounded_gate = by_q.get("grounded")
            severity_gate = by_q.get("severity_review")
            # §8.2 的 noul 取值是字符串 "true"/"false"；`grounded` 的契约是
            # `bool | None`，故归一化——直接透传会让 isinstance 判定失败而恒为 None。
            grounded = _noul_value(grounded_gate)

            # §4.1：verify_issues 的 trace 必须同时含 verify_issues 与内部
            # review_executability 两条原语记录。review_executability 只写
            # style-only 的 laya_note，永不改写 grounded/severity。
            exec_state = build_review_executability_state(dict(issue), spec, "")
            exec_questions = [
                (qid, to_laya_questions({qid: FROZEN_BUSINESS_QUESTIONS["review_executability"][qid]})[0])
                for qid in FROZEN_BUSINESS_QUESTIONS["review_executability"]
            ]
            exec_gated = await self._run_primitive(
                "review_executability", "verify_issues", [(exec_state, exec_questions)],
                context=context, spec_content_sha256=self._spec_content_sha256, call_seq=idx,
            )
            exec_by_q = {g.question_id: g for g in exec_gated}
            style_gate = exec_by_q.get("style_only")
            laya_note = style_gate.value if style_gate and style_gate.status == "act" else None

            results.append(
                VerifiedIssue(
                    issue_id=_issue_id_of(issue, f"issue-{idx}"),
                    grounded=grounded,
                    severity_review=severity_gate.value if severity_gate else None,
                    laya_note=laya_note if isinstance(laya_note, str) else None,
                    location_valid=True,
                    normalized_location=None,
                    location_error_code=None,
                    location_error_message=None,
                    trace=[g.audit for g in gated] + [g.audit for g in exec_gated],
                )
            )
        return results

    async def verify_resolutions(
        self,
        previous_issues: list[IssueStatus],
        spec: str,
        section_index: SectionIndex | None,
        *,
        context: DecisionContext,
    ) -> list[IssueResolution]:
        """只审计 observed_status；永不写回 IssueStatus.status（audit_only 恒 True）。"""
        results: list[IssueResolution] = []
        for idx, issue in enumerate(previous_issues):
            state = build_verify_resolutions_state(dict(issue), spec, "", "")
            questions = [
                (qid, to_laya_questions({qid: FROZEN_BUSINESS_QUESTIONS["verify_resolutions"][qid]})[0])
                for qid in FROZEN_BUSINESS_QUESTIONS["verify_resolutions"]
            ]
            gated = await self._run_primitive(
                "verify_resolutions", "verify_resolutions", [(state, questions)],
                context=context, spec_content_sha256=self._spec_content_sha256, call_seq=idx,
            )
            observed = "uncertain"
            for g in gated:
                if g.status == "act" and isinstance(g.value, str):
                    observed = g.value
            results.append(
                IssueResolution(
                    issue_id=_issue_id_of(issue, f"issue-{idx}"),
                    observed_status=observed,  # type: ignore[typeddict-item]
                    audit_only=True,
                    trace=[g.audit for g in gated],
                )
            )
        return results

    async def judge_convergence(
        self,
        previous_issues: list[IssueStatus],
        current_issues: list[IssueStatus],
        counts: dict[str, int],
        tracker: IssueTracker,
        *,
        context: DecisionContext,
    ) -> ConvergenceDecision:
        """仅两个 noul 可建议 cap；任一 uncertain/失败 → no_action。"""
        state = build_judge_convergence_state(
            [dict(i) for i in previous_issues],
            [dict(i) for i in current_issues],
            counts,
            {},
        )
        questions = [
            (qid, to_laya_questions({qid: FROZEN_BUSINESS_QUESTIONS["judge_convergence"][qid]})[0])
            for qid in FROZEN_BUSINESS_QUESTIONS["judge_convergence"]
        ]
        gated = await self._run_primitive(
            "judge_convergence", "judge_convergence", [(state, questions)],
            context=context, spec_content_sha256=self._spec_content_sha256, call_seq=0,
        )
        by_q = {g.question_id: g for g in gated}
        resolved_gate = by_q.get("blocking_high_resolved")
        converged_gate = by_q.get("converged")
        # §8.1 谓词：blocking_high_resolved 判定为 true 且 converged 判定为 false。
        #
        # 注意 §8.1 写作 `act(true)` / `act(false)`，但 §8.2 的 noul 规则里「有把握的
        # false」映射到 status=`pass`（`noul <= neg` → pass(false)），并非 `act`。
        # 故此处按**解析出的取值**判定（true→act，false→pass），而非死抠 status 字面：
        # 否则 converged 永远无法解析为 false，suggest_cap 永不触发。
        cap_ok = (
            _noul_resolved_true(resolved_gate)
            and _noul_resolved_false(converged_gate)
        )
        if cap_ok:
            return ConvergenceDecision(
                status="act",
                suggested_loop_cap=counts.get("iteration"),
                route_action="suggest_cap",
                reason="blocking_high_resolved=act(true) 且 converged=act(false)",
                trace=[g.audit for g in gated],
            )
        return ConvergenceDecision(
            status="uncertain",
            suggested_loop_cap=None,
            route_action="no_action",
            reason="谓词未成立或任一 noul uncertain（§8.1/§10.3 守卫）",
            trace=[g.audit for g in gated],
        )


class NullDecisionEngine:
    """降级 Null 引擎（disabled / unavailable）：全部 uncertain + no_action + degraded。"""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _audit(self, primitive: str, context: DecisionContext) -> DecisionAudit:
        return DecisionAudit(
            call_id="null", call_seq=0, thread_id=context.thread_id,
            iteration=context.iteration, spec_version=context.spec_version,
            primitive=primitive, method="null", question_id="null",
            question_schema_hash="", state_digest="", state_ref=None,
            spec_content_sha256=None, chunk_id=None, chunk_total=0,
            chunk_selected=0, chunk_omitted=0, model=None, checkpoint_id=None,
            checkpoint_manifest_sha256=None, laya_runtime_commit="",
            source_tree_clean=False, runtime_source_digest="",
            requested_device="cpu", actual_device="cpu",
            route_allowlist=sorted(ROUTE_ALLOWLIST), route_reason="null",
            raw_answer={}, answer_confidence=None, probabilities={},
            threshold_set=None, calibration_id=None, calibration_manifest_sha256=None,
            status="uncertain", route_action="no_action", value=None,
            usage={}, batch_plan={}, degraded=True, degradation_reason=self._reason,
            error_code="LAYA_ERR_DISABLED" if self._reason == "laya_disabled" else "LAYA_ERR_IMPORT",
            created_at=datetime.now(UTC).isoformat(),
        )

    async def screen_document(self, content: str, *, context: DecisionContext) -> ScreenResult:
        return ScreenResult(status="uncertain", findings=[], trace=[self._audit("screen", context)])

    async def assess_document(
        self, content: str, section_index: SectionIndex | None, *, context: DecisionContext
    ) -> GateDecision:
        return GateDecision(
            status="uncertain", document_type=None, completeness_level=None,
            warnings=[], suggested_loop_cap=None, route_action="no_action",
            trace=[self._audit("assess", context)],
        )

    async def verify_issues(
        self, issues: list[IssueStatus], spec: str, section_index: SectionIndex | None, *,
        context: DecisionContext,
    ) -> list[VerifiedIssue]:
        return [
            VerifiedIssue(
                issue_id=_issue_id_of(i, f"issue-{n}"), grounded=None,
                severity_review=None, laya_note=None, location_valid=False,
                normalized_location=None, location_error_code="LAYA_ERR_DISABLED",
                location_error_message=self._reason,
                trace=[self._audit("verify_issues", context)],
            )
            for n, i in enumerate(issues)
        ]

    async def verify_resolutions(
        self, previous_issues: list[IssueStatus], spec: str, section_index: SectionIndex | None, *,
        context: DecisionContext,
    ) -> list[IssueResolution]:
        return [
            IssueResolution(
                issue_id=_issue_id_of(i, f"issue-{n}"), observed_status="uncertain",
                audit_only=True, trace=[self._audit("verify_resolutions", context)],
            )
            for n, i in enumerate(previous_issues)
        ]

    async def judge_convergence(
        self, previous_issues: list[IssueStatus], current_issues: list[IssueStatus],
        counts: dict[str, int], tracker: IssueTracker, *, context: DecisionContext,
    ) -> ConvergenceDecision:
        return ConvergenceDecision(
            status="uncertain", suggested_loop_cap=None, route_action="no_action",
            reason=self._reason, trace=[self._audit("judge_convergence", context)],
        )
