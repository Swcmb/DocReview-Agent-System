"""审查历史契约特征化测试 / Characterization Tests for the History Contract (T-00c)

固定三段历史行为，供后续任务（T-14 issue/report flow、T-19 归档等）作为判据：

1. `_prune_review_history()` —— Token 累积压缩：最近 2 轮完整保留，
   第 3 轮及更早替换为单行摘要（由 `evaluate_result` 在写入新报告后调用，
   `review_workflow.py:250`）。
2. `_save_review_history()` —— 落盘格式：6 个键、文件名
   `reviews/history-review-YYYYMMDD-HHMMSS.json`、缩进 2、保留中文。
3. `finalize()` —— 仅在有报告时落盘，并置 `execution_status = "completed"`。

**关键事实**：`thread_id` 由 `_save_review_history` **内部按当前时间生成**，
不取自 `state`。因此同一秒内连续两次落盘会互相覆盖——这是既有行为，
此处锁定现状，供 T-19 决定是否需要外部注入 thread_id。
"""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from src.schemas.models import AgentState, ReviewReport
from src.state.agent_state import create_initial_state
from src.workflows.review_workflow import (
    _prune_review_history,
    _save_review_history,
    finalize,
)

# ─────────────────────────── 夹具 ───────────────────────────

# T-16（§12.2）：保留 Step 0 的 6 个键，并补齐 thread_id / snapshots /
# report_markdown / laya_findings / laya_trace / iteration_count / error_code。
HISTORY_KEYS = {
    "thread_id",
    "spec_version",
    "specification",
    "review_conclusion",
    "total_llm_cost",
    "reports",
    "specification_snapshots",
    "report_markdown",
    "laya_findings",
    "laya_trace",
    "iteration_count",
    "error_code",
}


def _state(**overrides: Any) -> AgentState:
    return cast("AgentState", {**create_initial_state(), **overrides})


def _mk_report(iteration: int, severities: list[str] | None = None) -> ReviewReport:
    issues = [
        {
            "issue_id": f"MD-{iteration}-{i + 1}",
            "severity": sev,
            "issue_type": "ConsistencyCheck",
            "description": f"第 {iteration} 轮问题 {i + 1}",
            "suggestion": "修订",
            "location": f"第 {i + 1} 节",
            "status": "open",
        }
        for i, sev in enumerate(severities or [])
    ]
    return ReviewReport(
        iteration=iteration,
        timestamp="2026-09-26T00:00:00",
        review_conclusion="Fail",
        review_summary=f"第 {iteration} 轮完整摘要",
        issues=cast("Any", issues),
        highlights=[],
        open_questions=[],
        next_steps="",
    )


def _history_files(root: Path) -> list[Path]:
    # T-16：thread_id 既可能是入口生成的 `review-<时间戳>`，也可能是 legacy fallback
    # 的 `legacy-<16 hex>`，因此 glob 放宽到 `history-*.json`；`.lock` 与 `.tmp.*`
    # 不匹配该模式，无需额外排除。
    return sorted((root / "reviews").glob("history-*.json"))


# ─────────────── `_prune_review_history`：压缩策略 ───────────────


def test_prune_is_noop_for_two_or_fewer_reports():
    """不变量：≤2 轮时完全不改动（最近 2 轮必须完整保留）。"""
    state = _state(review_reports=[_mk_report(1, ["Blocking"]), _mk_report(2, ["High"])])
    before = json.dumps(state["review_reports"], ensure_ascii=False, sort_keys=True)

    _prune_review_history(state)

    assert json.dumps(state["review_reports"], ensure_ascii=False, sort_keys=True) == before


def test_prune_is_noop_for_empty_reports():
    """不变量：空报告列表不报错、不改动。"""
    state = _state(review_reports=[])
    _prune_review_history(state)
    assert state["review_reports"] == []


def test_prune_compresses_third_round_and_earlier():
    """不变量：3 轮时第 1 轮被压缩，第 2/3 轮保持完整。"""
    reports = [
        _mk_report(1, ["Blocking", "High", "Medium", "Low"]),
        _mk_report(2, ["High"]),
        _mk_report(3, ["Medium"]),
    ]
    state = _state(review_reports=reports)

    _prune_review_history(state)

    first, second, third = state["review_reports"]
    assert first["issues"] == [], "被压缩轮次清空 issues 以省 token"
    assert first["review_summary"] == "Fail | 1B/1H/1M/1L"
    assert second["issues"], "最近 2 轮中的第 2 轮必须保留"
    assert second["review_summary"] == "第 2 轮完整摘要"
    assert third["issues"], "最新一轮必须保留"
    assert third["review_summary"] == "第 3 轮完整摘要"


def test_prune_summary_counts_each_severity_bucket():
    """不变量：摘要格式固定为 `{结论} | {B}B/{H}H/{M}M/{L}L`，逐类计数。"""
    reports = [
        _mk_report(1, ["Blocking", "Blocking", "High", "Medium", "Low", "Low", "Low"]),
        _mk_report(2, []),
        _mk_report(3, []),
    ]
    state = _state(review_reports=reports)

    _prune_review_history(state)

    assert state["review_reports"][0]["review_summary"] == "Fail | 2B/1H/1M/3L"


def test_prune_summary_falls_back_to_unknown_conclusion():
    """不变量：缺失 `review_conclusion` 时摘要用 `Unknown`。"""
    # 直接构造缺键的普通 dict：ReviewReport 的必填键不允许 del
    report = {k: v for k, v in dict(_mk_report(1, ["High"])).items() if k != "review_conclusion"}
    state = _state(review_reports=[cast("Any", report), _mk_report(2, []), _mk_report(3, [])])

    _prune_review_history(state)

    assert state["review_reports"][0]["review_summary"] == "Unknown | 0B/1H/0M/0L"


def test_prune_handles_missing_issues_key():
    """不变量：报告缺少 `issues` 键时按空列表处理，不抛异常。"""
    # 直接构造缺键的普通 dict：ReviewReport 的必填键不允许 del
    report = {k: v for k, v in dict(_mk_report(1, ["Blocking"])).items() if k != "issues"}
    state = _state(review_reports=[cast("Any", report), _mk_report(2, []), _mk_report(3, [])])

    _prune_review_history(state)

    assert state["review_reports"][0]["issues"] == []
    assert state["review_reports"][0]["review_summary"] == "Fail | 0B/0H/0M/0L"


def test_prune_mutates_state_in_place():
    """不变量（**已知副作用**）：函数就地修改 `state["review_reports"]`，无返回值。"""
    reports = [_mk_report(1, ["High"]), _mk_report(2, []), _mk_report(3, [])]
    state = _state(review_reports=reports)

    assert _prune_review_history(state) is None
    assert reports[0]["issues"] == []


def test_prune_keeps_iteration_and_other_fields():
    """不变量：压缩只清 `issues` 并改写 `review_summary`，其余字段保留。"""
    report = _mk_report(7, ["Blocking", "High"])
    report["highlights"] = ["保留高亮"]
    report["next_steps"] = "后续动作"
    state = _state(review_reports=[report, _mk_report(8, []), _mk_report(9, [])])

    _prune_review_history(state)

    pruned = state["review_reports"][0]
    assert pruned["iteration"] == 7
    assert pruned["highlights"] == ["保留高亮"]
    assert pruned["next_steps"] == "后续动作"
    assert pruned["timestamp"] == "2026-09-26T00:00:00"


# ─────────────── `_save_review_history`：落盘契约 ───────────────


def test_save_writes_expected_file_and_keys(monkeypatch, tmp_path):
    """不变量：写出 `reviews/history-review-<时间戳>.json`，且顶层键集固定为 6 个。"""
    monkeypatch.chdir(tmp_path)
    state = _state(
        spec_version=3,
        specification="# 规格正文",
        review_conclusion="Conditional Pass",
        total_llm_cost=1.25,
        review_reports=[_mk_report(1, ["High"])],
    )

    _save_review_history(state)

    files = _history_files(tmp_path)
    assert len(files) == 1, "应恰好写出一个历史文件"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert set(payload) == HISTORY_KEYS
    assert payload["spec_version"] == 3
    assert payload["specification"] == "# 规格正文", "Step 0 基线：必须落盘 specification"
    assert payload["review_conclusion"] == "Conditional Pass"
    assert payload["total_llm_cost"] == 1.25
    assert len(payload["reports"]) == 1


def test_save_embeds_thread_id_from_state_when_present(monkeypatch, tmp_path):
    """T-16：state 已有 thread_id 时必须复用，文件名与该 ID 一致。"""
    monkeypatch.chdir(tmp_path)
    _save_review_history(
        _state(review_reports=[_mk_report(1)], thread_id="review-20260101-000000")
    )

    path = _history_files(tmp_path)[0]
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "history-review-20260101-000000.json"
    assert payload["thread_id"] == "review-20260101-000000"


def test_save_reuses_same_thread_id_across_repeated_saves(monkeypatch, tmp_path):
    """T-16 修掉 Step 0 缺陷：thread_id 只生成一次，重复落盘不再互相覆盖。

    旧实现每次按秒重算 ID，同一秒内两次落盘会写进同一个文件——后写的覆盖先写的。
    """
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[_mk_report(1)])

    _save_review_history(state)
    first = _history_files(tmp_path)[0]
    _save_review_history(state)

    files = _history_files(tmp_path)
    assert len(files) == 1, "同一 thread 重复落盘必须命中同一文件，不得产生第二个 history"
    assert files[0] == first
    assert state["thread_id"].startswith("legacy-"), "缺 ID 时走 legacy fallback 并写回 state"


def test_save_falls_back_to_legacy_id_without_state_thread_id(monkeypatch, tmp_path):
    """T-16：state 与 resume config 都无 ID 时，legacy fallback 产出 `legacy-<16 hex>`。"""
    monkeypatch.chdir(tmp_path)
    _save_review_history(_state(review_reports=[_mk_report(1)]))

    path = _history_files(tmp_path)[0]
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == f"history-{payload['thread_id']}.json"
    thread_id = payload["thread_id"]
    assert thread_id.startswith("legacy-")
    assert len(thread_id) == len("legacy-") + 16


def test_save_prefers_resume_config_thread_id_over_legacy(monkeypatch, tmp_path):
    """T-16：resume config 里的 ID 优先于 legacy fallback。"""
    monkeypatch.chdir(tmp_path)
    _save_review_history(
        _state(review_reports=[_mk_report(1)], resume_config={"thread_id": "resumed-42"})
    )

    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["thread_id"] == "resumed-42"


def test_save_applies_defaults_for_missing_keys(monkeypatch, tmp_path):
    """不变量：state 缺字段时按固定缺省值落盘。"""
    monkeypatch.chdir(tmp_path)
    bare: dict[str, Any] = {"review_reports": [_mk_report(1)]}

    _save_review_history(cast("AgentState", bare))

    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["spec_version"] == 1
    assert payload["specification"] == ""
    assert payload["review_conclusion"] == "unknown"
    assert payload["total_llm_cost"] == 0


def test_save_preserves_chinese_and_indent(monkeypatch, tmp_path):
    """不变量：落盘为 `ensure_ascii=False` + `indent=2`，中文不转义。"""
    monkeypatch.chdir(tmp_path)
    _save_review_history(
        _state(review_reports=[_mk_report(1)], specification="# 中文规格")
    )

    raw = _history_files(tmp_path)[0].read_text(encoding="utf-8")
    assert "中文规格" in raw, "ensure_ascii=False，中文须原样写入"
    assert '\\u' not in raw, "不得出现 \\uXXXX 转义"
    assert '\n  "thread_id"' in raw, "indent=2"


def test_save_creates_reviews_dir(monkeypatch, tmp_path):
    """不变量：`reviews/` 不存在时自动创建。"""
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "reviews").exists()

    _save_review_history(_state(review_reports=[_mk_report(1)]))

    assert (tmp_path / "reviews").is_dir()


def test_save_swallows_errors(monkeypatch, tmp_path):
    """不变量：落盘失败只记日志、不抛出（`review_workflow.py:472-473`）。"""
    monkeypatch.chdir(tmp_path)
    # 让 reviews 成为普通文件，使 os.makedirs 必然失败
    (tmp_path / "reviews").write_text("not a directory", encoding="utf-8")

    _save_review_history(_state(review_reports=[_mk_report(1)]))  # 不应抛异常


def test_save_with_no_reports_still_writes(monkeypatch, tmp_path):
    """不变量：`_save_review_history` 自身不校验 reports 非空（守卫在 `finalize`）。"""
    monkeypatch.chdir(tmp_path)
    _save_review_history(_state(review_reports=[]))

    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["reports"] == []


# ─────────────── `finalize`：调用顺序与守卫 ───────────────


async def test_finalize_saves_history_when_reports_present(monkeypatch, tmp_path):
    """不变量：有报告时落盘并置 `execution_status = "completed"`。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[_mk_report(1, ["High"])], review_conclusion="Pass")

    out = await finalize(state)

    assert out["execution_status"] == "completed"
    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["review_conclusion"] == "Pass"


async def test_finalize_skips_save_without_reports(monkeypatch, tmp_path):
    """不变量：无报告时**不落盘**（`review_workflow.py:366-367` 的守卫），但仍置 completed。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[])

    out = await finalize(state)

    assert out["execution_status"] == "completed"
    assert _history_files(tmp_path) == []


async def test_finalize_returns_same_state_object(monkeypatch, tmp_path):
    """不变量：`finalize` 就地修改并返回同一 state 对象。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[_mk_report(1)])

    out = await finalize(state)

    assert out is state


# ─────────────── 与 CLI `status` 读取端的契约一致性 ───────────────


def test_saved_payload_is_consumable_by_cli_status(monkeypatch, tmp_path):
    """不变量：落盘键集与 `main.status` 读取的键一一对应（`main.py:256-260/277-280`）。

    防止后续重构只改一端：写入端加了键、或读取端改了键名而此处未同步。
    """
    monkeypatch.chdir(tmp_path)
    _save_review_history(
        _state(
            spec_version=5,
            review_conclusion="Fail",
            total_llm_cost=2.5,
            review_reports=[_mk_report(1, ["Blocking"])],
        )
    )
    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))

    # CLI 列表与详情两处读取的键
    for key in ("thread_id", "spec_version", "review_conclusion", "total_llm_cost", "reports"):
        assert key in payload, f"CLI status 依赖的键 {key} 缺失"

    # 详情视图还读 reports 长度，列表视图读 thread_id
    assert isinstance(payload["reports"], list)
    assert payload["thread_id"]
    # 成本需可按 float 格式化（`${...:.4f}`）
    assert isinstance(payload["total_llm_cost"], float)


def test_reports_are_persisted_verbatim(monkeypatch, tmp_path):
    """不变量：`reports` 原样保留完整报告结构（未压缩时含全部 issue 字段）。"""
    monkeypatch.chdir(tmp_path)
    report = _mk_report(1, ["Blocking", "Low"])
    _save_review_history(_state(review_reports=[report]))

    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert payload["reports"][0] == dict(report)
    assert payload["reports"][0]["issues"][0]["issue_id"] == "MD-1-1"


@pytest.mark.parametrize("reports_len", [1, 3, 7])
def test_history_round_trip_is_json_serializable(monkeypatch, tmp_path, reports_len):
    """不变量：任意轮次数的历史都能完整 JSON 往返（缩进与中文不破坏结构）。"""
    monkeypatch.chdir(tmp_path)
    reports = [_mk_report(i, ["Medium"]) for i in range(1, reports_len + 1)]

    _save_review_history(_state(review_reports=reports))

    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    assert len(payload["reports"]) == reports_len
    assert [r["iteration"] for r in payload["reports"]] == list(range(1, reports_len + 1))


def test_prune_then_save_keeps_summary_only_for_old_rounds(monkeypatch, tmp_path):
    """不变量：先压缩再落盘时，旧轮次以摘要形式持久化（这是归档的真实形态）。"""
    monkeypatch.chdir(tmp_path)
    reports = [
        _mk_report(1, ["Blocking", "High"]),
        _mk_report(2, ["Medium"]),
        _mk_report(3, ["Low"]),
        _mk_report(4, ["Blocking"]),
    ]
    state = _state(review_reports=reports)

    _prune_review_history(state)
    _save_review_history(state)

    payload = json.loads(_history_files(tmp_path)[0].read_text(encoding="utf-8"))
    saved = payload["reports"]
    assert saved[0]["issues"] == []
    assert saved[0]["review_summary"] == "Fail | 1B/1H/0M/0L"
    assert saved[1]["issues"] == [], "第 2 轮也属「第 3 轮及更早」范围"
    assert saved[2]["issues"], "第 3 轮是保留窗口内"
    assert saved[3]["issues"], "最新一轮必须完整"
    assert Path(tmp_path / "reviews").is_dir()
