"""§9.1 校准 artifact schema 与 labels/inferences 严格一一 join。

两个 artifact（``labels.jsonl`` / ``inferences.jsonl``）是独立受控文件，按
``sample_id`` **精确**一一 join，然后逐字段比较 identity。**任何** join 缺陷都
必须立即失败——不得按最近邻、时间或文本相似度补 join（§9.1）。

本模块承担「读 + 校验 + join + 指标 + fit-only 阈值选择」。CLI 驱动是
``scripts/calibrate_laya.py``（§9.5），它只做参数解析与落盘，逻辑全在此处，
以便指标与阈值口径可被测试直接覆盖。

设计要点：
- **受控样本库根由调用方显式传入**，不从配置猜、不从 ``state_ref`` 推导。
  自己声明「什么是受控」等于没有受控边界。
- 全部失败路径收敛到一个 ``CalibrationError``，带稳定 ``code``；上层脚本据此
  映射退出码，本模块不自行 ``sys.exit``。
- **阈值只在 fit join 上选**（§9.4）。validation join 只算最终指标，任何让它
  参与候选排序的代码路径都是规格违规。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal, TypedDict

from src.decisions.question_contracts import FROZEN_BUSINESS_QUESTIONS, canonical_json_bytes

__all__ = [
    # ---- 错误与 schema ----
    "CalibrationError",
    "CalibrationErrorCode",
    "FAILING_CODES",
    "SCHEMA_VERSION",
    "IDENTITY_FIELDS",
    # ---- §9.4 指标常量 ----
    "POS_GRID",
    "NEG_GRID",
    "MARGIN_GRID",
    "MIN_COVERAGE",
    "MIN_ACCURACY",
    "MAX_ECE",
    "MIN_FIT_JOIN",
    "MIN_VALIDATION_JOIN",
    "MIN_NOUL_PER_CLASS_VALIDATION",
    "MIN_LEVEL_PER_CLASS_VALIDATION",
    "ECE_BINS",
    "ECE_DECIMALS",
    # ---- 类型 ----
    "LabelSample",
    "InferenceSample",
    "JoinedSample",
    "Thresholds",
    "ClassMetrics",
    "EceBin",
    "Metrics",
    "EvalSample",
    "JoinSummary",
    # ---- §9.1 join ----
    "source_doc_dedup_key",
    "file_sha256",
    "canonical_digest",
    "parse_label",
    "parse_inference",
    "read_jsonl",
    "validate_human_label",
    "join_samples",
    "load_and_join",
    "summary_of",
    # ---- §9.4 指标与阈值 ----
    "threshold_grid",
    "question_classes",
    "build_eval_samples",
    "expected_calibration_error",
    "per_class_metrics",
    "compute_metrics",
    "select_thresholds",
    "validate_split_sizing",
    # ---- CLI ----
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
    "no_valid_threshold",
    "split_too_small",
    "split_document_leak",
    "class_sample_insufficient",
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
        "no_valid_threshold",
        "split_too_small",
        "split_document_leak",
        "class_sample_insufficient",
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


def canonical_digest(value: object) -> str:
    """canonical JSON 的 SHA-256，带 ``sha256:`` 前缀（§9.2／§9.3）。

    复用 question_contracts 的统一 canonical 序列化（sort_keys + 紧凑分隔符 +
    保留非 ASCII），保证 join 摘要、manifest 摘要与 question hash 三者口径一致。
    """
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


class JoinSummary(TypedDict):
    """split 规模摘要（§9.3 的 ``fit_join`` / ``validation_join``）。"""

    sample_count: int
    source_doc_key_count: int
    class_counts: dict[str, int]
    join_sha256: str


def summary_of(joined: Sequence[Mapping[str, Any]]) -> JoinSummary:
    """把已 join 样本压成 §9.3 要求的 split 摘要。

    ``join_sha256`` 由**按 sample_id 排序后的** label+inference 投影算出，故与
    artifact 的行序无关：重排 JSONL 不应改变摘要。投影里保留 identity 九字段、
    ``state_digest``、``human_label`` 与 ``raw_answer``/``probabilities``——
    任何影响判定的输入变动都必须改变摘要。
    """
    projection = []
    class_counts: dict[str, int] = {}
    for item in sorted(joined, key=lambda entry: str(entry["label"]["sample_id"])):
        label = item["label"]
        inference = item["inference"]
        primitive = str(label["primitive"])
        question_id = str(label["question_id"])
        question_type = str(FROZEN_BUSINESS_QUESTIONS[primitive][question_id]["type"])
        name = _class_name(question_type, label.get("human_label"))
        if name is not None:
            class_counts[name] = class_counts.get(name, 0) + 1
        projection.append(
            {
                "sample_id": str(label["sample_id"]),
                "identity": {field: label[field] for field in IDENTITY_FIELDS},
                "human_label": label.get("human_label"),
                "raw_answer": inference.get("raw_answer"),
                "probabilities": inference.get("probabilities"),
            }
        )
    return JoinSummary(
        sample_count=len(joined),
        source_doc_key_count=len({str(item["label"]["source_doc_dedup_key"]) for item in joined}),
        class_counts=dict(sorted(class_counts.items())),
        join_sha256=canonical_digest(projection),
    )


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


# ===========================================================================
# §9.4 指标与确定性阈值选择
# ===========================================================================
# 阈值网格（§9.4 固定，不得改为连续搜索）
POS_GRID: Final[tuple[float, ...]] = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))
NEG_GRID: Final[tuple[float, ...]] = tuple(round(0.05 + 0.05 * i, 2) for i in range(9))
MARGIN_GRID: Final[tuple[float, ...]] = tuple(round(0.05 + 0.05 * i, 2) for i in range(10))

#: fit 阶段的候选门槛（§9.4）
MIN_COVERAGE: Final = 0.60
MIN_ACCURACY: Final = 0.80
MAX_ECE: Final = 0.10

#: split 规模与类别最小样本（§9.4）
MIN_FIT_JOIN: Final = 100
MIN_VALIDATION_JOIN: Final = 50
MIN_NOUL_PER_CLASS_VALIDATION: Final = 25
MIN_LEVEL_PER_CLASS_VALIDATION: Final = 20

ECE_BINS: Final = 10
ECE_DECIMALS: Final = 6


class Thresholds(TypedDict):
    pos: float
    neg: float
    margin: float


class ClassMetrics(TypedDict):
    precision: float
    recall: float
    f1: float
    support: int


class EceBin(TypedDict):
    count: int
    mean_confidence: float | None
    accuracy: float | None


class Metrics(TypedDict):
    """``compute_metrics`` 恒定返回的全部字段——故全部为必填。"""

    sample_count: int
    decided_count: int
    coverage: float
    accuracy: float
    ece: float
    ece_bins: list[EceBin]
    per_class: dict[str, ClassMetrics]
    score_potential_coverage: float


class EvalSample(TypedDict):
    """单个已 join 样本的评估视图（把 label/inference 压成指标所需的最少字段）。"""

    sample_id: str
    question_type: str
    human_label: str
    status: str
    predicted: str | None
    confidence: float | None
    potential_decided: bool


def threshold_grid() -> list[Thresholds]:
    """固定阈值网格：``pos × neg × margin``，只保留 ``neg < pos``（§9.4）。"""
    return [
        Thresholds(pos=pos, neg=neg, margin=margin)
        for pos in POS_GRID
        for neg in NEG_GRID
        for margin in MARGIN_GRID
        if neg < pos
    ]


def question_classes(primitive: str, question_id: str) -> list[str]:
    """该 question 的全部类别名。

    noul → ``["false","true"]``；choice → criteria 声明顺序的键；score → ``"0".."n-1"``。
    §9.4 要求 choice 每个 criteria、score 每个档位都必须出现在 per-class 里。
    """
    question = FROZEN_BUSINESS_QUESTIONS[primitive][question_id]
    qtype = question["type"]
    if qtype == "noul":
        return ["false", "true"]
    if qtype == "choice":
        criteria = question["criteria"]
        assert isinstance(criteria, dict)
        return list(criteria)
    levels = question["criteria"]
    assert isinstance(levels, list)
    return [str(index) for index in range(len(levels))]


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _class_name(question_type: str, label: object) -> str | None:
    if question_type == "noul":
        return "true" if label is True else ("false" if label is False else None)
    if question_type == "choice":
        return label if isinstance(label, str) else None
    if isinstance(label, bool) or not isinstance(label, int):
        return None
    return str(label)


def _decide(
    primitive: str,
    question_id: str,
    label: Mapping[str, Any],
    inference: Mapping[str, Any],
    thresholds: Thresholds,
) -> tuple[str, str | None, float | None, bool]:
    """按 §4.1 三态规则判定单样本。

    返回 ``(status, predicted_class, confidence, potential_decided)``。

    **F2 纪律（§8.2）**：``noul`` 判据**只能**读原生字段
    ``raw_answer["noul"]`` 与 ``raw_answer["answer_confidence"]``。
    ``probabilities["noul"]`` 是 ``structured.py`` 用 ``{false:1-p,true:p}``
    现场合成的，不是模型输出；``answer_confidence`` 也不能用
    ``DecisionResult.confidence`` 替代（那是未校准的归一化熵）。
    choice/score 的 ``probabilities`` 才是模型输出，可以读。
    """
    question = FROZEN_BUSINESS_QUESTIONS[primitive][question_id]
    qtype = str(question["type"])
    raw = inference.get("raw_answer")
    if not isinstance(raw, dict):
        return "uncertain", None, None, False
    confidence = _finite(raw.get("answer_confidence"))
    pos = thresholds["pos"]
    neg = thresholds["neg"]

    if qtype == "noul":
        p_true = _finite(raw.get("noul"))
        if p_true is None or confidence is None:
            return "uncertain", None, confidence, False
        if p_true >= pos and confidence >= pos:
            return "act", "true", confidence, True
        if p_true <= neg and confidence >= pos:
            return "pass", "false", confidence, True
        return "uncertain", None, confidence, False

    if qtype == "choice":
        criteria = question["criteria"]
        assert isinstance(criteria, dict)
        probabilities = inference.get("probabilities")
        if len(criteria) < 2 or not isinstance(probabilities, dict) or confidence is None:
            return "uncertain", None, confidence, False
        known = {
            str(key): value
            for key, value in ((k, _finite(v)) for k, v in probabilities.items())
            if key in criteria and value is not None
        }
        if len(known) < 2:
            return "uncertain", None, confidence, False
        ranked = sorted(known.items(), key=lambda item: item[1], reverse=True)
        top_key, top_value = ranked[0]
        second_value = ranked[1][1]
        if confidence >= pos and (top_value - second_value) >= thresholds["margin"]:
            return "act", top_key, confidence, True
        return "uncertain", None, confidence, False

    # score：永远 uncertain，route_action=no_action（§4.1）。potential_decided 只用于
    # 离线 score_potential_coverage，运行时永不转成业务动作。
    #
    # 注意 score 概率的键是**档位下标字符串**，不是 criteria 描述文本：Laya 取 argmax
    # 用 `probs.get(str(i), probs.get(i, 0.0))`（`structured.py:185`），且它自己构造的
    # score criteria 也是 `[str(v) for v in range(lo, hi+1)]`（`structured.py:116`）。
    # 本项目的冻结契约把 score criteria 写成描述性标签（§5.2），两者**不是同一套键**，
    # 故这里必须按 `range(len(levels))` 生成合法下标集——若误拿描述文本去匹配，
    # 合法概率会被全部判为未知，score_potential_coverage 恒为 0 且不抛异常。
    probabilities = inference.get("probabilities")
    levels = question["criteria"]
    assert isinstance(levels, list)
    valid_level_keys = {str(index) for index in range(len(levels))}
    potential = False
    if isinstance(probabilities, dict) and confidence is not None and confidence >= pos:
        potential = any(
            _finite(value) is not None
            for key, value in probabilities.items()
            if str(key) in valid_level_keys
        )
    return "uncertain", None, confidence, potential


def build_eval_samples(
    joined: Sequence[Mapping[str, Any]], thresholds: Thresholds
) -> list[EvalSample]:
    """把已 join 样本压成评估视图。"""
    samples: list[EvalSample] = []
    for item in joined:
        label = item["label"]
        inference = item["inference"]
        primitive = str(label["primitive"])
        question_id = str(label["question_id"])
        question_type = str(FROZEN_BUSINESS_QUESTIONS[primitive][question_id]["type"])
        human = _class_name(question_type, label.get("human_label"))
        status, predicted, confidence, potential = _decide(
            primitive, question_id, label, inference, thresholds
        )
        samples.append(
            EvalSample(
                sample_id=str(label["sample_id"]),
                question_type=question_type,
                human_label=human if human is not None else "",
                status=status,
                predicted=predicted,
                confidence=confidence,
                potential_decided=potential,
            )
        )
    return samples


def expected_calibration_error(samples: Sequence[EvalSample]) -> tuple[float, list[EceBin]]:
    """10-bin ECE（§9.4）。

    边界固定为 ``[0.0,0.1), [0.1,0.2), ... [0.9,1.0]``——最后一个 bin **闭于 1.0**。
    每个 bin 记录 count / mean confidence / accuracy；空 bin 不参与平均；
    返回值保留 6 位小数。

    bin 索引用 ``Decimal`` 而非直接 ``int(conf*10)``：二进制浮点下
    ``0.3*10 == 2.9999999999999996``，会把恰好落在 ``0.3`` 的置信度错分到
    ``[0.2,0.3)``。规格把「ECE 边界」列为验收项，故此处必须精确。
    """
    usable = [
        sample
        for sample in samples
        if sample["predicted"] is not None and sample["confidence"] is not None
    ]
    buckets: list[list[EvalSample]] = [[] for _ in range(ECE_BINS)]
    for sample in usable:
        confidence = Decimal(str(sample["confidence"]))
        index = int(confidence * ECE_BINS)
        if index >= ECE_BINS:  # 1.0 落最后 bin
            index = ECE_BINS - 1
        buckets[index].append(sample)

    total = len(usable)
    bins: list[EceBin] = []
    ece = 0.0
    for bucket in buckets:
        count = len(bucket)
        if count == 0:
            bins.append(EceBin(count=0, mean_confidence=None, accuracy=None))
            continue
        mean_confidence = sum(float(s["confidence"]) for s in bucket) / count  # type: ignore[arg-type]
        accuracy = sum(1 for s in bucket if s["predicted"] == s["human_label"]) / count
        ece += (count / total) * abs(mean_confidence - accuracy)
        bins.append(
            EceBin(
                count=count,
                mean_confidence=round(mean_confidence, ECE_DECIMALS),
                accuracy=round(accuracy, ECE_DECIMALS),
            )
        )
    return round(ece, ECE_DECIMALS), bins


def per_class_metrics(
    samples: Sequence[EvalSample], classes: Sequence[str]
) -> dict[str, ClassMetrics]:
    """one-vs-rest 的 precision / recall / F1 / support（§9.4）。

    分母为零时该指标记 0，但 **support 仍按全部样本统计**（含未决定样本）——
    否则「一个都没决定」的类别会显示成不存在，掩盖覆盖率问题。
    """
    decided = [sample for sample in samples if sample["predicted"] is not None]
    result: dict[str, ClassMetrics] = {}
    for name in classes:
        true_positive = sum(
            1 for s in decided if s["predicted"] == name and s["human_label"] == name
        )
        false_positive = sum(
            1 for s in decided if s["predicted"] == name and s["human_label"] != name
        )
        false_negative = sum(
            1 for s in decided if s["predicted"] != name and s["human_label"] == name
        )
        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
        recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        support = sum(1 for s in samples if s["human_label"] == name)
        result[name] = ClassMetrics(
            precision=round(precision, ECE_DECIMALS),
            recall=round(recall, ECE_DECIMALS),
            f1=round(f1, ECE_DECIMALS),
            support=support,
        )
    return result


def compute_metrics(
    joined: Sequence[Mapping[str, Any]], thresholds: Thresholds
) -> Metrics:
    """在给定阈值下算全套指标（§9.4）。``joined`` 决定 fit 还是 validation 语义。"""
    samples = build_eval_samples(joined, thresholds)
    decided = [sample for sample in samples if sample["status"] in ("act", "pass")]
    total = len(samples)
    coverage = (len(decided) / total) if total else 0.0
    correct = sum(1 for sample in decided if sample["predicted"] == sample["human_label"])
    accuracy = (correct / len(decided)) if decided else 0.0
    ece, bins = expected_calibration_error(samples)

    classes: list[str] = []
    for item in joined:
        primitive = str(item["label"]["primitive"])
        question_id = str(item["label"]["question_id"])
        for name in question_classes(primitive, question_id):
            if name not in classes:
                classes.append(name)

    score_samples = [s for s in samples if s["question_type"] == "score"]
    potential = (
        sum(1 for s in score_samples if s["potential_decided"]) / len(score_samples)
        if score_samples
        else 0.0
    )

    return Metrics(
        sample_count=total,
        decided_count=len(decided),
        coverage=round(coverage, ECE_DECIMALS),
        accuracy=round(accuracy, ECE_DECIMALS),
        ece=ece,
        ece_bins=bins,
        per_class=per_class_metrics(samples, classes),
        score_potential_coverage=round(potential, ECE_DECIMALS),
    )


def _meets_fit_support(metrics: Metrics) -> bool:
    """fit 候选必须让**每个**类别都有样本，否则该类别无从校准。"""
    return bool(metrics["per_class"]) and all(
        entry["support"] >= 1 for entry in metrics["per_class"].values()
    )


def select_thresholds(
    fit_joined: Sequence[Mapping[str, Any]],
) -> tuple[Thresholds, Metrics]:
    """**只在 fit join 上**选阈值（§9.4）。

    过滤 ``coverage>=0.60``、``accuracy>=0.80``、每类 support>=1、``ece<=0.10``；
    按 ``(coverage 降, accuracy 降, ECE 升, (pos-neg) 升, margin 升, pos 升, neg 升)``
    取第一项。函数签名里**没有** validation 参数——这是「validation 不可调阈值」
    的结构性保证，而非靠调用方自觉。
    """
    candidates: list[tuple[Metrics, Thresholds]] = []
    for thresholds in threshold_grid():
        metrics = compute_metrics(fit_joined, thresholds)
        if metrics["coverage"] < MIN_COVERAGE:
            continue
        if metrics["accuracy"] < MIN_ACCURACY:
            continue
        if metrics["ece"] > MAX_ECE:
            continue
        if not _meets_fit_support(metrics):
            continue
        candidates.append((metrics, thresholds))
    if not candidates:
        raise CalibrationError(
            "no_valid_threshold", "fit join 上没有任何阈值组合满足 coverage/accuracy/ECE/support 门槛"
        )
    candidates.sort(
        key=lambda pair: (
            -pair[0]["coverage"],
            -pair[0]["accuracy"],
            pair[0]["ece"],
            pair[1]["pos"] - pair[1]["neg"],
            pair[1]["margin"],
            pair[1]["pos"],
            pair[1]["neg"],
        )
    )
    best_metrics, best_thresholds = candidates[0]
    return best_thresholds, best_metrics


def validate_split_sizing(
    fit_joined: Sequence[Mapping[str, Any]], validation_joined: Sequence[Mapping[str, Any]]
) -> None:
    """split 规模、类别最小样本与文档 key 交集（§9.4）。

    交集非空即 invalid：同一原始文档的片段跨 fit/validation 会让 validation 指标
    失去意义（等于在训练集上测）。
    """
    if len(fit_joined) < MIN_FIT_JOIN:
        raise CalibrationError("split_too_small", f"fit join {len(fit_joined)} < {MIN_FIT_JOIN}")
    if len(validation_joined) < MIN_VALIDATION_JOIN:
        raise CalibrationError(
            "split_too_small", f"validation join {len(validation_joined)} < {MIN_VALIDATION_JOIN}"
        )

    fit_keys = {str(item["label"]["source_doc_dedup_key"]) for item in fit_joined}
    validation_keys = {str(item["label"]["source_doc_dedup_key"]) for item in validation_joined}
    overlap = sorted(fit_keys & validation_keys)
    if overlap:
        raise CalibrationError(
            "split_document_leak",
            f"fit/validation 原始文档 key 交集非空（{len(overlap)} 个），存在数据泄漏",
        )

    counts: dict[tuple[str, str, str], int] = {}
    for item in validation_joined:
        label = item["label"]
        primitive = str(label["primitive"])
        question_id = str(label["question_id"])
        question_type = str(FROZEN_BUSINESS_QUESTIONS[primitive][question_id]["type"])
        name = _class_name(question_type, label.get("human_label")) or ""
        key = (primitive, question_id, name)
        counts[key] = counts.get(key, 0) + 1

    for item in validation_joined:
        label = item["label"]
        primitive = str(label["primitive"])
        question_id = str(label["question_id"])
        question_type = str(FROZEN_BUSINESS_QUESTIONS[primitive][question_id]["type"])
        for name in question_classes(primitive, question_id):
            minimum = (
                MIN_NOUL_PER_CLASS_VALIDATION
                if question_type == "noul"
                else MIN_LEVEL_PER_CLASS_VALIDATION
            )
            if counts.get((primitive, question_id, name), 0) < minimum:
                raise CalibrationError(
                    "class_sample_insufficient",
                    f"validation {primitive}.{question_id} 的 {name!r} 类样本 "
                    f"{counts.get((primitive, question_id, name), 0)} < {minimum}",
                )
