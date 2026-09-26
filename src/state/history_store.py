"""history 落盘：thread_id、snapshots、per-thread lock、原子替换（规格 §12.2，T-16）。

修掉 Step 0 基线里三个已知缺陷：

1. **每次落盘重新生成 thread_id**——同一秒内两次落盘互相覆盖。改为入口生成一次
   并写回 state，后续 finalize 复用。
2. **普通覆盖写**——进程在写一半时崩溃会留下截断的 JSON。改为「同目录临时文件
   + fsync + ``os.replace``」，``os.replace`` 在同一文件系统上是原子的。
3. **无并发保护**——多进程同时 finalize 会交错写坏。改为 ``O_CREAT | O_EXCL``
   per-thread lock。

锁协议（§12.2 逐条落地）：

- 锁文件固定名 ``history-<thread_id>.json.lock``；
- owner 内容保存 ``pid`` / ``thread_id`` / ``created_at``；
- 争用者每 50ms 轮询，最多等 5s；
- **超时显式失败，绝不自动删除锁**——自动删锁会把「别人正在写」误判成「锁坏了」，
  然后两个写者同时进入临界区，比等待更糟；
- 正常获取者在 ``finally`` 删除**自己创建的**锁；陈旧锁不自动接管。
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..decisions.question_contracts import canonical_json_bytes

if TYPE_CHECKING:  # pragma: no cover
    from ..schemas.models import AgentState

__all__ = [
    "LEGACY_THREAD_ID_FIELDS",
    "LOCK_POLL_SECONDS",
    "LOCK_TIMEOUT_SECONDS",
    "HistoryLockTimeoutError",
    "LegacyIdCollisionError",
    "content_sha256",
    "legacy_thread_id",
    "build_specification_snapshots",
    "thread_lock",
    "atomic_write_json",
    "build_history_payload",
    "save_review_history",
    "read_history",
    "replay_specification",
]

#: §12.2 固定投影字段，顺序即 canonical JSON 键序。**刻意不含** `messages`、
#: 运行时对象、所有 `laya_*` 字段与报告全文——它们要么不可序列化，要么会随轮次
#: 变动，纳入会让同一份输入算出不同 ID。
LEGACY_THREAD_ID_FIELDS: tuple[str, ...] = (
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

#: 争用者轮询间隔与总等待上限（§12.2：50ms / 5s）。
LOCK_POLL_SECONDS = 0.05
LOCK_TIMEOUT_SECONDS = 5.0

#: 缺失的历史文件在回放时的哨兵值（§12.2：旧 history 缺 snapshots 仍可读取，
#: 但相关 audit 回放返回 ``not_replayable``）。
NOT_REPLAYABLE = "not_replayable"


class HistoryLockTimeoutError(RuntimeError):
    """等待 per-thread lock 超时。**不会**删除锁。"""


class LegacyIdCollisionError(RuntimeError):
    """legacy fallback 算出的 thread_id 已有对应 history，拒绝覆盖。"""


def content_sha256(content: str) -> str:
    """规格正文的 UTF-8 原始字节摘要（无 BOM、无换行规范化）。"""
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def legacy_thread_id(state: Mapping[str, Any]) -> str:
    """按稳定字段投影计算 legacy thread ID（``legacy-<16 hex>``）。

    仅在 state 与 resume config 都没有 ID 时使用。固定字段顺序构造 canonical
    JSON，取 SHA-256 前 16 位十六进制。
    """
    projection = {field: state.get(field) for field in LEGACY_THREAD_ID_FIELDS}
    digest = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    return f"legacy-{digest[:16]}"


def build_specification_snapshots(
    state: Mapping[str, Any],
    existing: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """构造去重后的 ``specification_snapshots``。

    每次 specification 更新后追加 ``{spec_version, content_sha256, content}``，
    按 ``(spec_version, content_sha256)`` 去重——同一版本同一内容重复落盘不应
    产生第二条。
    """
    snapshots: list[dict[str, Any]] = [
        dict(item) for item in (existing or []) if isinstance(item, Mapping)
    ]
    seen = {(item.get("spec_version"), item.get("content_sha256")) for item in snapshots}

    content = state.get("specification", "") or ""
    spec_version = state.get("spec_version", 1)
    entry = {
        "spec_version": spec_version,
        "content_sha256": content_sha256(content),
        "content": content,
    }
    key = (entry["spec_version"], entry["content_sha256"])
    if key not in seen:
        snapshots.append(entry)
    return snapshots


def _lock_path(history_path: str) -> str:
    """锁文件路径固定为 ``history-<thread_id>.json.lock``（§12.2）。"""
    return f"{history_path}.lock"


@contextmanager
def thread_lock(
    history_path: str,
    thread_id: str,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
    poll: float = LOCK_POLL_SECONDS,
) -> Iterator[str]:
    """获取 per-thread 排他锁；``finally`` 中只删除**自己创建的**锁。

    Args:
        history_path: 目标 history 文件路径。
        thread_id: 用于拼锁文件名的线程标识。
        timeout: 总等待上限（秒）。
        poll: 轮询间隔（秒）。

    Yields:
        锁文件路径。

    Raises:
        HistoryLockTimeoutError: 超时。**锁文件保持原样**，不做任何清理。
    """
    lock_path = _lock_path(history_path)
    owner = {
        "pid": os.getpid(),
        "thread_id": thread_id,
        # 锁 owner 时间戳带时区：跨机器排查陈旧锁时，裸本地时间无法比较。
        "created_at": datetime.now(UTC).isoformat(),
    }
    payload = json.dumps(owner, ensure_ascii=False, sort_keys=True).encode("utf-8")

    acquired = False
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if time.monotonic() >= deadline:
                # 绝不自动删除锁：删除等于宣告「持锁者已死」，而它可能只是慢。
                raise HistoryLockTimeoutError(
                    f"等待锁超时（{timeout}s）：{lock_path}；请确认持锁进程状态后手动清理"
                ) from None
            time.sleep(poll)
            continue
        except OSError as exc:  # pragma: no cover - 平台差异
            if exc.errno != errno.EEXIST:
                raise
            if time.monotonic() >= deadline:
                raise HistoryLockTimeoutError(f"等待锁超时（{timeout}s）：{lock_path}") from exc
            time.sleep(poll)
            continue

        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        acquired = True
        break

    try:
        yield lock_path
    finally:
        # 只删自己创建的锁；未获取成功时路径上根本没有我们的锁文件。
        if acquired:
            try:
                os.unlink(lock_path)
            except FileNotFoundError:  # pragma: no cover - 防御性
                pass


def atomic_write_json(path: str, payload: Mapping[str, Any]) -> None:
    """原子写 JSON：同目录临时文件 + fsync + ``os.replace``。

    临时文件必须与目标**同目录**，否则 ``os.replace`` 可能跨文件系统而退化为
    非原子的复制。
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # 失败不得留下临时文件。
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def build_history_payload(
    state: Mapping[str, Any],
    thread_id: str,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """组装 history 顶层载荷（§12.2 要求的键集）。

    保留 Step 0 基线的 6 个键（外部脚本可能依赖），并补齐 ``thread_id`` /
    ``specification_snapshots`` / ``report_markdown`` / ``laya_findings`` /
    ``laya_trace`` / ``iteration_count`` / ``error_code``。
    """
    return {
        "thread_id": thread_id,
        "spec_version": state.get("spec_version", 1),
        "specification": state.get("specification", "") or "",
        "review_conclusion": state.get("review_conclusion", "unknown"),
        "total_llm_cost": state.get("total_llm_cost", 0),
        "reports": state.get("review_reports", []),
        "specification_snapshots": build_specification_snapshots(
            state, (existing or {}).get("specification_snapshots")
        ),
        "report_markdown": state.get("report_markdown", "") or "",
        "laya_findings": state.get("laya_findings", []),
        "laya_trace": state.get("laya_trace", []),
        "iteration_count": state.get("iteration_count", 0),
        "error_code": state.get("error_code"),
    }


def save_review_history(
    state: AgentState,
    history_dir: str = "reviews",
) -> str:
    """落盘审查历史，返回最终 history 路径。

    流程：解析 thread_id（state → resume config → legacy fallback）→ per-thread
    lock → 读旧载荷合并 snapshots → 原子替换。

    Raises:
        HistoryLockTimeoutError: 锁等待超时。
        LegacyIdCollisionError: legacy fallback 算出的 ID 已有 history，拒绝覆盖。
    """
    # thread_id 只生成一次：优先复用 state 里已有的，其次 resume config，
    # 都没有才走 legacy fallback。
    #
    # `used_legacy_fallback` 记录本次是否**新算**出 legacy ID——碰撞守卫只对
    # fallback 生效。state 里已带 `legacy-` 前缀说明这是本线程上次落盘留下的
    # 身份，续写它正是 §12.2 要求的「追加 snapshot 并去重」，不是碰撞。
    used_legacy_fallback = False
    thread_id = state.get("thread_id") or ""
    if not thread_id:
        resume_config = state.get("resume_config") or {}
        if isinstance(resume_config, Mapping):
            thread_id = resume_config.get("thread_id") or ""
    if not thread_id:
        thread_id = legacy_thread_id(state)
        used_legacy_fallback = True
        # 写回 state，后续 finalize 复用同一 ID，不再重算。
        state["thread_id"] = thread_id

    os.makedirs(history_dir, exist_ok=True)
    history_path = os.path.join(history_dir, f"history-{thread_id}.json")

    # legacy fallback 新算出的 ID 撞已有 history 时必须失败且不覆盖：覆盖会静默
    # 毁掉另一次审查。
    if used_legacy_fallback and os.path.exists(history_path):
        raise LegacyIdCollisionError(f"legacy thread_id 目标已存在，拒绝覆盖：{history_path}")

    existing: Mapping[str, Any] | None = None
    if os.path.exists(history_path):
        try:
            existing = json.loads(open(history_path, encoding="utf-8").read())
        except (OSError, json.JSONDecodeError):
            existing = None

    payload = build_history_payload(state, thread_id, existing)

    with thread_lock(history_path, thread_id):
        atomic_write_json(history_path, payload)

    return history_path


def read_history(path: str) -> dict[str, Any]:
    """读取 history 文件。"""
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def replay_specification(history: Mapping[str, Any], spec_version: int) -> dict[str, Any]:
    """按 ``spec_version`` 回放规格快照。

    旧 history 缺 ``specification_snapshots`` 时返回 ``not_replayable``——**不**
    静默回落到 ``specification`` 字段，因为那是最后一次的正文，未必是该版本。
    """
    snapshots = history.get("specification_snapshots")
    if not snapshots:
        return {"status": NOT_REPLAYABLE, "reason": "缺少 specification_snapshots"}

    for snapshot in snapshots:
        if snapshot.get("spec_version") == spec_version:
            return {"status": "ok", "snapshot": dict(snapshot)}
    return {"status": NOT_REPLAYABLE, "reason": f"无 spec_version={spec_version} 的快照"}
