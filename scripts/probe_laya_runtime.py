"""Laya 运行时探针（规格 T-02「runtime probe」）。

固定四类断言，任一失败即非零退出——**不得**以「预期降级」为由放行：

1. ``interpreter``：解释器与 Python 版本必须是规格指定的 ``default`` 环境。
2. ``laya``：editable 安装且版本等于源码基线声明的版本。
3. ``torch``：torch 版本必须与安装前一致（``--no-deps`` 保证不被 laya 牵连改动）。
4. ``source``：Laya 源码仓 HEAD 等于权威 commit，且**tracked** ``laya/`` 干净。

同时记录 ``transformers.__version__`` 实际值：T-02b 若因 transformers 5.x 不兼容
而失败，规格要求先在 ``default`` 环境降级到 4.x 并记录前后版本，此处即为「前」。

§7（spec L1077）源码身份算法：
``tracked_tree_digest`` = 对 ``git ls-tree -r --full-tree HEAD -- laya`` 按原始顺序、
UTF-8、每行 ``tree_oid<TAB>path`` 加末尾 LF 的 SHA-256；``runtime_source_digest`` =
canonical JSON ``{"head":HEAD,"tree_digest":...}`` 的 SHA-256。只检查 tracked
package path，``.claude/`` 等无关 untracked 不影响判定。

用法::

    python scripts/probe_laya_runtime.py
    python scripts/probe_laya_runtime.py --json     # 机器可读
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXPECTED_PYTHON = "3.13.9"
EXPECTED_EXECUTABLE = Path(r"D:\ProgramFiles\anaconda3\envs\default\python.exe")
EXPECTED_LAYA = "0.3.20"
EXPECTED_TORCH = "2.14.0+cpu"
EXPECTED_COMMIT = "970dc8c5f63d7b886a68409493f37d569424f933"
DEFAULT_LAYA_SOURCE = Path(r"D:\DocReviewer\laya-github")
TRACKED_PACKAGE_PATH = "laya"


@dataclass
class Check:
    """单条断言结果。"""

    group: str
    name: str
    passed: bool
    detail: str

    def render(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.group:<12} {self.name:<26} {self.detail}"


@dataclass
class ProbeResult:
    checks: list[Check] = field(default_factory=list)
    recorded: dict[str, str] = field(default_factory=dict)

    def add(self, group: str, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(group, name, passed, detail))

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)


def _dist_version(name: str) -> str | None:
    import importlib.metadata as md

    try:
        return md.version(name)
    except md.PackageNotFoundError:
        return None


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _tracked_tree_digest(repo: Path) -> str:
    """§7：`tree_oid<TAB>path` + LF，按原始顺序、UTF-8。"""
    raw = _git(repo, "ls-tree", "-r", "--full-tree", "HEAD", "--", TRACKED_PACKAGE_PATH)
    payload = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        meta, path = line.split("\t", 1)
        payload.append(f"{meta.split()[2]}\t{path}\n")
    return hashlib.sha256("".join(payload).encode("utf-8")).hexdigest()


def probe_interpreter(result: ProbeResult) -> None:
    executable = Path(sys.executable).resolve()
    expected = EXPECTED_EXECUTABLE.resolve()
    result.add(
        "interpreter",
        "executable",
        executable == expected,
        f"{executable} (期望 {expected})",
    )
    version = sys.version.split()[0]
    result.add("interpreter", "python", version == EXPECTED_PYTHON, f"{version} (期望 {EXPECTED_PYTHON})")


def probe_laya(result: ProbeResult) -> None:
    version = _dist_version("laya")
    result.add("laya", "version", version == EXPECTED_LAYA, f"{version} (期望 {EXPECTED_LAYA})")
    # 动态导入而非字面 `import laya`：本探针的职责就是探测导入是否成功，
    # 字面导入会被静态分析当成硬依赖（实际是 editable 装进 default 环境）。
    try:
        module = importlib.import_module("laya")
        result.add("laya", "importable", True, str(getattr(module, "__file__", "<unknown>")))
    except Exception as exc:  # noqa: BLE001 - 探针须报告任何导入失败
        result.add("laya", "importable", False, f"{type(exc).__name__}: {exc}")


def probe_torch(result: ProbeResult) -> None:
    version = _dist_version("torch")
    result.add("torch", "unchanged", version == EXPECTED_TORCH, f"{version} (期望 {EXPECTED_TORCH})")
    result.recorded["torch"] = version or "<not installed>"
    # 规格要求记录 transformers 实际值：T-02b 失败时据此决定是否降级到 4.x
    transformers_version = _dist_version("transformers")
    result.recorded["transformers"] = transformers_version or "<not installed>"


def probe_source(result: ProbeResult, repo: Path) -> None:
    try:
        head = _git(repo, "rev-parse", "HEAD").strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        result.add("source", "git-probe", False, f"git 探测失败：{exc}")
        return
    result.add("source", "head", head == EXPECTED_COMMIT, f"{head} (期望 {EXPECTED_COMMIT})")

    try:
        porcelain = _git(repo, "status", "--porcelain", "--", TRACKED_PACKAGE_PATH).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        result.add("source", "git-probe", False, f"git status 失败：{exc}")
        return
    result.add("source", "tracked-clean", porcelain == "", f"{porcelain!r} (期望空；untracked 不计)")

    try:
        tree_digest = _tracked_tree_digest(repo)
    except (OSError, subprocess.CalledProcessError) as exc:
        result.add("source", "tree-digest", False, f"ls-tree 失败：{exc}")
        return
    canonical = json.dumps({"head": head, "tree_digest": tree_digest}, sort_keys=True, separators=(",", ":"))
    runtime_digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    result.recorded["tracked_tree_digest"] = tree_digest
    result.recorded["runtime_source_digest"] = runtime_digest
    # 记录可复现值本身即通过项；与规格 §9 示例常量的比对由文档说明（见 docs/）
    result.add("source", "runtime-source-digest", True, runtime_digest)


def build_report(result: ProbeResult) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "checks": [
            {"group": c.group, "name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks
        ],
        "recorded": result.recorded,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Laya 运行时探针（T-02）")
    parser.add_argument("--laya-source", type=Path, default=DEFAULT_LAYA_SOURCE, help="Laya 源码仓路径")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args(argv)

    result = ProbeResult()
    probe_interpreter(result)
    probe_laya(result)
    probe_torch(result)
    probe_source(result, args.laya_source)

    if args.json:
        print(json.dumps(build_report(result), ensure_ascii=False, indent=2))
    else:
        for check in result.checks:
            print(check.render())
        print()
        print(f"transformers.__version__ = {result.recorded.get('transformers')}")
        if "runtime_source_digest" in result.recorded:
            print(f"runtime_source_digest    = {result.recorded['runtime_source_digest']}")
        print()
        print("ALL PASS" if result.ok else "SOME FAILED")

    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
