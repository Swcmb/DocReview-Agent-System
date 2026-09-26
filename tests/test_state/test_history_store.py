"""T-16 history 落盘契约测试（规格 §12.2）。

覆盖三条主线：

1. **per-thread lock** —— ``O_CREAT|O_EXCL``、owner 字段、50ms/5s 常量、超时显式
   失败且**不删锁**、正常获取者 ``finally`` 只删自己的锁。
2. **原子写 + snapshots** —— 同目录临时文件 + ``os.replace``、无临时文件残留、
   snapshot 按 ``(spec_version, content_sha256)`` 去重。
3. **thread_id / legacy fallback** —— 入口 ID 复用、resume config 优先、
   ``legacy-<16 hex>`` 稳定性与字段排除、碰撞拒绝覆盖、``not_replayable`` 回放。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from src.schemas.models import AgentState
from src.state.agent_state import create_initial_state
from src.state.history_store import (
    LEGACY_THREAD_ID_FIELDS,
    LOCK_POLL_SECONDS,
    LOCK_TIMEOUT_SECONDS,
    HistoryLockTimeoutError,
    LegacyIdCollisionError,
    atomic_write_json,
    build_specification_snapshots,
    content_sha256,
    legacy_thread_id,
    read_history,
    replay_specification,
    save_review_history,
    thread_lock,
)

# ─────────────────────────── 夹具 ───────────────────────────


def _state(**overrides: Any) -> AgentState:
    return cast("AgentState", {**create_initial_state(), **overrides})


@pytest.fixture
def history_dir(tmp_path: Path) -> Iterator[Path]:
    path = tmp_path / "reviews"
    path.mkdir()
    yield path


# ─────────────────────── 锁协议（§12.2） ───────────────────────


def test_lock_constants_match_spec():
    """规格固定：争用者每 50ms 轮询，最多等 5s。"""
    assert LOCK_POLL_SECONDS == 0.05
    assert LOCK_TIMEOUT_SECONDS == 5.0


def test_lock_uses_o_excl_and_writes_owner_fields(history_dir: Path):
    """锁用 O_CREAT|O_EXCL 创建，owner 内容保存 pid / thread_id / created_at。"""
    target = str(history_dir / "history-t1.json")

    with thread_lock(target, "t1") as lock_path:
        assert Path(lock_path).exists(), "持有期间锁文件必须存在"
        assert Path(lock_path).name == "history-t1.json.lock", "锁名固定为 history-<id>.json.lock"
        owner = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["thread_id"] == "t1"
        assert owner["created_at"], "created_at 不得为空"


def test_lock_is_removed_after_normal_exit(history_dir: Path):
    """正常获取者在 finally 删除自己创建的锁。"""
    target = str(history_dir / "history-t1.json")

    with thread_lock(target, "t1") as lock_path:
        pass

    assert not Path(lock_path).exists(), "正常退出后必须清理自己的锁"


def test_lock_is_removed_even_when_body_raises(history_dir: Path):
    """临界区抛异常时锁仍须清理，否则一次崩溃会永久锁死该 thread。"""
    target = str(history_dir / "history-t1.json")
    # 锁名由 target 唯一决定，直接推导而不依赖 with-as 绑定。
    lock_path = history_dir / "history-t1.json.lock"

    with pytest.raises(ValueError):
        with thread_lock(target, "t1"):
            raise ValueError("boom")

    assert not lock_path.exists()


def test_lock_timeout_raises_and_never_deletes_lock(history_dir: Path):
    """超时显式失败，且**绝不自动删除锁**。

    自动删锁等于宣告「持锁者已死」——而它可能只是慢。删了就会让两个写者同时
    进入临界区，比等待更糟。
    """
    target = str(history_dir / "history-t1.json")
    lock_path = history_dir / "history-t1.json.lock"

    with thread_lock(target, "t1"):
        # 第二个获取者必然拿不到锁。
        with pytest.raises(HistoryLockTimeoutError):
            with thread_lock(target, "t1", timeout=0.1, poll=0.01):
                pass

        # 必须在持锁者仍持有期间断言：外层 with 退出后它会正常清理自己的锁，
        # 那时锁不存在是正确的，不是缺陷。
        assert lock_path.exists(), "超时方不得删除持锁者留下的锁"


def test_lock_is_reusable_after_release(history_dir: Path):
    """前一个持有者释放后，后一个获取者应能正常取得锁。"""
    target = str(history_dir / "history-t1.json")

    with thread_lock(target, "t1"):
        pass
    with thread_lock(target, "t1", timeout=0.2, poll=0.01):
        pass  # 未抛异常即成功


# ─────────────────────── 原子写（§12.2） ───────────────────────


def test_atomic_write_creates_valid_json_and_leaves_no_temp(history_dir: Path):
    """原子写：产出合法 JSON，且不留临时文件。"""
    target = history_dir / "out.json"
    payload = {"thread_id": "t1", "note": "中文"}

    atomic_write_json(str(target), payload)

    assert read_history(str(target)) == payload
    leftovers = list(history_dir.glob("out.json.tmp.*"))
    assert not leftovers, f"不得残留临时文件: {leftovers}"


def test_atomic_write_preserves_chinese_and_indent(history_dir: Path):
    """沿用 Step 0 基线：ensure_ascii=False + indent=2。"""
    target = history_dir / "out.json"
    atomic_write_json(str(target), {"note": "中文规格"})

    raw = target.read_text(encoding="utf-8")
    assert "中文规格" in raw
    assert "\\u" not in raw
    assert '\n  "note"' in raw


def test_atomic_write_cleans_temp_file_on_serializer_failure(history_dir: Path):
    """序列化失败不得留下临时文件。"""
    target = history_dir / "out.json"

    with pytest.raises(TypeError):
        atomic_write_json(str(target), {"bad": object()})

    assert not target.exists()
    assert not list(history_dir.glob("out.json.tmp.*"))


def test_atomic_write_overwrites_existing_file(history_dir: Path):
    """原子替换必须能覆盖既有文件（重复 finalize 的正常路径）。"""
    target = history_dir / "out.json"
    atomic_write_json(str(target), {"round": 1})
    atomic_write_json(str(target), {"round": 2})

    assert read_history(str(target)) == {"round": 2}


# ─────────────────────── snapshots 去重（§12.2） ───────────────────────


def test_snapshot_shape_and_sha256():
    """snapshot 形状固定为 {spec_version, content_sha256, content}。"""
    snapshots = build_specification_snapshots(_state(spec_version=2, specification="# 正文"))
    assert len(snapshots) == 1

    snapshot = snapshots[0]
    assert set(snapshot) == {"spec_version", "content_sha256", "content"}
    assert snapshot["spec_version"] == 2
    assert snapshot["content"] == "# 正文"
    assert snapshot["content_sha256"] == content_sha256("# 正文")


def test_snapshot_dedupes_same_version_and_content():
    """同版本同内容重复落盘不得产生第二条。"""
    state = _state(spec_version=1, specification="# 正文")
    first = build_specification_snapshots(state)
    second = build_specification_snapshots(state, existing=first)

    assert len(second) == 1, "同 (spec_version, content_sha256) 必须去重"


def test_snapshot_appends_when_spec_version_changes():
    """specification 更新后追加新快照，保留历史版本。"""
    state = _state(spec_version=1, specification="# v1")
    first = build_specification_snapshots(state)

    state["spec_version"] = 2
    state["specification"] = "# v2"
    second = build_specification_snapshots(state, existing=first)

    assert [item["spec_version"] for item in second] == [1, 2]


def test_snapshot_appends_when_content_changes_within_same_version():
    """同版本但内容变了也算新快照——去重键是 (版本, 内容摘要) 二元组。"""
    state = _state(spec_version=1, specification="# v1")
    first = build_specification_snapshots(state)

    state["specification"] = "# v1 修订"
    second = build_specification_snapshots(state, existing=first)

    assert len(second) == 2, "同版本不同内容必须各自保留"


# ─────────────── thread_id 与 legacy fallback（§12.2） ───────────────


def test_legacy_thread_id_is_deterministic_and_prefixed():
    """legacy ID 形状固定为 legacy-<16 hex>，且对同一输入稳定。"""
    state = _state(user_task="评审 PRD")
    thread_id = legacy_thread_id(state)

    assert thread_id == legacy_thread_id(state), "同一输入必须算出同一 ID"
    assert thread_id.startswith("legacy-")
    assert len(thread_id) == len("legacy-") + 16


def test_legacy_thread_id_excludes_volatile_fields():
    """messages / laya_* / 报告全文不得影响 legacy ID。"""
    base = _state(user_task="评审 PRD", spec_version=1)
    expected = legacy_thread_id(base)

    noisy = _state(
        user_task="评审 PRD",
        spec_version=1,
        messages=[{"role": "user", "content": "x" * 100}],
        laya_findings=[{"issue_id": "A-1-1"}],
        laya_trace=[{"route": "route_after_evaluate"}],
        review_reports=[{"iteration": 1, "issues": [{"issue_id": "A-1-1"}]}],
    )

    assert legacy_thread_id(noisy) == expected, "易变字段不得进入 ID 投影"


def test_legacy_thread_id_uses_declared_field_order():
    """投影字段集合与顺序由 LEGACY_THREAD_ID_FIELDS 固定。"""
    assert LEGACY_THREAD_ID_FIELDS == (
        "user_task",
        "document_path",
        "document_content_sha256",
        "specification_sha256",
        "spec_version",
        "max_iterations",
        "stagnation_threshold",
        "user_approved",
        "execution_status",
        "error_code",
    )


def test_legacy_thread_id_changes_with_stable_field():
    """稳定字段变化必须改变 ID。"""
    assert legacy_thread_id(_state(user_task="A")) != legacy_thread_id(_state(user_task="B"))


def test_save_review_history_writes_state_thread_id(tmp_path: Path, monkeypatch):
    """state 已有 thread_id 时直接复用，并写回同一文件名。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[], thread_id="review-20260101-000000")

    path = save_review_history(state, history_dir="reviews")

    assert Path(path).name == "history-review-20260101-000000.json"
    assert read_history(path)["thread_id"] == "review-20260101-000000"


def test_save_review_history_prefers_resume_config_over_legacy(tmp_path: Path, monkeypatch):
    """resume config 的 ID 优先于 legacy fallback。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[], resume_config={"thread_id": "resumed-42"})

    path = save_review_history(state, history_dir="reviews")

    assert read_history(path)["thread_id"] == "resumed-42"


def test_save_review_history_writes_legacy_id_back_to_state(tmp_path: Path, monkeypatch):
    """缺 ID 时走 legacy fallback，并把结果写回 state 供后续复用。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[])

    path = save_review_history(state, history_dir="reviews")

    assert state["thread_id"].startswith("legacy-")
    assert read_history(path)["thread_id"] == state["thread_id"]


def test_save_review_history_is_idempotent_across_calls(tmp_path: Path, monkeypatch):
    """修掉 Step 0 缺陷：同一 state 重复落盘不再互相覆盖／分裂成两个文件。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[])

    first = save_review_history(state, history_dir="reviews")
    second = save_review_history(state, history_dir="reviews")

    assert first == second
    assert len(list((tmp_path / "reviews").glob("history-*.json"))) == 1


def test_legacy_collision_refuses_to_overwrite(tmp_path: Path, monkeypatch):
    """legacy fallback 撞已有 history 必须失败且不覆盖。

    覆盖会静默毁掉另一次审查的结果——宁可显式失败。
    """
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[])

    first = save_review_history(state, history_dir="reviews")
    original = read_history(first)
    original["marker"] = "第一次审查的成果"
    atomic_write_json(first, original)

    with pytest.raises(LegacyIdCollisionError):
        save_review_history(_state(review_reports=[]), history_dir="reviews")

    assert read_history(first)["marker"] == "第一次审查的成果", "既有 history 不得被覆盖"


def test_save_review_history_leaves_no_lock_behind(tmp_path: Path, monkeypatch):
    """正常落盘后不得残留锁文件。"""
    monkeypatch.chdir(tmp_path)
    save_review_history(_state(review_reports=[], thread_id="t1"), history_dir="reviews")

    assert not list((tmp_path / "reviews").glob("*.lock"))


def test_save_review_history_requires_t16_top_level_keys(tmp_path: Path, monkeypatch):
    """§12.2：顶层至少保存 thread_id / snapshots / report_markdown / laya_* / iteration_count / error_code。"""
    monkeypatch.chdir(tmp_path)
    state = _state(
        review_reports=[],
        thread_id="t1",
        report_markdown="# 报告",
        laya_findings=[{"issue_id": "A-1-1"}],
        laya_trace=[{"route": "route_after_evaluate"}],
        iteration_count=3,
        error_code=None,
    )

    payload = read_history(save_review_history(state, history_dir="reviews"))

    for key in (
        "thread_id",
        "specification_snapshots",
        "report_markdown",
        "laya_findings",
        "laya_trace",
        "iteration_count",
        "error_code",
    ):
        assert key in payload, f"§12.2 要求顶层含 {key}"

    assert payload["report_markdown"] == "# 报告"
    assert payload["iteration_count"] == 3
    assert len(payload["specification_snapshots"]) == 1


# ─────────────────────── 回放（§12.2） ───────────────────────


def test_replay_returns_snapshot_for_known_version(tmp_path: Path, monkeypatch):
    """有对应快照时正常回放。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[], thread_id="t1", spec_version=2, specification="# v2")
    path = save_review_history(state, history_dir="reviews")

    result = replay_specification(read_history(path), 2)

    assert result["status"] == "ok"
    assert result["snapshot"]["content"] == "# v2"


def test_replay_is_not_replayable_without_snapshots():
    """旧 history 缺 snapshots 仍可读取，但回放必须明确 not_replayable。"""
    legacy: dict[str, Any] = {"thread_id": "t1", "specification": "# 正文"}

    result = replay_specification(legacy, 1)

    assert result["status"] == "not_replayable"
    assert "specification_snapshots" in result["reason"]


def test_replay_is_not_replayable_for_unknown_version(tmp_path: Path, monkeypatch):
    """版本不在快照中时明确 not_replayable，且不回落到最后一次正文。"""
    monkeypatch.chdir(tmp_path)
    state = _state(review_reports=[], thread_id="t1", spec_version=1, specification="# v1")
    path = save_review_history(state, history_dir="reviews")

    result = replay_specification(read_history(path), 99)

    assert result["status"] == "not_replayable"
    assert "99" in result["reason"]


def test_old_history_without_snapshots_is_still_readable(tmp_path: Path):
    """§12.2：旧 history 缺 snapshots 仍可读取（读取与回放是两件事）。"""
    target = tmp_path / "history-old.json"
    atomic_write_json(str(target), {"thread_id": "t1", "specification": "# 正文"})

    assert read_history(str(target))["thread_id"] == "t1"
