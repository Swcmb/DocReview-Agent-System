"""生成/验证 Laya checkpoint manifest（规格 §7.3，T-02）。

**只读**扫描选定 checkpoint root，产出行为文件摘要；**绝不写入**权重仓库
`laya-huggingface`——输出路径若落在 bundle 内直接拒绝。

用法::

    # 生成（§19.2）
    python scripts/build_laya_checkpoint_manifest.py \\
        --model-dir D:/DocReviewer/laya-huggingface \\
        --model multilingual \\
        --output D:/controlled/laya_checkpoint_manifest.json

    # 验证（重算并与既有 manifest 逐字段比对）
    python scripts/build_laya_checkpoint_manifest.py \\
        --model-dir D:/DocReviewer/laya-huggingface \\
        --model multilingual \\
        --output D:/controlled/laya_checkpoint_manifest.json --verify

`--model-dir` 传的是 bundle 根，按 §7.3 解析到实际 checkpoint 目录：
``english``→bundle 根、``multilingual``→``bundle/multilingual``、
``typed-decisions``→``bundle/typed-decisions``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
WEIGHTS_FILE = "model.safetensors"
AGENT_CONFIG_FILE = "rl_agent_config.json"
CHECKSUM_PREFIX = "sha256:"
READ_CHUNK = 1024 * 1024

# §7.3：model -> 相对 bundle 根的 checkpoint 子目录。english 即 bundle 根本身。
MODEL_SUBDIR: dict[str, str] = {
    "english": "",
    "multilingual": "multilingual",
    "typed-decisions": "typed-decisions",
}

# 字段顺序即 §7.3 schema 顺序；canonical JSON 必须按此顺序序列化。
MANIFEST_FIELDS = (
    "schema_version",
    "model",
    "checkpoint_id",
    "runtime_commit",
    "root",
    "files",
    "all_behavior_files_covered",
)


class ManifestError(RuntimeError):
    """manifest 生成/验证失败（调用方应 fail closed，不得降级放行）。"""


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256，避免把 600MB 权重读进内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    """§7.3 canonical JSON：schema 字段顺序 + 紧凑分隔符 + 不转义非 ASCII。"""
    ordered = {key: manifest[key] for key in MANIFEST_FIELDS}
    return json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def manifest_digest(manifest: dict[str, Any]) -> str:
    """`checkpoint_manifest_sha256`：去掉该字段后的 canonical JSON 摘要。"""
    payload = {k: v for k, v in manifest.items() if k != "checkpoint_manifest_sha256"}
    return CHECKSUM_PREFIX + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def select_checkpoint_root(bundle: Path, model: str) -> Path:
    """把 bundle 根解析为实际 checkpoint 目录（§7.3 映射）。"""
    if model not in MODEL_SUBDIR:
        raise ManifestError(f"未知 model：{model!r}；允许 {sorted(MODEL_SUBDIR)}")
    root = (bundle / MODEL_SUBDIR[model]).resolve() if MODEL_SUBDIR[model] else bundle.resolve()
    if not root.is_dir():
        raise ManifestError(f"checkpoint 目录不存在：{root}")
    return root


def is_inside(candidate: Path, parent: Path) -> bool:
    """`candidate` 是否位于 `parent` 之内（用于 symlink 逃逸与写保护）。"""
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def scan_behavior_files(root: Path, cache: dict[str, Any] | None) -> list[dict[str, Any]]:
    """递归收集 root 下全部 regular file，按 path UTF-8 字典序排列。

    摘要按 `(path, size, mtime_ns)` 缓存；任一项变化即重算（§7.3）。
    symlink 若指向 root 之外则 fail closed。
    """
    entries: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.is_symlink() and not is_inside(path, root):
            raise ManifestError(f"symlink 指向 checkpoint 根目录之外：{path}")
        if not is_inside(path, root):
            raise ManifestError(f"文件逃出 checkpoint 根目录：{path}")

        stat = path.stat()
        rel = PurePosixPath(path.relative_to(root).as_posix()).as_posix()
        if rel.startswith("../") or rel == "..":
            raise ManifestError(f"files 中出现上级路径：{rel}")

        digest: str | None = None
        if cache is not None:
            hit = cache.get(rel)
            if (
                isinstance(hit, dict)
                and hit.get("size") == stat.st_size
                and hit.get("mtime_ns") == stat.st_mtime_ns
            ):
                digest = hit.get("sha256")
        if digest is None:
            digest = CHECKSUM_PREFIX + sha256_file(path)
            if cache is not None:
                cache[rel] = {
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": digest,
                }
        entries.append({"path": rel, "size": stat.st_size, "sha256": digest})

    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    return entries


def assert_behavior_coverage(root: Path, entries: list[dict[str, Any]]) -> None:
    """§7.3：manifest 必须覆盖权重、agent config、tokenizer、encoder/config。

    缺任一关键文件即 fail closed——漏文件会让 digest 校验形同虚设。
    """
    paths = {item["path"] for item in entries}
    if WEIGHTS_FILE not in paths:
        raise ManifestError(f"缺少权重文件 {WEIGHTS_FILE}")
    if AGENT_CONFIG_FILE not in paths:
        raise ManifestError(f"缺少 {AGENT_CONFIG_FILE}")
    if not any(p.startswith("tokenizer/") for p in paths):
        raise ManifestError("缺少 tokenizer 文件")
    if not any(p.startswith("encoder/") for p in paths):
        raise ManifestError("缺少 encoder/config 文件")


def read_runtime_commit(laya_source: Path) -> str:
    """从 Laya 源码仓读取 HEAD 作为 `runtime_commit`（§9.2 权威 commit）。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(laya_source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ManifestError(f"无法读取 Laya runtime commit：{exc}") from exc
    return proc.stdout.strip()


def load_cache(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return {} if path is not None else None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_cache(path: Path | None, cache: dict[str, Any] | None) -> None:
    if path is None or cache is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")


def build_manifest(bundle: Path, model: str, laya_source: Path, cache: dict[str, Any] | None) -> dict[str, Any]:
    """组装符合 §7.3 schema 的 manifest。"""
    root = select_checkpoint_root(bundle, model)
    entries = scan_behavior_files(root, cache)
    if not entries:
        raise ManifestError(f"checkpoint 根目录没有任何 regular file：{root}")
    assert_behavior_coverage(root, entries)

    weights = next(item for item in entries if item["path"] == WEIGHTS_FILE)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "model": model,
        "checkpoint_id": f"{model}:{weights['sha256']}",
        "runtime_commit": read_runtime_commit(laya_source),
        "root": root.as_posix(),
        "files": entries,
        "all_behavior_files_covered": True,
    }
    manifest["checkpoint_manifest_sha256"] = manifest_digest(manifest)
    return manifest


def _index_files(files: list[Any]) -> dict[str, dict[str, Any]]:
    """按 `path` 建立索引；只接受 str 路径，顺带滤掉非法条目。"""
    index: dict[str, dict[str, Any]] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str):
            index[path] = item
    return index


def _diff_files(existing: list[dict[str, Any]], rebuilt: list[dict[str, Any]]) -> list[str]:
    """按 path 对齐两个 files 列表，产出紧凑的逐文件差异描述。"""
    old_by_path = _index_files(existing)
    new_by_path = _index_files(rebuilt)
    problems: list[str] = []

    for path in sorted(old_by_path.keys() - new_by_path.keys()):
        problems.append(f"files：manifest 多出 {path}")
    for path in sorted(new_by_path.keys() - old_by_path.keys()):
        problems.append(f"files：manifest 缺少 {path}")
    for path in sorted(old_by_path.keys() & new_by_path.keys()):
        old, new = old_by_path[path], new_by_path[path]
        for field in ("size", "sha256"):
            if old.get(field) != new.get(field):
                problems.append(f"files[{path}].{field}：manifest={old.get(field)!r} 重算={new.get(field)!r}")
    return problems


def verify(existing: dict[str, Any], rebuilt: dict[str, Any]) -> list[str]:
    """逐字段比对既有 manifest 与重算结果，返回差异描述列表。

    `checkpoint_manifest_sha256` 是**派生**值（§7.3「去掉该字段后的 canonical JSON
    摘要」），§7.3 schema 并不强制落盘该字段。故：落盘了就直接比；没落盘就从既有
    manifest 重算后再比——两种形态都接受，但都必须与重算值一致。
    """
    problems: list[str] = []
    for field in MANIFEST_FIELDS:
        if field == "files":
            continue
        want, got = existing.get(field), rebuilt.get(field)
        if want != got:
            problems.append(f"{field}：manifest={want!r} 重算={got!r}")

    old_files = existing.get("files")
    if isinstance(old_files, list):
        problems.extend(_diff_files(old_files, rebuilt["files"]))
    else:
        problems.append(f"files：manifest={old_files!r}（类型非法，应为 list）")

    want_digest = existing.get("checkpoint_manifest_sha256")
    if want_digest is None:
        want_digest = manifest_digest(existing)
    got_digest = rebuilt["checkpoint_manifest_sha256"]
    if want_digest != got_digest:
        problems.append(f"checkpoint_manifest_sha256：manifest={want_digest!r} 重算={got_digest!r}")
    return problems


def guard_output_path(output: Path, bundle: Path) -> None:
    """§7.3：manifest 不得写入 `laya-huggingface` 权重仓库。"""
    if is_inside(output, bundle):
        raise ManifestError(f"输出路径落在权重仓库内，拒绝写入：{output}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成/验证 Laya checkpoint manifest（§7.3）")
    parser.add_argument("--model-dir", required=True, type=Path, help="权重 bundle 根目录")
    parser.add_argument("--model", required=True, choices=sorted(MODEL_SUBDIR), help="逻辑模型名")
    parser.add_argument("--output", required=True, type=Path, help="manifest 输出路径（必须在 bundle 外）")
    parser.add_argument("--laya-source", type=Path, default=Path(r"D:\DocReviewer\laya-github"), help="Laya 源码仓")
    parser.add_argument("--digest-cache", type=Path, default=None, help="摘要缓存 JSON（按 path/size/mtime_ns 失效）")
    parser.add_argument("--verify", action="store_true", help="只验证既有 manifest，不写出")
    args = parser.parse_args(argv)

    bundle = args.model_dir.resolve()
    if not bundle.is_dir():
        print(f"[FAIL] 权重 bundle 不存在：{bundle}", file=sys.stderr)
        return 2

    cache = load_cache(args.digest_cache)
    try:
        guard_output_path(args.output, bundle)
        rebuilt = build_manifest(bundle, args.model, args.laya_source, cache)
    except ManifestError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.verify:
        if not args.output.is_file():
            print(f"[FAIL] 待验证 manifest 不存在：{args.output}", file=sys.stderr)
            return 2
        try:
            # utf-8-sig：容忍 Windows 工具写出的 BOM，同时兼容无 BOM 的 canonical 文件
            existing = json.loads(args.output.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[FAIL] manifest 解析失败：{exc}", file=sys.stderr)
            return 2
        problems = verify(existing, rebuilt)
        if problems:
            for item in problems:
                print(f"[FAIL] {item}", file=sys.stderr)
            return 1
        print(f"[PASS] manifest 校验一致：{rebuilt['checkpoint_manifest_sha256']}")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 固定用 "\n" 而非 os.linesep：manifest 参与 digest，字节需跨平台可复现
    args.output.write_text(canonical_bytes(rebuilt).decode("utf-8") + "\n", encoding="utf-8")
    save_cache(args.digest_cache, cache)
    print(f"[OK] {rebuilt['checkpoint_manifest_sha256']}")
    print(f"     root={rebuilt['root']}")
    print(f"     files={len(rebuilt['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
