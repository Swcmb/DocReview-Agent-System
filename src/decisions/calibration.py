"""§9.1 校准 artifact schema 与 labels/inferences 严格一一 join。

两个 artifact（``labels.jsonl`` / ``inferences.jsonl``）是独立受控文件，按
``sample_id`` **精确**一一 join，然后逐字段比较 identity。**任何** join 缺陷都
必须立即失败——不得按最近邻、时间或文本相似度补 join（§9.1）。

本模块只做「读 + 校验 + join」。阈值拟合、指标、ECE 属 T-10 的
``scripts/calibrate_laya.py``。

设计要点：
- **受控样本库根由调用方显式传入**，不从配置猜、不从 ``state_ref`` 推导。
  自己声明「什么是受控」等于没有受控边界。
- 全部失败路径收敛到一个 ``CalibrationError``，带稳定 ``code``；上层脚本据此
  映射退出码，本模块不自行 ``sys.exit``。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final, Literal, TypedDict

from src.decisions.question_contracts import FROZEN_BUSINESS_QUESTIONS

__all__ = [
    "CalibrationError",
    "CalibrationErrorCode",
    "LabelSample",
    "InferenceSample",
    "JoinedSample",
    "source_doc_dedup_key",
    "file_sha256",
    "parse_label",
    "parse_inference",
    "read_jsonl",
    "validate_human_label",
    "join_samples",
    "load_and_join",
    "main",
]

SCHEMA_VERSION: Final = 1

#: 两侧都必须逐字节相等的 identity 字段（§9.1）。任一不同即 identity mismatch。
IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "primitive",
    "question_id",
    "question_schema_hash",
    "model",
    "checkpoint_id",
    "checkpoint_manifest_sha256",
    "laya_runtime_commit",
    "runtime_source_digest",
    "state_digest",
)

CalibrationErrorCode = Literal[
    "labels_duplicate_sample_id",
    "inferences_duplicate_sample_id",
    "labels_empty",
    "inferences_empty",
    "join_extra_label",
    "join_extra_inference",
    "identity_mismatch",
    "state_ref_mismatch",
    "state_digest_mismatch",
    "state_ref_not_absolute",
    "state_ref_escapes_store",
    "state_ref_not_a_file",
    "state_file_digest_mismatch",
    "schema_version_unsupported",
    "missing_field",
    "human_label_type_mismatch",
    "artifact_unreadable",
]

#: 需要非零退出码的 code 全集。测试据此断言「失败即非零」，不逐个写 exit 1。
FAILING_CODES: Final[frozenset[str]] = frozenset(
    {
        "labels_duplicate_sample_id",
        "inferences_duplicate_sample_id",
        "labels_empty",
        "inferences_empty",
        "join_extra_label",
        "join_extra_inference",
        "identity_mismatch",
        "state_ref_mismatch",
        "state_digest_mismatch",
        "state_ref_not_absolute",
        "state_ref_escapes_store",
        "state_ref_not_a_file",
        "state_file_digest_mismatch",
        "schema_version_unsupported",
        "missing_field",
        "human_label_type_mismatch",
        "artifact_unreadable",
    }
)


class CalibrationError(Exception):
    """校准 artifact 校验失败。``code`` 稳定，供上层映射退出码/审计。"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class _LabelRequired(TypedDict):
    """``parse_label`` 强制存在的字段（§9.1）。"""

    schema_version: int
    sample_id: str
    primitive: str
    question_id: str
    question_schema_hash: str
    model: str
    checkpoint_id: str
    checkpoint_manifest_sha256: str
    laya_runtime_commit: str
    runtime_source_digest: str
    state_ref: dict[str, str]
    state_digest: str
    human_label: Any
    source_doc_dedup_key: str
    split: str


class LabelSample(_LabelRequired, total=False):
    """label sample；仅以下 provenance 字段为可选。"""

    label_source: str
    label_version: str


class _InferenceRequired(TypedDict):
    """``parse_inference`` 强制存在的字段（§9.1）。"""

    schema_version: int
    sample_id: str
    primitive: str
    question_id: str
    question_schema_hash: str
    model: str
    checkpoint_id: str
    checkpoint_manifest_sha256: str
    laya_runtime_commit: str
    runtime_source_digest: str
    state_ref: dict[str, str]
    state_digest: str
    raw_answer: dict[str, Any]
    split: str


class InferenceSample(_InferenceRequired, total=False):
    """inference sample；模型输出与计量字段为可选。"""

    probabilities: dict[str, float]
    answer_confidence: float
    usage: dict[str, Any]
    actual_model: str
    actual_device: str


class JoinedSample(TypedDict):
    sample_id: str
    label: LabelSample
    inference: InferenceSample


# ---------------------------------------------------------------------------
# §9.1 source_doc_dedup_key 规范化
# ---------------------------------------------------------------------------
def source_doc_dedup_key(text: str) -> str:
    """原始文档去重键（§9.1）。

    规范化算法（§9.1）：Unicode NFC；CRLF/CR 转 LF；删除每行末尾空格；
    保留段内其他字符与空行；再以 UTF-8 做 SHA-256。

    两处规格未明示、此处已定的口径（均有测试锁定）：
    1. **行终止符不算内容**。末尾换行会被剥掉。规格只说「CRLF/CR 转 LF」，若
       保留末尾换行，同一逻辑文档会因 checkout 的换行风格或末尾换行有无而得到
       两个不同的键，「同一原始文档的片段不得跨 fit/validation」这条约束就有洞
       可钻。段内空行仍保留，故 ``"a\\n\\nb"`` 不受影响。
    2. ``rstrip()`` 剥除全部行尾空白（含 tab），比规格字面的「行末空格」略宽；
       同样服务于换行风格不变性，且比只剥空格更不易撞键。
    """
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    stripped = "\n".join(line.rstrip() for line in normalized.split("\n"))
    # 末尾换行是终止符而非内容；只剥末尾，段内空行保留。
    canonical = stripped.rstrip("\n")
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """以 UTF-8 原始字节计算 sha256（§9.1）。

    刻意**不**做任何换行/编码规范化：``state_ref.sha256`` 钉的是文件真实字节。
    """
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# state_ref 校验
# ---------------------------------------------------------------------------
def _validate_state_ref(
    sample: Mapping[str, Any], side: str, store_root: Path
) -> Path:
    """校验 ``state_ref`` 并返回已解析路径（§9.1）。

    ``path`` 必须是受控样本库内的**绝对**路径：不得是 URL、仓库相对路径或
    任意用户输入。解析后再按原始字节核对 ``sha256``。
    """
    state_ref = sample.get("state_ref")
    if not isinstance(state_ref, dict):
        raise CalibrationError("missing_field", f"{side} {sample.get('sample_id')!r} 缺 state_ref")
    raw_path = state_ref.get("path")
    raw_digest = state_ref.get("sha256")
    if not isinstance(raw_path, str) or not isinstance(raw_digest, str):
        raise CalibrationError("missing_field", f"{side} {sample.get('sample_id')!r} 的 state_ref 不完整")

    if "://" in raw_path:
        raise CalibrationError("state_ref_not_absolute", f"{side} state_ref.path 不得是 URL：{raw_path}")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        raise CalibrationError("state_ref_not_absolute", f"{side} state_ref.path 必须是绝对路径：{raw_path}")

    # 防目录穿越：解析后必须仍在受控样本库内。
    try:
        resolved = candidate.resolve()
        root = store_root.resolve()
    except OSError as exc:  # pragma: no cover - 平台相关
        raise CalibrationError("state_ref_not_a_file", f"{side} state_ref.path 无法解析：{raw_path}（{exc}）") from exc
    if resolved != root and root not in resolved.parents:
        raise CalibrationError(
            "state_ref_escapes_store",
            f"{side} state_ref.path 逃出受控样本库 {root}：{resolved}",
        )
    if not resolved.is_file():
        raise CalibrationError("state_ref_not_a_file", f"{side} state_ref.path 不是文件：{resolved}")

    actual = file_sha256(resolved)
    if actual != raw_digest:
        raise CalibrationError(
            "state_file_digest_mismatch",
            f"{side} {sample.get('sample_id')!r} state 文件摘要不符：声明 {raw_digest}，实际 {actual}",
        )
    return resolved


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
_LABEL_REQUIRED: Final[tuple[str, ...]] = (
    "sample_id",
    "primitive",
    "question_id",
    "question_schema_hash",
    "state_digest",
    "human_label",
    "source_doc_dedup_key",
    "split",
    *IDENTITY_FIELDS,
    "state_ref",
)
_INFERENCE_REQUIRED: Final[tuple[str, ...]] = (
    "sample_id",
    "state_digest",
    "split",
    "raw_answer",
    *IDENTITY_FIELDS,
    "state_ref",
)


def _check_required(sample: Mapping[str, Any], required: Sequence[str], side: str) -> None:
    version = sample.get("schema_version")
    if version != SCHEMA_VERSION:
        raise CalibrationError(
            "schema_version_unsupported",
            f"{side} {sample.get('sample_id')!r} 的 schema_version={version!r}，期望 {SCHEMA_VERSION}",
        )
    missing = [key for key in required if key not in sample]
    if missing:
        raise CalibrationError(
            "missing_field", f"{side} {sample.get('sample_id')!r} 缺字段：{sorted(missing)}"
        )


def parse_label(raw: Mapping[str, Any]) -> LabelSample:
    _check_required(raw, _LABEL_REQUIRED, "labels")
    return dict(raw)  # type: ignore[return-value]


def parse_inference(raw: Mapping[str, Any]) -> InferenceSample:
    _check_required(raw, _INFERENCE_REQUIRED, "inferences")
    return dict(raw)  # type: ignore[return-value]


def read_jsonl(path: Path, side: str) -> list[dict[str, Any]]:
    """读 JSONL。空文件即失败——空 artifact 不可能 join 出任何东西。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationError("artifact_unreadable", f"{side} 无法读取 {path}：{exc}") from exc
    if text.startswith("﻿"):
        raise CalibrationError("artifact_unreadable", f"{side} {path} 含 BOM，必须为无 BOM UTF-8")
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError("artifact_unreadable", f"{side} {path}:{lineno} JSON 非法：{exc}") from exc
        if not isinstance(record, dict):
            raise CalibrationError("artifact_unreadable", f"{side} {path}:{lineno} 不是 JSON object")
        records.append(record)
    return records


def _index_unique(
    samples: Iterable[Mapping[str, Any]], side: str, code: str
) -> dict[str, Mapping[str, Any]]:
    """按 ``sample_id`` 建索引并拒绝重复。

    重复必须失败而不是「后者覆盖前者」——那会让重复样本静默丢失，而 join 结果
    看起来仍然自洽。
    """
    index: dict[str, Mapping[str, Any]] = {}
    for sample in samples:
        sample_id = str(sample["sample_id"])
        if sample_id in index:
            raise CalibrationError(code, f"{side} 的 sample_id {sample_id!r} 重复")
        index[sample_id] = sample
    return index


# ---------------------------------------------------------------------------
# human_label 类型（§9.1）
# ---------------------------------------------------------------------------
def validate_human_label(sample: Mapping[str, Any]) -> None:
    """``human_label`` 类型必须与 question 匹配：noul=bool，choice=criteria key，score=整数档位。

    类型不匹配会让后续阈值拟合在错误类型上静默进行，故在此硬失败。
    """
    primitive = str(sample["primitive"])
    question_id = str(sample["question_id"])
    questions = FROZEN_BUSINESS_QUESTIONS.get(primitive)
    if questions is None or question_id not in questions:
        raise CalibrationError(
            "missing_field", f"未知 question：{primitive}.{question_id}"
        )
    question = questions[question_id]
    qtype = question["type"]
    label = sample["human_label"]

    if qtype == "noul":
        if not isinstance(label, bool):
            raise CalibrationError(
                "human_label_type_mismatch",
                f"{primitive}.{question_id} 是 noul，human_label 必须是 bool，实得 {label!r}",
            )
    elif qtype == "choice":
        criteria = question["criteria"]
        assert isinstance(criteria, dict)
        if not isinstance(label, str) or label not in criteria:
            raise CalibrationError(
                "human_label_type_mismatch",
                f"{primitive}.{question_id} 是 choice，human_label 必须是 criteria key，"
                f"实得 {label!r}（合法：{sorted(criteria)}）",
            )
    else:  # score
        levels = question["criteria"]
        assert isinstance(levels, list)
        # 允许 0 基或 1 基档位序号，但必须在 [0, len) 内。
        if isinstance(label, bool) or not isinstance(label, int) or not (0 <= label < len(levels)):
            raise CalibrationError(
                "human_label_type_mismatch",
                f"{primitive}.{question_id} 是 score，human_label 必须是 [0,{len(levels)}) 的整数档位，实得 {label!r}",
            )


# ---------------------------------------------------------------------------
# join
# ---------------------------------------------------------------------------
def join_samples(
    labels: Sequence[Mapping[str, Any]],
    inferences: Sequence[Mapping[str, Any]],
    *,
    store_root: Path,
) -> list[JoinedSample]:
    """严格一一 join（§9.1）。

    顺序：按 ``sample_id`` 建索引（拒绝重复）→ 集合必须完全相等 → 逐字段比较
    identity → 核对 ``state_ref`` 两侧一致且文件摘要相符 → 校验 ``human_label``。

    返回按 ``sample_id`` 字典序排列，保证下游（阈值拟合、join digest）结果可复现。
    """
    if not labels:
        raise CalibrationError("labels_empty", "labels artifact 为空")
    if not inferences:
        raise CalibrationError("inferences_empty", "inferences artifact 为空")

    label_index = _index_unique(labels, "labels", "labels_duplicate_sample_id")
    inference_index = _index_unique(inferences, "inferences", "inferences_duplicate_sample_id")

    label_ids = set(label_index)
    inference_ids = set(inference_index)
    if label_ids != inference_ids:
        only_labels = sorted(label_ids - inference_ids)
        only_inferences = sorted(inference_ids - label_ids)
        if only_labels:
            raise CalibrationError(
                "join_extra_label", f"labels 中有 sample 无对应 inference：{only_labels}"
            )
        raise CalibrationError(
            "join_extra_inference", f"inferences 中有 sample 无对应 label：{only_inferences}"
        )

    joined: list[JoinedSample] = []
    for sample_id in sorted(label_ids):
        label = label_index[sample_id]
        inference = inference_index[sample_id]

        for field in IDENTITY_FIELDS:
            if label.get(field) != inference.get(field):
                raise CalibrationError(
                    "identity_mismatch",
                    f"sample {sample_id!r} 的 {field} 两侧不一致："
                    f"label={label.get(field)!r} inference={inference.get(field)!r}",
                )

        label_ref = label["state_ref"]
        inference_ref = inference["state_ref"]
        assert isinstance(label_ref, dict) and isinstance(inference_ref, dict)
        if label_ref.get("path") != inference_ref.get("path"):
            raise CalibrationError(
                "state_ref_mismatch",
                f"sample {sample_id!r} 的 state_ref.path 两侧不一致："
                f"{label_ref.get('path')!r} vs {inference_ref.get('path')!r}",
            )
        if label_ref.get("sha256") != inference_ref.get("sha256"):
            raise CalibrationError(
                "state_ref_mismatch",
                f"sample {sample_id!r} 的 state_ref.sha256 两侧不一致："
                f"{label_ref.get('sha256')!r} vs {inference_ref.get('sha256')!r}",
            )

        # 两侧都独立核对真实文件字节：只比对声明值不足以发现「两侧一起写错」。
        _validate_state_ref(label, "labels", store_root)
        _validate_state_ref(inference, "inferences", store_root)

        declared = label["state_digest"]
        if label_ref.get("sha256") != declared:
            raise CalibrationError(
                "state_digest_mismatch",
                f"sample {sample_id!r} 的 state_digest 与 state_ref.sha256 不一致："
                f"{declared!r} vs {label_ref.get('sha256')!r}",
            )

        if label.get("split") != inference.get("split"):
            raise CalibrationError(
                "identity_mismatch",
                f"sample {sample_id!r} 的 split 两侧不一致："
                f"{label.get('split')!r} vs {inference.get('split')!r}",
            )

        validate_human_label(label)

        joined.append(
            JoinedSample(sample_id=sample_id, label=dict(label), inference=dict(inference))  # type: ignore[typeddict-item]
        )
    return joined


def load_and_join(
    labels_path: Path, inferences_path: Path, *, store_root: Path
) -> list[JoinedSample]:
    """读两个 artifact 并 join。任一校验失败即抛 ``CalibrationError``。"""
    labels = [parse_label(record) for record in read_jsonl(labels_path, "labels")]
    inferences = [parse_inference(record) for record in read_jsonl(inferences_path, "inferences")]
    return join_samples(labels, inferences, store_root=store_root)


def main(argv: Sequence[str] | None = None) -> int:
    """校验两个 artifact 的 CLI 入口：成功 0，失败非零（§9.1「立即失败并退出非零」）。

    阈值拟合与指标在 T-10 的 ``scripts/calibrate_laya.py``；此处只做 join 门禁，
    使「join 缺陷必然非零退出」这条性质在本任务内即可验证。
    """
    import argparse

    parser = argparse.ArgumentParser(description="校验 Laya 校准 artifact 的一一 join")
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--inferences", required=True, type=Path)
    parser.add_argument(
        "--sample-store",
        required=True,
        type=Path,
        help="受控本地样本库根目录；state_ref.path 必须落在其内",
    )
    args = parser.parse_args(argv)

    try:
        joined = load_and_join(args.labels, args.inferences, store_root=args.sample_store)
    except CalibrationError as exc:
        print(f"FAIL {exc.code}: {exc.detail}")
        return 1
    print(f"OK joined={len(joined)}")
    return 0
