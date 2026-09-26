"""§9.5 决策评估脚本：只读 history／specification snapshots → 离线指标。

**只读契约（§9.5）**：本脚本**不修改**业务 state、审查结论或 checkpoint。它只在
内存里聚合已落盘的 history 文件，输出评估报告。唯一的写操作是可选的
``--output``（把聚合结果写到调用方指定的路径），业务侧状态一律不碰。

指标（§9.5 逐条）：

============  ==========================================================
误报率／漏报率  逐条列出 finding 供**人工对照**——自动判定需要人工标签，
              故此处只给对照入口（finding + trace 决策 + 人工判定槽位）
修复率        ``fixed_count / all_issues``
提前终止候选率  命中 ``has_unresolved_blocking`` 真值表即候选
路由分布      ``laya_trace`` 中各 route 的出现次数
降级率        ``mcp_degraded`` 或 trace 中 degraded 决策占比
LLM 成本      ``total_llm_cost`` 汇总
校准质量      有效 calibration 记录数与降级占比
============  ==========================================================

**缺少 specification snapshot 时明确输出 ``not_replayable``**（§12.2）：旧 history
不带 snapshots，其 audit 回放不可判定——此时**不**静默回落到 ``specification``
字段，因为那是最后一次的正文，未必对应被问询的 spec_version。

**导入边界**：本脚本不是 ``src.decisions`` 的一部分，故不触碰该包；只依赖
``src.state.history_store`` 的纯读取函数。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from src.state.history_store import NOT_REPLAYABLE, read_history, replay_specification

if TYPE_CHECKING:  # pragma: no cover
    from src.schemas.models import AgentState


def _iter_history_files(history_dir: Path, thread_id: str | None) -> list[Path]:
    """列出待评估的 history 文件；``thread_id`` 给了就只看那一个。"""
    if thread_id:
        exact = history_dir / f"history-{thread_id}.json"
        return [exact] if exact.exists() else []
    # 只取 .json；`.json.lock` 与 `.json.tmp.<pid>` 不匹配该模式。
    return sorted(history_dir.glob("history-*.json"))


def _route_of(trace_entry: Any) -> str:
    """从一条 laya_trace 记录里取路由名，缺失时归入 ``unknown``。"""
    if isinstance(trace_entry, dict):
        for key in ("route", "decision", "action"):
            value = trace_entry.get(key)
            if isinstance(value, str) and value:
                return value
    return "unknown"


def _is_degraded(trace_entry: Any) -> bool:
    """判断单条 trace 是否为降级决策。"""
    if not isinstance(trace_entry, dict):
        return False
    if trace_entry.get("degraded") is True:
        return True
    action = trace_entry.get("action")
    return isinstance(action, str) and action in {"degraded", "audit_only"}


def _manual_review_entries(history: dict[str, Any]) -> list[dict[str, Any]]:
    """构造误报／漏报的**人工对照入口**。

    §9.5 要求给出「人工对照入口」而非自动判定：自动算 FPR/FNR 需要人工标签，
    而 v1 没有校准语料（§0.2），因此这里逐条给出 finding 与其对应决策，
    由人工填 ``human_verdict``（``true_positive``/``false_positive``/
    ``true_negative``/``false_negative``）后再统计。
    """
    findings = history.get("laya_findings") or []
    trace = history.get("laya_trace") or []
    entries: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        decision = trace[index] if index < len(trace) else None
        entries.append(
            {
                "index": index,
                "issue_id": finding.get("issue_id"),
                "severity": finding.get("severity"),
                "issue_type": finding.get("issue_type"),
                "description": finding.get("description"),
                "decision": _route_of(decision),
                "degraded": _is_degraded(decision),
                # 人工填写；None 表示尚未判定。
                "human_verdict": None,
            }
        )
    return entries


def _fix_rate(history: dict[str, Any]) -> dict[str, Any]:
    """修复率：``issue_tracker`` 优先，缺失时按 reports 内 status 兜底统计。"""
    tracker = history.get("issue_tracker")
    if isinstance(tracker, dict) and tracker.get("all_issues"):
        all_issues = len(tracker["all_issues"])
        fixed = int(tracker.get("fixed_count", 0) or 0)
        return {
            "source": "issue_tracker",
            "all": all_issues,
            "fixed": fixed,
            "fix_rate": (fixed / all_issues) if all_issues else None,
        }

    total = 0
    fixed = 0
    for report in history.get("reports") or []:
        if not isinstance(report, dict):
            continue
        for issue in report.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            total += 1
            if issue.get("status") in {"fixed", "resolved"}:
                fixed += 1
    return {
        "source": "reports",
        "all": total,
        "fixed": fixed,
        "fix_rate": (fixed / total) if total else None,
    }


def _early_stop_candidates(history: dict[str, Any]) -> dict[str, Any]:
    """提前终止候选：未解决的 blocking 问题存在即候选。

    复用 ``has_unresolved_blocking`` 的真值表，而不是在此重写一份判定逻辑——
    两处判定漂移会让评估结论与运行时行为不一致。
    """
    from src.workflows.review_routing import has_unresolved_blocking

    tracker = history.get("issue_tracker")
    # 复用真值表需要 AgentState 形状；此处只读地构造最小投影并 cast。
    state = cast("AgentState", {"issue_tracker": tracker} if isinstance(tracker, dict) else {})
    return {"candidate": bool(has_unresolved_blocking(state))}


def _replay_status(history: dict[str, Any]) -> dict[str, Any]:
    """按 spec_version 回放；缺快照或版本不存在时明确报 ``not_replayable``。

    判定委托给 ``history_store.replay_specification``——``not_replayable`` 的口径
    必须与运行时一致，此处重写一份只会在两处漂移。额外附上可用版本号，方便人工
    判断该补哪个快照。
    """
    raw_version = history.get("spec_version")
    # 损坏的 history 可能带非 int 版本；显式收敛而不是让类型错误冒泡。
    version = raw_version if isinstance(raw_version, int) else 0
    result = replay_specification(history, version)
    if result.get("status") == "ok":
        return {"status": "ok", "spec_version": raw_version}

    return {
        "status": NOT_REPLAYABLE,
        "reason": result.get("reason", "快照不可回放"),
        "available_versions": [
            item.get("spec_version")
            for item in (history.get("specification_snapshots") or [])
            if isinstance(item, dict)
        ],
    }


def evaluate_one(path: Path) -> dict[str, Any]:
    """评估单个 history 文件。读取失败不静默吞掉——返回显式错误。"""
    try:
        history = read_history(str(path))
    except (OSError, json.JSONDecodeError) as exc:
        return {"file": path.name, "status": "unreadable", "error": str(exc)}

    trace = list(history.get("laya_trace") or [])
    findings = history.get("laya_findings") or []
    routes = Counter(_route_of(item) for item in trace)
    degraded = sum(1 for item in trace if _is_degraded(item))

    return {
        "file": path.name,
        "status": "ok",
        "thread_id": history.get("thread_id"),
        "spec_version": history.get("spec_version"),
        "iteration_count": history.get("iteration_count"),
        "error_code": history.get("error_code"),
        "replay": _replay_status(history),
        "manual_review_entries": _manual_review_entries(history),
        "false_positive_negative": {
            # v1 无人工标签，无法自动算 FPR/FNR（§0.2）；给出对照入口。
            "status": NOT_REPLAYABLE if not findings else "needs_human_labels",
            "finding_count": len(findings),
            "note": "误报率/漏报率需人工填写 human_verdict 后统计（v1 无校准语料）",
        },
        "fix_rate": _fix_rate(history),
        "early_stop": _early_stop_candidates(history),
        "route_distribution": dict(routes),
        "degradation": {
            "degraded_decisions": degraded,
            "total_decisions": len(trace),
            "rate": (degraded / len(trace)) if trace else None,
        },
        "llm_cost": history.get("total_llm_cost", 0),
    }


def aggregate(reports: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """把逐文件报告聚合成整体指标。"""
    reports = list(reports)
    ok = [item for item in reports if item.get("status") == "ok"]

    total_cost = sum(float(item.get("llm_cost") or 0) for item in ok)
    routes: Counter[str] = Counter()
    degraded = 0
    decisions = 0
    candidates = 0
    not_replayable = 0
    findings = 0

    for item in ok:
        routes.update(item.get("route_distribution") or {})
        degradation = item.get("degradation") or {}
        degraded += int(degradation.get("degraded_decisions") or 0)
        decisions += int(degradation.get("total_decisions") or 0)
        if (item.get("early_stop") or {}).get("candidate"):
            candidates += 1
        if (item.get("replay") or {}).get("status") == NOT_REPLAYABLE:
            not_replayable += 1
        findings += int((item.get("false_positive_negative") or {}).get("finding_count") or 0)

    return {
        "history_files": len(reports),
        "readable": len(ok),
        "unreadable": len(reports) - len(ok),
        "not_replayable_histories": not_replayable,
        "findings_total": findings,
        "route_distribution": dict(routes),
        "degradation": {
            "degraded_decisions": degraded,
            "total_decisions": decisions,
            "rate": (degraded / decisions) if decisions else None,
        },
        "early_stop_candidate_rate": (candidates / len(ok)) if ok else None,
        "llm_cost_total": total_cost,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。**只读**：除可选 ``--output`` 外不写任何文件。"""
    parser = argparse.ArgumentParser(
        description="§9.5 决策评估：只读 history／specification snapshots，输出离线指标"
    )
    parser.add_argument(
        "--history-dir",
        default="reviews",
        help="history 目录（默认 reviews）",
    )
    parser.add_argument("--thread-id", default=None, help="只评估指定 thread 的 history")
    parser.add_argument("--output", default=None, help="可选：把聚合结果写入该 JSON 路径")
    args = parser.parse_args(argv)

    history_dir = Path(args.history_dir)
    if not history_dir.is_dir():
        print(f"history 目录不存在: {history_dir}", file=sys.stderr)
        return 2

    files = _iter_history_files(history_dir, args.thread_id)
    if not files:
        print(f"未找到 history 文件: {history_dir}", file=sys.stderr)
        return 1

    reports = [evaluate_one(path) for path in files]
    result = {"aggregate": aggregate(reports), "histories": reports}

    if args.output:
        # 唯一的写操作，且只写调用方显式指定的路径。
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
