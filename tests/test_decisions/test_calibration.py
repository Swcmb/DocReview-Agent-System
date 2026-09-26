"""T-09：校准 artifact schema 与 labels/inferences 严格一一 join（规格 §9.1）。

验收：无 join／重复／缺失／identity／state digest 错误**均非零失败**。

规范 fixture（`fixtures/calibration/`）逐字节取自规格 §9.1 的示例行，用于证明
schema 形态与规格一致；join 行为则用 `tmp_path` 里物化的副本验证——因为规范里的
``state_ref.path`` 指向受控样本库 ``D:/controlled/...``，本地并不存在。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, get_args

import pytest

from src.decisions.calibration import (
    ECE_BINS,
    ECE_DECIMALS,
    FAILING_CODES,
    IDENTITY_FIELDS,
    MARGIN_GRID,
    NEG_GRID,
    POS_GRID,
    CalibrationError,
    CalibrationErrorCode,
    EvalSample,
    Thresholds,
    compute_metrics,
    expected_calibration_error,
    file_sha256,
    load_and_join,
    main,
    parse_inference,
    parse_label,
    per_class_metrics,
    question_classes,
    read_jsonl,
    select_thresholds,
    source_doc_dedup_key,
    threshold_grid,
    validate_human_label,
    validate_split_sizing,
)
from src.decisions.question_contracts import FROZEN_BUSINESS_QUESTIONS

FIXTURES = Path(__file__).parent / "fixtures" / "calibration"
CANONICAL_LABELS = FIXTURES / "labels.jsonl"
CANONICAL_INFERENCES = FIXTURES / "inferences.jsonl"
CANONICAL_STATE = FIXTURES / "sample-store" / "verify_grounded-000001.json"

#: 规格 §9.1 固定向量里的摘要，作为不可替换的 golden。
SPEC_STATE_DIGEST = "sha256:32a500a946af6094a8eef1781da9dc1aa53c94af23457c22963676d7e86f4afb"
SPEC_DEDUP_KEY = "sha256:765c5e88aeb4322c6dff6907e4c06253cb7c5142d9ab9af61f990843f26ebdf4"
SPEC_COMMIT = "970dc8c5f63d7b886a68409493f37d569424f933"


# ---------------------------------------------------------------------------
# 物化helper：在 tmp 里造一对**有效** artifact，供各条变异测试在此基础上破坏
# ---------------------------------------------------------------------------
def _materialize(
    tmp_path: Path,
) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any]]:
    """把规范 fixture 复制进 tmp 并把 ``state_ref.path`` 指向真实文件。

    返回 ``(labels_path, inferences_path, store_root, label, inference)``。
    """
    store = tmp_path / "sample-store"
    store.mkdir()
    state = store / "verify_grounded-000001.json"
    state.write_bytes(CANONICAL_STATE.read_bytes())

    label = json.loads(CANONICAL_LABELS.read_text(encoding="utf-8"))
    inference = json.loads(CANONICAL_INFERENCES.read_text(encoding="utf-8"))
    for record in (label, inference):
        record["state_ref"] = {
            "path": str(state).replace("\\", "/"),
            "sha256": file_sha256(state),
        }
        record["state_digest"] = file_sha256(state)

    labels_path = tmp_path / "labels.jsonl"
    inferences_path = tmp_path / "inferences.jsonl"
    labels_path.write_text(json.dumps(label, ensure_ascii=False), encoding="utf-8")
    inferences_path.write_text(json.dumps(inference, ensure_ascii=False), encoding="utf-8")
    return labels_path, inferences_path, store, label, inference


def _rewrite(path: Path, record: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# §9.1 source_doc_dedup_key 规范化
# ---------------------------------------------------------------------------
def test_dedup_key_reproduces_spec_fixed_vector():
    """规范 §9.1：示例 ``source_doc_dedup_key`` 对应原文
    ``The acceptance criteria are measurable.``。算法固化后必须可复算。"""
    assert source_doc_dedup_key("The acceptance criteria are measurable.") == SPEC_DEDUP_KEY


def test_dedup_key_normalizes_crlf_and_trailing_space():
    assert source_doc_dedup_key("The acceptance criteria are measurable.  \r\n") == SPEC_DEDUP_KEY
    assert source_doc_dedup_key("The acceptance criteria are measurable.\r") == SPEC_DEDUP_KEY
    # 裸 LF 结尾（文件最常见形态）也必须同键：否则同一文档因末尾换行有无而得两键，
    # 「同一原始文档的片段不得跨 fit/validation」就有洞可钻。
    assert source_doc_dedup_key("The acceptance criteria are measurable.\n") == SPEC_DEDUP_KEY


def test_dedup_key_ignores_trailing_blank_lines():
    """末尾空行同样不算内容；段内空行仍必须保留。"""
    base = source_doc_dedup_key("The acceptance criteria are measurable.")
    assert source_doc_dedup_key("The acceptance criteria are measurable.\n\n\n") == base
    assert source_doc_dedup_key("a\n\nb") != source_doc_dedup_key("a\nb")


def test_dedup_key_preserves_blank_lines_and_inner_chars():
    """规范化只删行尾空格；段内字符与空行必须保留，否则不同文档会撞键。"""
    assert source_doc_dedup_key("a  b\n\nc") == source_doc_dedup_key("a  b\n\nc")
    assert source_doc_dedup_key("a  b\n\nc") != source_doc_dedup_key("a b\n\nc")


# ---------------------------------------------------------------------------
# 规范 fixture 的 schema 保真
# ---------------------------------------------------------------------------
def test_canonical_fixtures_have_no_bom_and_no_trailing_newline():
    """§9.1：示例 artifact 为 UTF-8 无 BOM、无尾随换行。"""
    for path in (CANONICAL_LABELS, CANONICAL_INFERENCES):
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert not raw.endswith(b"\n"), path


def test_canonical_label_parses_and_has_required_fields():
    label = parse_label(json.loads(CANONICAL_LABELS.read_text(encoding="utf-8")))
    assert label["sample_id"] == "verify_grounded-000001"
    assert label["primitive"] == "verify_issues"
    assert label["question_id"] == "grounded"
    assert label["laya_runtime_commit"] == SPEC_COMMIT
    assert label["state_digest"] == SPEC_STATE_DIGEST
    assert label["source_doc_dedup_key"] == SPEC_DEDUP_KEY


def test_canonical_state_file_digest_matches_declared_state_digest():
    """state_digest 钉的是文件真实字节，不是重新序列化后的结果。"""
    assert file_sha256(CANONICAL_STATE) == SPEC_STATE_DIGEST


def test_canonical_inference_parses():
    inference = parse_inference(json.loads(CANONICAL_INFERENCES.read_text(encoding="utf-8")))
    assert inference["raw_answer"]["type"] == "noul"
    assert inference["raw_answer"]["noul"] == 0.91
    # answer_confidence 在 schema 里是可选字段（模型未必报告），故先断言存在再取值。
    assert "answer_confidence" in inference
    assert inference.get("answer_confidence") == 0.91


def test_canonical_label_human_label_is_bool_for_noul():
    label = parse_label(json.loads(CANONICAL_LABELS.read_text(encoding="utf-8")))
    assert label["human_label"] is True
    validate_human_label(label)


# ---------------------------------------------------------------------------
# 正常 join
# ---------------------------------------------------------------------------
def test_valid_pair_joins(tmp_path):
    labels_path, inferences_path, store, _, _ = _materialize(tmp_path)
    joined = load_and_join(labels_path, inferences_path, store_root=store)
    assert len(joined) == 1
    assert joined[0]["sample_id"] == "verify_grounded-000001"
    assert joined[0]["label"]["human_label"] is True


def test_join_is_sorted_by_sample_id(tmp_path):
    """join 结果按 sample_id 排序，保证下游 join digest 可复现。"""
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    second_label = dict(label, sample_id="verify_grounded-000002")
    second_inference = dict(inference, sample_id="verify_grounded-000002")
    labels_path.write_text(
        json.dumps(label) + "\n" + json.dumps(second_label), encoding="utf-8"
    )
    inferences_path.write_text(
        json.dumps(inference) + "\n" + json.dumps(second_inference), encoding="utf-8"
    )
    joined = load_and_join(labels_path, inferences_path, store_root=store)
    assert [item["sample_id"] for item in joined] == [
        "verify_grounded-000001",
        "verify_grounded-000002",
    ]


# ---------------------------------------------------------------------------
# 非零失败：每条都必须让 main() 返回非零
# ---------------------------------------------------------------------------
def _expect_nonzero(tmp_path: Path, labels: Path, inferences: Path, store: Path) -> int:
    code = main(["--labels", str(labels), "--inferences", str(inferences), "--sample-store", str(store)])
    assert code != 0, "校验失败必须非零退出"
    return code


def test_duplicate_label_sample_id_fails(tmp_path):
    labels_path, inferences_path, store, label, _ = _materialize(tmp_path)
    labels_path.write_text(json.dumps(label) + "\n" + json.dumps(label), encoding="utf-8")
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_duplicate_inference_sample_id_fails(tmp_path):
    labels_path, inferences_path, store, _, inference = _materialize(tmp_path)
    inferences_path.write_text(
        json.dumps(inference) + "\n" + json.dumps(inference), encoding="utf-8"
    )
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_label_without_inference_fails(tmp_path):
    labels_path, inferences_path, store, label, _ = _materialize(tmp_path)
    labels_path.write_text(
        json.dumps(label) + "\n" + json.dumps(dict(label, sample_id="orphan-000001")),
        encoding="utf-8",
    )
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_inference_without_label_fails(tmp_path):
    labels_path, inferences_path, store, _, inference = _materialize(tmp_path)
    inferences_path.write_text(
        json.dumps(inference) + "\n" + json.dumps(dict(inference, sample_id="orphan-000001")),
        encoding="utf-8",
    )
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_no_join_at_all_fails(tmp_path):
    """两侧 sample_id 完全不相交——不得按文本相似度补 join。"""
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    _rewrite(labels_path, dict(label, sample_id="labels-only-000001"))
    _rewrite(inferences_path, dict(inference, sample_id="inferences-only-000001"))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_same_text_different_sample_id_does_not_join(tmp_path):
    """反事实：文本/state 摘要全同、仅 sample_id 不同，仍必须 join 失败。

    这是「不得按最近邻、时间或文本相似度补 join」的可执行断言。
    """
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    _rewrite(labels_path, dict(label, sample_id="labels-only-000001"))
    _rewrite(inferences_path, dict(inference, sample_id="inferences-only-000001"))
    with pytest.raises(CalibrationError) as excinfo:
        load_and_join(labels_path, inferences_path, store_root=store)
    assert excinfo.value.code in {"join_extra_label", "join_extra_inference"}


def test_empty_artifact_fails(tmp_path):
    labels_path, inferences_path, store, _, _ = _materialize(tmp_path)
    labels_path.write_text("", encoding="utf-8")
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


@pytest.mark.parametrize("field", IDENTITY_FIELDS)
def test_identity_mismatch_fails(tmp_path, field):
    """逐个 identity 字段都必须触发失败（§9.1 要求逐字段比较）。"""
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    _rewrite(inferences_path, dict(inference, **{field: "MUTATED"}))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_split_mismatch_fails(tmp_path):
    labels_path, inferences_path, store, _, inference = _materialize(tmp_path)
    _rewrite(inferences_path, dict(inference, split="validation"))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_state_digest_mismatch_fails(tmp_path):
    """state_digest 与 state_ref.sha256 不一致必须失败。"""
    labels_path, inferences_path, store, label, _ = _materialize(tmp_path)
    _rewrite(labels_path, dict(label, state_digest="sha256:" + "0" * 64))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_state_ref_digest_mismatch_between_sides_fails(tmp_path):
    labels_path, inferences_path, store, _, inference = _materialize(tmp_path)
    broken = dict(inference)
    broken["state_ref"] = dict(inference["state_ref"], sha256="sha256:" + "1" * 64)
    _rewrite(inferences_path, broken)
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_state_file_bytes_changed_fails(tmp_path):
    """两侧声明一致、但磁盘文件被改——只比对声明值抓不到，必须核真实字节。"""
    labels_path, inferences_path, store, _, _ = _materialize(tmp_path)
    (store / "verify_grounded-000001.json").write_text("tampered", encoding="utf-8")
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_state_ref_relative_path_fails(tmp_path):
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    relative = {**label["state_ref"], "path": "sample-store/verify_grounded-000001.json"}
    _rewrite(labels_path, dict(label, state_ref=relative))
    _rewrite(inferences_path, dict(inference, state_ref=relative))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_state_ref_url_fails(tmp_path):
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    url = {**label["state_ref"], "path": "https://example.com/sample.json"}
    _rewrite(labels_path, dict(label, state_ref=url))
    _rewrite(inferences_path, dict(inference, state_ref=url))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_state_ref_outside_store_fails(tmp_path):
    """受控样本库之外的路径必须失败——否则「受控」形同虚设。"""
    labels_path, inferences_path, store, label, inference = _materialize(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(CANONICAL_STATE.read_bytes())
    escaped = {
        "path": str(outside).replace("\\", "/"),
        "sha256": file_sha256(outside),
    }
    _rewrite(labels_path, dict(label, state_ref=escaped))
    _rewrite(inferences_path, dict(inference, state_ref=escaped))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_missing_required_field_fails(tmp_path):
    labels_path, inferences_path, store, label, _ = _materialize(tmp_path)
    incomplete = dict(label)
    # 必须删一个 parse_label 真正强制的字段；label_source/label_version 是可选
    # provenance，删它们不构成契约违例。
    del incomplete["source_doc_dedup_key"]
    _rewrite(labels_path, incomplete)
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_unsupported_schema_version_fails(tmp_path):
    labels_path, inferences_path, store, label, _ = _materialize(tmp_path)
    _rewrite(labels_path, dict(label, schema_version=99))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


# ---------------------------------------------------------------------------
# human_label 类型必须与 question 匹配（§9.1）
# ---------------------------------------------------------------------------
def test_human_label_wrong_type_for_noul_fails(tmp_path):
    labels_path, inferences_path, store, label, _ = _materialize(tmp_path)
    _rewrite(labels_path, dict(label, human_label="true"))
    _expect_nonzero(tmp_path, labels_path, inferences_path, store)


def test_human_label_invalid_choice_key_fails():
    with pytest.raises(CalibrationError) as excinfo:
        validate_human_label(
            {
                "primitive": "assess",
                "question_id": "document_type",
                "human_label": "not_a_criteria_key",
            }
        )
    assert excinfo.value.code == "human_label_type_mismatch"


def test_human_label_valid_choice_key_passes():
    validate_human_label(
        {"primitive": "assess", "question_id": "document_type", "human_label": "prd"}
    )


def test_human_label_score_level_bounds():
    base = {"primitive": "assess", "question_id": "completeness"}
    validate_human_label(dict(base, human_label=0))
    validate_human_label(dict(base, human_label=4))
    with pytest.raises(CalibrationError):
        validate_human_label(dict(base, human_label=5))
    with pytest.raises(CalibrationError):
        validate_human_label(dict(base, human_label=True))  # bool 不算整数档位


def test_unknown_question_fails():
    with pytest.raises(CalibrationError):
        validate_human_label(
            {"primitive": "assess", "question_id": "not_a_question", "human_label": True}
        )


# ---------------------------------------------------------------------------
# JSONL 读取
# ---------------------------------------------------------------------------
def test_read_jsonl_rejects_bom(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"a":1}')
    with pytest.raises(CalibrationError) as excinfo:
        read_jsonl(path, "labels")
    assert excinfo.value.code == "artifact_unreadable"


def test_read_jsonl_rejects_malformed_line(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"a":1}\nnot json\n', encoding="utf-8")
    with pytest.raises(CalibrationError):
        read_jsonl(path, "labels")


def test_read_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "labels.jsonl"
    path.write_text('{"a":1}\n\n{"b":2}\n', encoding="utf-8")
    assert len(read_jsonl(path, "labels")) == 2


# ---------------------------------------------------------------------------
# 失败路径的退出码契约
# ---------------------------------------------------------------------------
def test_valid_pair_exits_zero(tmp_path):
    labels_path, inferences_path, store, _, _ = _materialize(tmp_path)
    assert main(
        ["--labels", str(labels_path), "--inferences", str(inferences_path), "--sample-store", str(store)]
    ) == 0


def test_declared_error_codes_match_failing_codes():
    """``CalibrationErrorCode`` 与 ``FAILING_CODES`` 不得漂移。

    两边不一致意味着两种缺陷之一：某个 code 永远不会抛（死码，会误导审计消费方），
    或某个失败路径没登记（非零退出契约漏网）。本轮就因此清掉了 ``join_missing_pair``。
    """
    assert set(get_args(CalibrationErrorCode)) == set(FAILING_CODES)


# ===========================================================================
# T-10：§9.4 指标、10-bin ECE、per-class golden、fit-only 阈值选择
# ===========================================================================
def _noul_joined(
    index: int, p_true: float, confidence: float, human_label: bool, dedup: str | None = None
) -> dict[str, Any]:
    """构造一个 ``verify_issues.grounded``（noul）的已 join 样本。

    刻意只填指标层读取的字段，避免测试依赖 join 全字段。
    """
    sample_id = f"grounded-{index:06d}"
    return {
        "sample_id": sample_id,
        "label": {
            "sample_id": sample_id,
            "primitive": "verify_issues",
            "question_id": "grounded",
            "human_label": human_label,
            "source_doc_dedup_key": dedup or f"sha256:doc{index:064d}"[:71],
        },
        "inference": {
            "sample_id": sample_id,
            # F2：noul 判据只读 raw_answer 的原生字段
            "raw_answer": {"type": "noul", "noul": p_true, "answer_confidence": confidence},
            "probabilities": {"true": p_true, "false": 1.0 - p_true},
        },
    }


def _eval(
    confidence: float, predicted: str | None, human_label: str, question_type: str = "noul"
) -> Any:
    return EvalSample(
        sample_id="s",
        question_type=question_type,
        human_label=human_label,
        status="act" if predicted is not None else "uncertain",
        predicted=predicted,
        confidence=confidence,
        potential_decided=predicted is not None,
    )


# --- 阈值网格（§9.4 固定）-------------------------------------------------
def test_threshold_grid_shape_and_neg_lt_pos():
    grid = threshold_grid()
    assert len(grid) == 900  # 10 pos × 9 neg × 10 margin
    assert all(item["neg"] < item["pos"] for item in grid)
    assert POS_GRID[0] == 0.50 and POS_GRID[-1] == 0.95
    assert NEG_GRID[0] == 0.05 and NEG_GRID[-1] == 0.45
    assert MARGIN_GRID[0] == 0.05 and MARGIN_GRID[-1] == 0.50


# --- ECE 边界 golden（§9.4：验收项）--------------------------------------
@pytest.mark.parametrize(
    ("confidence", "expected_bin"),
    [
        (0.0, 0),
        (0.05, 0),
        (0.1, 1),
        (0.29999, 2),
        (0.3, 3),  # 关键：二进制浮点下 0.3*10 会落到 2，必须精确进 bin 3
        (0.55, 5),
        (0.89999, 8),
        (0.9, 9),
        (0.95, 9),
        (1.0, 9),  # 1.0 闭于最后 bin
    ],
)
def test_ece_bin_boundaries(confidence, expected_bin):
    _, bins = expected_calibration_error([_eval(confidence, "true", "true")])
    occupied = [index for index, entry in enumerate(bins) if entry["count"] > 0]
    assert occupied == [expected_bin], f"conf={confidence} 应落 bin {expected_bin}，实得 {occupied}"


def test_ece_perfect_calibration_is_zero():
    """conf=1.0 且全对 → mean_conf==accuracy==1.0 → ECE 0。"""
    ece, bins = expected_calibration_error([_eval(1.0, "true", "true")])
    assert ece == 0.0
    assert bins[9] == {"count": 1, "mean_confidence": 1.0, "accuracy": 1.0}


def test_ece_confidently_wrong_is_one():
    """conf=1.0 且全错 → ECE 1.0。"""
    ece, _ = expected_calibration_error([_eval(1.0, "true", "false")])
    assert ece == 1.0


def test_ece_two_bins_weighted_golden():
    """两个样本分处 bin 0 / bin 9，各对各错一个：
    0.5*|0.05-1.0| + 0.5*|0.95-0.0| = 0.475 + 0.475 = 0.95。"""
    samples = [_eval(0.05, "true", "true"), _eval(0.95, "true", "false")]
    ece, bins = expected_calibration_error(samples)
    assert bins[0] == {"count": 1, "mean_confidence": 0.05, "accuracy": 1.0}
    assert bins[9] == {"count": 1, "mean_confidence": 0.95, "accuracy": 0.0}
    assert ece == 0.95


def test_ece_empty_bins_excluded_and_reported():
    """空 bin 必须出现在 bins 里（count=0）但不参与加权平均。"""
    ece, bins = expected_calibration_error([_eval(1.0, "true", "true")])
    assert len(bins) == ECE_BINS
    assert sum(1 for entry in bins if entry["count"] == 0) == ECE_BINS - 1
    assert ece == 0.0  # 空 bin 若参与平均会得到 0.0/10 之类的假低值


def test_ece_rounded_to_six_decimals():
    samples = [_eval(0.07, "true", "true"), _eval(0.93, "true", "false")]
    ece, _ = expected_calibration_error(samples)
    assert ece == round(ece, ECE_DECIMALS)
    assert len(str(ece).split(".")[-1]) <= ECE_DECIMALS


# --- per-class golden（one-vs-rest）---------------------------------------
def test_per_class_metrics_golden():
    """4 个已决定样本：TP=2 / FP=1 / FN=1（针对 "true" 类）。
    precision=2/3, recall=2/3, f1=2/3。"""
    samples = [
        _eval(0.9, "true", "true"),  # TP
        _eval(0.9, "true", "true"),  # TP
        _eval(0.9, "true", "false"),  # FP（误报 true）
        _eval(0.9, "false", "true"),  # FN（漏报 true）
    ]
    result = per_class_metrics(samples, ["false", "true"])
    assert result["true"] == {
        "precision": round(2 / 3, 6),
        "recall": round(2 / 3, 6),
        "f1": round(2 / 3, 6),
        "support": 3,
    }
    # "false" 类：唯一标注为 false 的样本被预测成 true → FN；另有一个被预测成 false
    # 但标注为 true → FP。故 TP=0，precision/recall/F1 全为 0，support 仍为 1。
    assert result["false"] == {
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "support": 1,
    }


def test_per_class_zero_denominator_records_zero_but_keeps_support():
    """分母为零记 0，但 support 仍保留——否则「一个都没决定」会显示成类不存在。"""
    samples = [_eval(0.9, "true", "true")]
    result = per_class_metrics(samples, ["false", "true"])
    assert result["false"] == {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}
    assert result["true"]["support"] == 1


def test_per_class_support_counts_undecided_samples():
    samples = [_eval(0.9, "true", "true"), _eval(0.1, None, "false")]
    result = per_class_metrics(samples, ["false", "true"])
    assert result["false"]["support"] == 1  # 未决定也算 support


def test_question_classes_cover_every_choice_key_and_score_level():
    assert question_classes("verify_issues", "grounded") == ["false", "true"]
    assert question_classes("assess", "document_type") == [
        "prd", "technical_plan", "implementation_plan", "acceptance_checklist", "non_technical",
    ]
    assert question_classes("assess", "completeness") == ["0", "1", "2", "3", "4"]


# --- coverage / accuracy / score_potential_coverage -----------------------
def test_coverage_and_accuracy_golden():
    """4 个 noul 样本：2 个决定且正确、1 个决定但错、1 个未决定。
    coverage=3/4=0.75，accuracy=2/3。"""
    joined = [
        _noul_joined(0, 0.95, 0.95, True),
        _noul_joined(1, 0.05, 0.95, False),
        _noul_joined(2, 0.95, 0.95, False),  # 决定但错
        _noul_joined(3, 0.50, 0.10, True),  # conf < pos → uncertain
    ]
    metrics = compute_metrics(joined, Thresholds(pos=0.75, neg=0.25, margin=0.20))
    assert metrics["sample_count"] == 4
    assert metrics["decided_count"] == 3
    assert metrics["coverage"] == 0.75
    assert metrics["accuracy"] == round(2 / 3, 6)


def test_score_is_always_uncertain_and_reports_potential_only():
    """score 永远 uncertain；potential 只作离线诊断，不进业务动作。"""
    joined = [
        {
            "sample_id": f"imp-{i:06d}",
            "label": {
                "sample_id": f"imp-{i:06d}",
                "primitive": "assess",
                "question_id": "completeness",
                "human_label": 4,
                "source_doc_dedup_key": f"sha256:doc{i:064d}"[:71],
            },
            "inference": {
                "sample_id": f"imp-{i:06d}",
                "raw_answer": {"type": "score", "answer_confidence": 0.9},
                "probabilities": {"0": 0.02, "1": 0.03, "2": 0.05, "3": 0.2, "4": 0.7},
            },
        }
        for i in range(4)
    ]
    metrics = compute_metrics(joined, Thresholds(pos=0.75, neg=0.25, margin=0.20))
    assert metrics["decided_count"] == 0
    assert metrics["coverage"] == 0.0
    assert metrics["score_potential_coverage"] == 1.0
    assert set(metrics["per_class"]) == {"0", "1", "2", "3", "4"}


def test_f2_noul_reads_native_fields_not_probabilities():
    """F2 回归：noul 判据只读 raw_answer 原生字段。

    这里把 ``probabilities`` 置为与 ``noul`` 矛盾的值，若实现误读 probabilities
    就会得出相反结论。``probabilities["noul"]`` 本就是 structured.py 现场合成的。
    """
    joined = _noul_joined(0, 0.95, 0.95, True)
    joined["inference"]["probabilities"] = {"true": 0.05, "false": 0.95, "noul": 0.05}
    metrics = compute_metrics([joined], Thresholds(pos=0.75, neg=0.25, margin=0.20))
    assert metrics["decided_count"] == 1
    assert metrics["accuracy"] == 1.0  # 按 raw_answer.noul=0.95 → act(true) → 与 label 一致


# --- fit-only 阈值选择（§9.4）--------------------------------------------
def _separable_fit(n_true: int = 60, n_false: int = 40) -> list[dict[str, Any]]:
    joined = [_noul_joined(i, 0.95, 0.95, True, dedup=f"sha256:fit-true-{i:056d}") for i in range(n_true)]
    joined += [
        _noul_joined(1000 + i, 0.05, 0.95, False, dedup=f"sha256:fit-false-{i:056d}")
        for i in range(n_false)
    ]
    return joined


def test_select_thresholds_uses_only_fit_join():
    """结构性保证：``select_thresholds`` 签名里没有 validation 参数。"""
    import inspect

    parameters = list(inspect.signature(select_thresholds).parameters)
    assert parameters == ["fit_joined"]


def test_select_thresholds_deterministic_tiebreak():
    """完美可分数据下所有阈值同分，按 (pos-neg) 升序等 tiebreak 选出最窄带。"""
    thresholds, metrics = select_thresholds(_separable_fit())
    assert metrics["coverage"] == 1.0
    assert metrics["accuracy"] == 1.0
    # (pos-neg) 升序 → 最小 pos-neg 胜出，即 pos=0.50 / neg=0.45（0.05）
    assert thresholds == Thresholds(pos=0.50, neg=0.45, margin=0.05)


def test_select_thresholds_rejects_uncalibratable_data():
    """全噪声数据（conf 低、标注随机）无法达到 accuracy>=0.80 → 必须失败。"""
    joined = [_noul_joined(i, 0.5, 0.10, i % 2 == 0) for i in range(100)]
    with pytest.raises(CalibrationError) as excinfo:
        select_thresholds(joined)
    assert excinfo.value.code == "no_valid_threshold"


def test_select_thresholds_requires_every_class_supported():
    """只有单一类别的 fit join 无法校准另一类 → 必须失败。"""
    joined = [_noul_joined(i, 0.95, 0.95, True) for i in range(100)]
    with pytest.raises(CalibrationError) as excinfo:
        select_thresholds(joined)
    assert excinfo.value.code == "no_valid_threshold"


# --- split 规模与泄漏（§9.4）---------------------------------------------
def test_split_too_small_fails():
    fit = _separable_fit(60, 40)
    validation = [_noul_joined(9000 + i, 0.95, 0.95, i % 2 == 0) for i in range(10)]
    with pytest.raises(CalibrationError) as excinfo:
        validate_split_sizing(fit, validation)
    assert excinfo.value.code == "split_too_small"


def test_document_key_leak_fails():
    """同一原始文档出现在两侧即 invalid——否则等于在训练集上测。"""
    fit = _separable_fit(60, 40)
    shared = "sha256:shared-document"
    validation = [_noul_joined(9000 + i, 0.95, 0.95, i % 2 == 0, dedup=shared) for i in range(60)]
    fit[0]["label"]["source_doc_dedup_key"] = shared
    with pytest.raises(CalibrationError) as excinfo:
        validate_split_sizing(fit, validation)
    assert excinfo.value.code == "split_document_leak"


def test_class_sample_insufficient_fails():
    """noul 每类 validation 至少 25 条；不足即 invalid。"""
    fit = _separable_fit(60, 40)
    validation = [_noul_joined(9000 + i, 0.95, 0.95, i < 10) for i in range(60)]
    with pytest.raises(CalibrationError) as excinfo:
        validate_split_sizing(fit, validation)
    assert excinfo.value.code == "class_sample_insufficient"


def test_valid_sizing_passes():
    fit = _separable_fit(60, 40)
    validation = [
        _noul_joined(9000 + i, 0.95, 0.95, i % 2 == 0, dedup=f"sha256:val-{i:059d}")
        for i in range(60)
    ]
    validate_split_sizing(fit, validation)  # 不抛即通过


def test_score_probability_keys_are_level_indices_not_criteria_text():
    """回归锁：score 概率键是**档位下标字符串**，不是 criteria 描述文本。

    Laya 取 score argmax 用 `probs.get(str(i), ...)`（``structured.py:185``），
    而本项目冻结契约的 score criteria 是描述性标签（§5.2）——两套键不同。
    早先实现拿描述文本匹配，导致合法概率被判为未知、score_potential_coverage
    恒为 0 且**不抛异常**。此测试把两种键的差异钉死。
    """
    thresholds = Thresholds(pos=0.75, neg=0.25, margin=0.20)
    levels_text = FROZEN_BUSINESS_QUESTIONS["assess"]["completeness"]["criteria"]
    assert isinstance(levels_text, list)
    assert levels_text[0] == "almost_no_substantive_information"  # 确认是描述文本

    def joined_with(probabilities: dict[str, float]) -> dict[str, Any]:
        return {
            "sample_id": "imp-000001",
            "label": {
                "sample_id": "imp-000001",
                "primitive": "assess",
                "question_id": "completeness",
                "human_label": 4,
                "source_doc_dedup_key": "sha256:doc" + "0" * 60,
            },
            "inference": {
                "sample_id": "imp-000001",
                "raw_answer": {"type": "score", "answer_confidence": 0.9},
                "probabilities": probabilities,
            },
        }

    by_index = compute_metrics(
        [joined_with({"0": 0.02, "1": 0.03, "2": 0.05, "3": 0.2, "4": 0.7})], thresholds
    )
    assert by_index["score_potential_coverage"] == 1.0

    by_criteria_text = compute_metrics(
        [joined_with({str(level): 0.2 for level in levels_text})], thresholds
    )
    assert by_criteria_text["score_potential_coverage"] == 0.0
