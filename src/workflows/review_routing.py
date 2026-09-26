"""工作流只读路径函数与唯一 ``has_unresolved_blocking``（规格 §10.1／T-15）。

本模块是**路由真值表的唯一实现**。``review_workflow.py`` 只做 re-export，
以保持既有外部 import 路径不破坏（§13.2）。

**禁止反向 import ``review_workflow.py``**——那会构成循环依赖。本模块只依赖
``src.schemas.models``，跨模块类型依赖一律走 ``TYPE_CHECKING``。

§10.1 的推论值得复述，因为实现者极易想当然地放宽它：v1 中**没有任何代码路径
能写入** ``fixed`` / ``outdated``（``IssueStatus.status`` 恒为解析期写死的
``"open"``）。因此只要当前轮**或**紧邻上一轮出现过任何 Blocking/High，
``has_unresolved_blocking()`` **恒为** ``True``。这不是缺陷，是 §1 rule 5 与
§10.2 禁止 Laya 写业务状态的直接后果。**不得**为了让路由"看起来干净"而临时
写 ``status="fixed"`` 来解锁后续动作。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查，避免运行时循环导入
    from ..schemas.models import AgentState

logger = logging.getLogger(__name__)

__all__ = [
    "BLOCKING_SEVERITIES",
    "RESOLVED_STATUSES",
    "has_unresolved_blocking",
    "route_after_initialize",
    "route_after_load_document",
    "route_after_generate_spec",
    "route_after_evaluate",
    "route_after_revise_spec",
    "route_after_approval",
]

#: 构成「阻塞」的 severity 档位（§10.1 真值表第一列）。
BLOCKING_SEVERITIES: frozenset[str] = frozenset({"Blocking", "High"})

#: 唯一被视为「已解决」的 status。其余（含 ``open``／``partially_fixed``／
#: ``unfixed``／未知／缺失）一律按未解决处理——保守方向。
RESOLVED_STATUSES: frozenset[str] = frozenset({"fixed", "outdated"})

#: 生成/修订失败时直接终止的错误码前缀（Step 0 契约：失败轮次递增并终止）。
_FATAL_ERROR_PREFIXES: tuple[str, ...] = (
    "DOCREVIEW_ERR_GEN_",
    "DOCREVIEW_ERR_REV_",
    "DOCREVIEW_ERR_DOC_",
)


def _is_unresolved(issue: dict[str, Any]) -> bool:
    """单条 issue 是否算「未解决」（§10.1 保守定义）。"""
    return issue.get("status") not in RESOLVED_STATUSES


def _has_blocking_in_round(reports: Sequence[Mapping[str, Any]], index: int) -> bool:
    """第 ``index`` 轮报告里是否存在未解决的 Blocking/High。

    ``index`` 支持负数语义：``-1`` 为当前轮、``-2`` 为紧邻上一轮。负数先归一化
    成绝对下标再做边界检查——直接用 ``index < 0`` 判越界会把 ``-1``／``-2``
    一起误杀。
    """
    if not reports:
        return False
    real = index if index >= 0 else len(reports) + index
    if real < 0 or real >= len(reports):
        return False
    round_issues = reports[real].get("issues", []) or []
    return any(
        issue.get("severity") in BLOCKING_SEVERITIES and _is_unresolved(issue)
        for issue in round_issues
    )


def has_unresolved_blocking(state: AgentState) -> bool:
    """是否存在未解决的 Blocking/High——**唯一真值表**（§10.1）。

    只读 state。判据只看**当前轮**与**紧邻上一轮**：

    ==========================================  ======
    当前轮 open Blocking/High                    返回
    ==========================================  ======
    是                                          ``True``
    否，但紧邻上一轮有 unresolved Blocking/High   ``True``
    否，上一轮也没有（更早轮次忽略）             ``False``
    ==========================================  ======

    空当前报告和空上一报告都不构成阻塞（clean report grace）。

    Laya 的 ``observed_status`` **永远不能**改变此输入——它只能出现在
    ``IssueResolution.trace`` 里。
    """
    reports = list(state.get("review_reports", []) or [])
    if not reports:
        return False

    if _has_blocking_in_round(reports, -1):
        return True

    return _has_blocking_in_round(reports, -2)


def _has_fatal_error(state: AgentState) -> bool:
    """state 是否带有生成/修订/加载的致命错误码。"""
    error_code = state.get("error_code") or ""
    return any(error_code.startswith(prefix) for prefix in _FATAL_ERROR_PREFIXES)


# ─────────────────────────── 路径函数 ───────────────────────────


def route_after_initialize(state: AgentState) -> Literal["load_document", "generate_spec"]:
    """initialize 后：有 document_path 则加载文档，否则直接生成规格。"""
    if state.get("document_path"):
        return "load_document"
    return "generate_spec"


def route_after_load_document(state: AgentState) -> Literal["generate_spec", "finalize"]:
    """load_document 后：加载失败直接终止，否则进入规格生成。"""
    if _has_fatal_error(state):
        logger.info("文档加载失败，终止审查")
        return "finalize"
    return "generate_spec"


def route_after_generate_spec(state: AgentState) -> Literal["docreview", "finalize"]:
    """generate_spec 后：生成失败直接终止，否则进入文档审查。"""
    if _has_fatal_error(state):
        logger.info("规格生成失败，终止审查")
        return "finalize"
    return "docreview"


def route_after_evaluate(
    state: AgentState,
) -> Literal["user_approval", "revise_spec", "finalize"]:
    """evaluate_result 后的条件路由。

    守卫顺序即优先级：**legacy max → stagnation → 结论**。上限与停滞先于
    结论判定，否则一个「Pass 但已停滞」的状态会被送去等审批，白等一轮。
    """
    conclusion = state.get("review_conclusion", "Fail")
    iteration_count = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 10)
    stagnation_count = state.get("stagnation_count", 0)
    stagnation_threshold = state.get("stagnation_threshold", 2)

    if iteration_count >= max_iterations:
        logger.info("达到最大迭代次数，强制终止")
        return "finalize"

    if stagnation_count >= stagnation_threshold:
        logger.info("检测到停滞，强制终止")
        return "finalize"

    if conclusion in ("Pass", "Conditional Pass"):
        state["awaiting_approval"] = True
        return "user_approval"

    return "revise_spec"


def route_after_revise_spec(state: AgentState) -> Literal["docreview", "finalize"]:
    """revise_spec 后：修订失败直接终止，否则回到文档审查。

    修订后需刷新章节索引（`section_index_dirty`），由 docreview 节点消费。
    """
    if _has_fatal_error(state):
        logger.info("规格修订失败，终止审查")
        return "finalize"
    return "docreview"


def route_after_approval(
    state: AgentState,
) -> Literal["execute", "revise_spec", "finalize"]:
    """user_approval 后的条件路由。

    审批超时是**错误路径**，必须落 ``DOCREVIEW_ERR_LOOP_003`` 并终止，不得
    静默当作拒绝继续走修订循环。
    """
    if state.get("approval_timed_out"):
        state["error_code"] = "DOCREVIEW_ERR_LOOP_003"
        return "finalize"

    if state.get("user_approved"):
        return "execute"

    conclusion = state.get("review_conclusion", "")

    if conclusion == "Conditional Pass":
        return "revise_spec"

    return "finalize"
