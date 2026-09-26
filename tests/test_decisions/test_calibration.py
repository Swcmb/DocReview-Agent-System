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
    FAILING_CODES,
    IDENTITY_FIELDS,
    CalibrationError,
    CalibrationErrorCode,
    file_sha256,
    load_and_join,
    main,
    parse_inference,
    parse_label,
    read_jsonl,
    source_doc_dedup_key,
    validate_human_label,
)

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
