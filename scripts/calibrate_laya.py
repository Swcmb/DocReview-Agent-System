"""§9.5 校准评估脚本：四个 artifact → fit 选阈值 → validation 最终指标 → manifest。

流程严格分三段，任何一段失败立即非零退出：

1. **join**：fit 与 validation 各自做一一 join / path+sha256 / identity / state_digest
   校验（§9.1），再校验 split 规模与文档 key 交集（§9.4）。
2. **选阈值**：**只在 fit join 上**搜索固定网格（§9.4）。validation 至此不参与任何
   排序——``select_thresholds()`` 的签名里就没有 validation 参数，这是结构性保证。
3. **出指标**：用 fit 选出的阈值在 validation join 上算最终指标，写 canonical
   manifest 与 calibration envelope（§9.2／§9.3）。

**规格空缺处置**：§9.3 的 manifest 示例只给了单数 ``labels_artifact`` /
``inferences_artifact``，但 §9.5 的 CLI 收四个 artifact（fit 与 validation 各一对），
示例里的 fit=240 / validation=120 也不可能来自同一个文件。此处按 split 显式记录
``fit_artifacts`` / ``validation_artifacts``，并让单数字段指向 **fit** 对以保持与
示例 key 兼容。若上游日后给出单数字段的确切口径，只需改这一处。

**导入边界**：本脚本**不是** ``src.decisions`` 的一部分，故允许惰性 import
``laya.presets`` 取 guard 三轮的 schema hash。但该 import 失败时**不静默降级**——
guard hash 缺一项会让 manifest 与运行时校验口径不一致，故直接非零退出，并提示
在装有 laya 的环境运行。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import LayaConfig
from src.decisions.calibration import (
    SCHEMA_VERSION,
    CalibrationError,
    JoinSummary,
    Metrics,
    Thresholds,
    canonical_digest,
    compute_metrics,
    file_sha256,
    load_and_join,
    select_thresholds,
    summary_of,
    validate_split_sizing,
)
from src.decisions.question_contracts import (
    FROZEN_BUSINESS_QUESTIONS,
    PRIMITIVE_ORDER,
    canonical_json_bytes,
    schema_descriptor,
    sha256_canonical,
)

MANIFEST_ID = "laya-calibration-v1"
CHECKSUM_PREFIX = "sha256:"


class CalibrationScriptError(Exception):
    """脚本自身的失败（与 artifact 校验失败区分开）。"""


def _git_text(*args: str, cwd: Path) -> str:
    """跑一条 git 命令并返回 stdout；失败即抛，不吞。"""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # git 不在 PATH
        raise CalibrationScriptError(f"无法执行 git：{exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CalibrationScriptError(f"git {' '.join(args)} 失败：{detail}")
    return completed.stdout.decode("utf-8", errors="strict")


def runtime_source_digest(laya_source: Path) -> str:
    """按 §7／docs §4 重算 ``runtime_source_digest``。

    ``tracked_tree_digest`` = ``git ls-tree -r --full-tree HEAD -- laya`` 的**原始顺序**、
    UTF-8、每行 ``tree_oid<TAB>path`` 加末尾 LF 的 SHA-256；``runtime_source_digest``
    = canonical JSON ``{"head":HEAD,"tree_digest":...}`` 的 SHA-256。

    刻意**不**硬编码任何 digest：规格示例里的 ``c80e800b…`` 与实测不符
    （docs §6 已记录该不一致），生产必须按实际 source tree 重算。
    """
    dirty = _git_text("status", "--porcelain", "--", "laya", cwd=laya_source).strip()
    if dirty:
        raise CalibrationScriptError(
            "laya tracked package 有未提交改动，源码身份不可复现：\n" + dirty
        )
    head = _git_text("rev-parse", "HEAD", cwd=laya_source).strip()
    listing = _git_text("ls-tree", "-r", "--full-tree", "HEAD", "--", "laya", cwd=laya_source)
    lines = [line for line in listing.replace("\r\n", "\n").split("\n") if line != ""]
    tree_payload = ("\n".join(lines) + "\n").encode("utf-8")
    tree_digest = hashlib.sha256(tree_payload).hexdigest()
    return canonical_digest({"head": head, "tree_digest": tree_digest})


def question_schema_hashes() -> dict[str, str]:
    """六个原语的 order-aware schema hash（§5.2 golden）。

    五个业务原语取冻结契约；``guard`` 必须来自 ``laya.presets.guard_questions()``
    （§5.3 禁止复刻一份可变副本），故在此惰性 import。
    """
    hashes = {
        primitive: sha256_canonical(schema_descriptor(FROZEN_BUSINESS_QUESTIONS[primitive]))
        for primitive in PRIMITIVE_ORDER
    }
    try:
        presets = importlib.import_module("laya.presets")
    except ImportError as exc:
        raise CalibrationScriptError(
            "无法 import laya.presets 以计算 guard schema hash；"
            "请在装有 laya 的环境运行本脚本（不得跳过该 hash）。"
        ) from exc
    hashes["guard"] = sha256_canonical(schema_descriptor(dict(presets.guard_questions())))
    return hashes


def checkpoint_manifest_digest(manifest: dict[str, Any]) -> str:
    """``checkpoint_manifest_sha256`` = 去掉该字段后的 canonical JSON 摘要。"""
    payload = {k: v for k, v in manifest.items() if k != "checkpoint_manifest_sha256"}
    return canonical_digest(payload)


def _artifact(path: Path) -> dict[str, str]:
    return {"path": path.as_posix(), "sha256": file_sha256(path)}


def _as_posix_absolute(path: Path) -> str:
    """manifest 里的路径必须是绝对路径（§9.2）。"""
    if not path.is_absolute():
        raise CalibrationScriptError(f"路径必须为绝对路径：{path}")
    return path.as_posix()


def _manifest_metrics(metrics: Metrics) -> dict[str, Any]:
    """把内部 ``Metrics`` 压成 manifest 里的指标摘要（§9.3）。

    注意 ``ece_bins`` 在 manifest 里是**档数**（10），不是内部那份 10 元素数组——
    完整 bin 明细只在校准报告里需要，manifest 保持可读的标量摘要。
    ``score_potential_coverage`` 是 T-10 新增的必报项（§9.4：score 恒 uncertain，
    potential 只作离线诊断），故一并写入。
    """
    return {
        "coverage": metrics["coverage"],
        "accuracy": metrics["accuracy"],
        "ece": metrics["ece"],
        "ece_bins": len(metrics["ece_bins"]),
        "per_class": metrics["per_class"],
        "score_potential_coverage": metrics["score_potential_coverage"],
        "decided_count": metrics["decided_count"],
        "sample_count": metrics["sample_count"],
    }


def build_manifest(
    *,
    created_at: str,
    checkpoint_manifest: dict[str, Any],
    runtime_source_digest_value: str,
    question_hashes: dict[str, str],
    fit_artifacts: dict[str, dict[str, str]],
    validation_artifacts: dict[str, dict[str, str]],
    fit_summary: JoinSummary,
    validation_summary: JoinSummary,
    thresholds: Thresholds,
    validation_metrics: Metrics,
    max_batch_encoded_chars: int,
) -> dict[str, Any]:
    """按 §9.3 组装 canonical manifest。

    单数 ``labels_artifact`` / ``inferences_artifact`` 指向 **fit** 对，以保持与
    规格示例的 key 兼容；``fit_artifacts`` / ``validation_artifacts`` 才是两侧的
    权威记录（规格示例的 fit=240 / validation=120 不可能出自同一文件，故单数字段
    不足以表达四 artifact 契约——详见模块 docstring 的「规格空缺处置」）。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": MANIFEST_ID,
        "created_at": created_at,
        "laya_runtime_commit": str(checkpoint_manifest["runtime_commit"]),
        "runtime_source_digest": runtime_source_digest_value,
        "model": str(checkpoint_manifest["model"]),
        "checkpoint_manifest_sha256": checkpoint_manifest_digest(checkpoint_manifest),
        "labels_artifact": fit_artifacts["labels"],
        "inferences_artifact": fit_artifacts["inferences"],
        "fit_artifacts": fit_artifacts,
        "validation_artifacts": validation_artifacts,
        "question_schema_hashes": question_hashes,
        "fit_join": fit_summary,
        "validation_join": validation_summary,
        "source_doc_key_overlap": [],
        "thresholds": dict(thresholds),
        "metrics": _manifest_metrics(validation_metrics),
        "max_batch_encoded_chars": max_batch_encoded_chars,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="§9.5 Laya 校准：fit 选阈值 → validation 最终指标 → canonical manifest"
    )
    parser.add_argument("--fit-labels", required=True, type=Path)
    parser.add_argument("--fit-inferences", required=True, type=Path)
    parser.add_argument("--validation-labels", required=True, type=Path)
    parser.add_argument("--validation-inferences", required=True, type=Path)
    parser.add_argument(
        "--sample-store",
        required=True,
        type=Path,
        help="受控本地样本库根目录；两侧 state_ref.path 都必须落在其内",
    )
    parser.add_argument(
        "--checkpoint-manifest",
        required=True,
        type=Path,
        help="受控 checkpoint manifest；提供 model / checkpoint_id / runtime_commit",
    )
    parser.add_argument(
        "--laya-source",
        required=True,
        type=Path,
        help="Laya 源码树（git 仓库），用于按 §7 重算 runtime_source_digest",
    )
    parser.add_argument("--output", required=True, type=Path, help="calibration envelope 落盘路径")
    parser.add_argument(
        "--created-at",
        default=None,
        help="ISO-8601 时间戳；缺省用当前时间。测试固定传入以保证可复现。",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """成功 0；任一校验或脚本失败非零（§9.5）。"""
    args = _parse_args(argv)
    try:
        for label, path in (
            ("--fit-labels", args.fit_labels),
            ("--fit-inferences", args.fit_inferences),
            ("--validation-labels", args.validation_labels),
            ("--validation-inferences", args.validation_inferences),
            ("--checkpoint-manifest", args.checkpoint_manifest),
        ):
            if not path.is_file():
                raise CalibrationScriptError(f"{label} 不存在或不是文件：{path}")

        fit_joined = load_and_join(
            args.fit_labels, args.fit_inferences, store_root=args.sample_store
        )
        validation_joined = load_and_join(
            args.validation_labels, args.validation_inferences, store_root=args.sample_store
        )
        # split 规模 / 类别最小样本 / 文档 key 交集——在选阈值**之前**拒绝不合规数据。
        validate_split_sizing(fit_joined, validation_joined)

        # 阈值只在 fit join 上选；validation 从此只读。
        thresholds, fit_metrics = select_thresholds(fit_joined)
        validation_metrics = compute_metrics(validation_joined, thresholds)

        checkpoint_manifest = json.loads(args.checkpoint_manifest.read_text(encoding="utf-8"))
        config = LayaConfig()
        manifest = build_manifest(
            created_at=args.created_at or datetime.now().astimezone().isoformat(timespec="seconds"),
            checkpoint_manifest=checkpoint_manifest,
            runtime_source_digest_value=runtime_source_digest(args.laya_source),
            question_hashes=question_schema_hashes(),
            fit_artifacts={
                "labels": _artifact(args.fit_labels),
                "inferences": _artifact(args.fit_inferences),
            },
            validation_artifacts={
                "labels": _artifact(args.validation_labels),
                "inferences": _artifact(args.validation_inferences),
            },
            fit_summary=summary_of(fit_joined),
            validation_summary=summary_of(validation_joined),
            thresholds=thresholds,
            validation_metrics=validation_metrics,
            max_batch_encoded_chars=config.max_batch_encoded_chars,
        )

        manifest_path = args.output.parent / "laya_calibration_manifest.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")

        envelope = {
            "schema_version": SCHEMA_VERSION,
            "manifest_path": _as_posix_absolute(manifest_path),
            "manifest": manifest,
            "manifest_sha256": canonical_digest(manifest),
            "fit_dataset_sha256": manifest["fit_join"]["join_sha256"],
            "validation_dataset_sha256": manifest["validation_join"]["join_sha256"],
            "thresholds": dict(thresholds),
            "metrics": _manifest_metrics(validation_metrics),
            "fit_metrics": _manifest_metrics(fit_metrics),
            "records": [],
        }
        # calibration_id 必须是内容摘要的前 16 位，不能人工填写（§9.2）。
        envelope["calibration_id"] = "calibration-v1-" + canonical_digest(envelope)[7:23]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json_bytes(envelope) + b"\n")
    except CalibrationError as exc:
        print(f"FAIL {exc.code}: {exc.detail}", file=sys.stderr)
        return 1
    except CalibrationScriptError as exc:
        print(f"FAIL script: {exc}", file=sys.stderr)
        return 2

    print(f"OK {envelope['calibration_id']} thresholds={thresholds}")
    print(f"   fit coverage={fit_metrics['coverage']} accuracy={fit_metrics['accuracy']}")
    print(
        f"   validation coverage={validation_metrics['coverage']} "
        f"accuracy={validation_metrics['accuracy']} ece={validation_metrics['ece']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
