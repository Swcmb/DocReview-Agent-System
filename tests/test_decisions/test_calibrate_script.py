"""T-10：`scripts/calibrate_laya.py` 的 manifest 组装与失败路径。

只覆盖**不依赖 laya / 真实 checkpoint** 的部分：manifest 字段形状、指标摘要压平、
checkpoint 摘要的「排除自身」语义，以及缺文件时的非零退出。

按 §9.5，guard schema hash 必须来自 ``laya.presets``，故 ``question_schema_hashes()``
与 ``runtime_source_digest()`` 在本环境不可测——它们由 T-11 的跨环境方案覆盖，
此处**不**伪造其返回值。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "calibrate_laya.py"


def _load_script() -> ModuleType:
    """按文件路径加载 CLI 脚本（``scripts/`` 不是包）。"""
    spec = importlib.util.spec_from_file_location("calibrate_laya_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load_script()


_CHECKPOINT_MANIFEST = {
    "schema_version": 1,
    "model": "multilingual",
    "checkpoint_id": "laya-multilingual",
    "runtime_commit": "970dc8c5f63d7b886a684094f93f37d569424f933",
    "root": "D:/controlled",
    "files": [],
    "all_behavior_files_covered": True,
}


def _metrics(**overrides: object) -> dict:
    base = {
        "sample_count": 120,
        "decided_count": 90,
        "coverage": 0.75,
        "accuracy": 0.866667,
        "ece": 0.05,
        "ece_bins": [{"count": 0}] * 10,
        "per_class": {
            "true": {"precision": 0.88, "recall": 0.85, "f1": 0.865, "support": 60},
            "false": {"precision": 0.84, "recall": 0.87, "f1": 0.855, "support": 60},
        },
        "score_potential_coverage": 0.0,
    }
    base.update(overrides)
    return base


def _summary(count: int) -> dict:
    return {
        "sample_count": count,
        "source_doc_key_count": count // 2,
        "class_counts": {"true": count // 2, "false": count // 2},
        "join_sha256": f"sha256:{count:064d}",
    }


def _build(script: ModuleType, **overrides: object) -> dict:
    kwargs = {
        "created_at": "2026-09-25T00:00:00+08:00",
        "checkpoint_manifest": _CHECKPOINT_MANIFEST,
        "runtime_source_digest_value": "sha256:" + "a" * 64,
        "question_hashes": {f"p{i}": f"sha256:{i:064d}" for i in range(6)},
        "fit_artifacts": {
            "labels": {"path": "D:/c/fit-labels.jsonl", "sha256": "sha256:" + "b" * 64},
            "inferences": {"path": "D:/c/fit-inf.jsonl", "sha256": "sha256:" + "c" * 64},
        },
        "validation_artifacts": {
            "labels": {"path": "D:/c/val-labels.jsonl", "sha256": "sha256:" + "d" * 64},
            "inferences": {"path": "D:/c/val-inf.jsonl", "sha256": "sha256:" + "e" * 64},
        },
        "fit_summary": _summary(240),
        "validation_summary": _summary(120),
        "thresholds": {"pos": 0.75, "neg": 0.25, "margin": 0.2},
        "validation_metrics": _metrics(),
        "max_batch_encoded_chars": 16384,
    }
    kwargs.update(overrides)
    return script.build_manifest(**kwargs)  # type: ignore[arg-type]


# --- manifest 形状 ---------------------------------------------------------
def test_manifest_has_every_spec_key(script):
    manifest = _build(script)
    assert set(manifest) == {
        "schema_version",
        "manifest_id",
        "created_at",
        "laya_runtime_commit",
        "runtime_source_digest",
        "model",
        "checkpoint_manifest_sha256",
        "labels_artifact",
        "inferences_artifact",
        "fit_artifacts",
        "validation_artifacts",
        "question_schema_hashes",
        "fit_join",
        "validation_join",
        "source_doc_key_overlap",
        "thresholds",
        "metrics",
        "max_batch_encoded_chars",
    }
    assert manifest["schema_version"] == 1
    assert manifest["manifest_id"] == "laya-calibration-v1"
    assert manifest["created_at"] == "2026-09-25T00:00:00+08:00"


def test_manifest_carries_model_and_runtime_commit_from_checkpoint_manifest(script):
    manifest = _build(script)
    assert manifest["model"] == "multilingual"
    assert manifest["laya_runtime_commit"] == _CHECKPOINT_MANIFEST["runtime_commit"]


def test_manifest_keeps_all_four_artifacts(script):
    """§9.5 收四个 artifact；单数字段指向 fit 对，四 artifact 全部可追溯。"""
    manifest = _build(script)
    assert manifest["labels_artifact"] == manifest["fit_artifacts"]["labels"]
    assert manifest["inferences_artifact"] == manifest["fit_artifacts"]["inferences"]
    assert manifest["validation_artifacts"]["labels"]["path"] == "D:/c/val-labels.jsonl"
    assert manifest["validation_artifacts"]["inferences"]["path"] == "D:/c/val-inf.jsonl"


def test_manifest_join_digests_come_from_summaries(script):
    manifest = _build(script)
    assert manifest["fit_join"]["join_sha256"] == f"sha256:{240:064d}"
    assert manifest["validation_join"]["join_sha256"] == f"sha256:{120:064d}"
    assert manifest["source_doc_key_overlap"] == []


def test_manifest_metrics_are_validation_not_fit(script):
    """manifest 里的指标必须是 validation 的最终指标（§9.5 第 3 段）。"""
    fit_metrics = _metrics(coverage=1.0, accuracy=1.0, ece=0.0)
    manifest = _build(script, validation_metrics=_metrics(coverage=0.75, accuracy=0.866667))
    assert manifest["metrics"]["coverage"] == 0.75
    assert manifest["metrics"]["accuracy"] == 0.866667
    assert manifest["metrics"]["coverage"] != fit_metrics["coverage"]


def test_manifest_metrics_ece_bins_is_count_not_array(script):
    """§9.3 里 ``ece_bins`` 是档数 10，不是内部 10 元素数组。"""
    manifest = _build(script)
    assert manifest["metrics"]["ece_bins"] == 10


def test_manifest_metrics_include_score_potential_coverage(script):
    manifest = _build(script, validation_metrics=_metrics(score_potential_coverage=1.0))
    assert manifest["metrics"]["score_potential_coverage"] == 1.0


# --- checkpoint_manifest_sha256 -------------------------------------------
def test_checkpoint_digest_excludes_its_own_field(script):
    """§9.3：``checkpoint_manifest_sha256`` = 去掉该字段后的 canonical 摘要。"""
    manifest = dict(_CHECKPOINT_MANIFEST)
    without = script.checkpoint_manifest_digest(manifest)
    manifest["checkpoint_manifest_sha256"] = "sha256:" + "0" * 64
    assert script.checkpoint_manifest_digest(manifest) == without


def test_checkpoint_digest_changes_when_any_other_field_changes(script):
    baseline = script.checkpoint_manifest_digest(_CHECKPOINT_MANIFEST)
    changed = {**_CHECKPOINT_MANIFEST, "checkpoint_id": "laya-english"}
    assert script.checkpoint_manifest_digest(changed) != baseline


# --- 失败路径 --------------------------------------------------------------
def test_missing_checkpoint_manifest_exits_nonzero(script, tmp_path, capsys):
    labels = tmp_path / "l.jsonl"
    labels.write_text("", encoding="utf-8")
    code = script.main(
        [
            "--fit-labels", str(labels),
            "--fit-inferences", str(labels),
            "--validation-labels", str(labels),
            "--validation-inferences", str(labels),
            "--sample-store", str(tmp_path),
            "--checkpoint-manifest", str(tmp_path / "nope.json"),
            "--laya-source", str(tmp_path),
            "--output", str(tmp_path / "out.json"),
        ]
    )
    assert code != 0
    assert "checkpoint-manifest" in capsys.readouterr().err


def test_empty_labels_artifact_exits_nonzero(script, tmp_path, capsys):
    """空 labels 必须在 join 阶段就非零退出，不得写出 manifest。"""
    labels = tmp_path / "l.jsonl"
    labels.write_text("", encoding="utf-8")
    manifest = tmp_path / "cm.json"
    manifest.write_text(json.dumps(_CHECKPOINT_MANIFEST), encoding="utf-8")
    code = script.main(
        [
            "--fit-labels", str(labels),
            "--fit-inferences", str(labels),
            "--validation-labels", str(labels),
            "--validation-inferences", str(labels),
            "--sample-store", str(tmp_path),
            "--checkpoint-manifest", str(manifest),
            "--laya-source", str(tmp_path),
            "--output", str(tmp_path / "out.json"),
        ]
    )
    assert code != 0
    assert not (tmp_path / "out.json").exists()
