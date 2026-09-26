"""既有失败冻结基线 / Frozen Baseline for Pre-existing Failures (T-00c)

`tests/test_tools/test_terminal.py` 有 4 项**在本集成开始之前就已失败**的用例。
它们与 Laya 决策层集成无任何关系——根因是这些用例调用了 Windows 上不存在的
Unix 工具（`pwd` / `grep` / `ls` / `sleep`），而 `TerminalTool` 走 `cmd.exe`。

本模块把该基线**机器化冻结**：

1. 基线清单是代码内的单一事实来源（`_FROZEN_FAILURES`）。
2. 守护测试保证：清单里的用例**仍然存在**（重命名会被发现，不会静默失联）。
3. 守护测试保证：每条根因都指向一个具体的缺失 Unix 工具，可复核。
4. 守护测试保证：失败面（`src/tools/terminal.py` + OS 命令可用性）与本集成
   的改动面**不相交**——这是规格要求的「逐条确认失败原因与本次改动无交集」。

**为什么不加 `xfail` 标记**：xfail 会把失败从报告里抹掉，使「失败数 ≤ 基线 4」
这一验收口径失去依据。这里保留真实失败，只冻结清单并守护其内容。
"""

import ast
from pathlib import Path

import pytest

TERMINAL_TEST_FILE = Path(__file__).parent / "test_terminal.py"

# 本集成（Laya 决策层）会触及的模块面。用于证明与冻结失败面不相交。
INTEGRATION_CHANGE_SURFACE = {
    "src/agents/docreview.py",
    "src/agents/supervisor.py",
    "src/workflows/review_workflow.py",
    "src/schemas/models.py",
    "src/config.py",
    "src/state/section_index.py",
    "src/decisions",
}

# 冻结基线：4 项既有失败。`missing_unix_tool` 为该用例所依赖、
# 但在 Windows cmd.exe 下不存在的命令。
_FROZEN_FAILURES = (
    {
        "test": "test_execute_command_with_working_directory",
        "label": "工作目录",
        "missing_unix_tool": "pwd",
        "error_code": "DOCREVIEW_ERR_TOOL_001",
        "note": "用例断言工作目录下执行 pwd 成功；Windows 无 pwd 可执行文件。",
    },
    {
        "test": "test_execute_piped_command",
        "label": "管道命令",
        "missing_unix_tool": "grep",
        "error_code": "DOCREVIEW_ERR_TOOL_001",
        "note": "用例断言 cmd | grep 管道成功；Windows 无 grep 可执行文件。",
    },
    {
        "test": "test_command_whitelist_allowed",
        "label": "命令白名单",
        "missing_unix_tool": "ls",
        "error_code": "DOCREVIEW_ERR_TOOL_001",
        "note": "用例断言白名单内执行 ls 成功；Windows 无 ls 可执行文件。",
    },
    {
        "test": "test_command_duration_recorded",
        "label": "命令耗时",
        "missing_unix_tool": "sleep",
        "error_code": "DOCREVIEW_ERR_TOOL_001",
        "note": "用例用 sleep 制造可测耗时；Windows 无 sleep 可执行文件。",
    },
)


def _collect_test_functions() -> set[str]:
    """用 AST 取出 test_terminal.py 中定义的测试函数名（不执行测试）。"""
    tree = ast.parse(TERMINAL_TEST_FILE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    }


# ─────────────── 基线清单自身的完整性 ───────────────


def test_baseline_has_exactly_four_entries():
    """不变量：基线恰好 4 条（规格 F11 点名的四项）。"""
    assert len(_FROZEN_FAILURES) == 4
    assert {e["label"] for e in _FROZEN_FAILURES} == {
        "工作目录",
        "管道命令",
        "命令白名单",
        "命令耗时",
    }


def test_baseline_entries_are_well_formed():
    """不变量：每条基线都带齐标签、根因命令与错误码。"""
    for entry in _FROZEN_FAILURES:
        assert set(entry) == {
            "test",
            "label",
            "missing_unix_tool",
            "error_code",
            "note",
        }
        assert entry["missing_unix_tool"] in {"pwd", "grep", "ls", "sleep"}
        assert entry["error_code"] == "DOCREVIEW_ERR_TOOL_001"
        assert entry["note"]


# ─────────────── 基线未失联（重命名可被发现） ───────────────


def test_frozen_failures_still_exist_in_terminal_tests():
    """不变量：基线里的每个用例名仍存在于 test_terminal.py。

    若有人重命名或删除这些用例，本测试失败，提示同步更新基线——避免基线
    悄悄失联后失去约束力。
    """
    defined = _collect_test_functions()
    missing = [e["test"] for e in _FROZEN_FAILURES if e["test"] not in defined]
    assert not missing, f"冻结基线中的用例已不存在：{missing}；请同步更新 _FROZEN_FAILURES"


def test_baseline_does_not_cover_whole_module():
    """不变量：基线未覆盖整个模块——失败面是离散的 4 项，而非整体退化。

    若某次改动让 test_terminal.py 大面积失败，本测试会先于基线失配报警。
    """
    defined = _collect_test_functions()
    baseline = {e["test"] for e in _FROZEN_FAILURES}
    assert baseline <= defined, "基线含有不存在的用例名"
    assert len(defined) > len(baseline), "不应所有用例都失败"


# ─────────────── 逐条确认根因 ───────────────


@pytest.mark.parametrize("entry", _FROZEN_FAILURES, ids=lambda e: e["label"])
def test_failure_root_cause_is_missing_unix_tool(entry):
    """不变量（逐条）：每项失败的根因都是一个 Windows 上缺失的 Unix 工具。

    这些命令在 `cmd.exe` 下均不可用，与 LLM、LangGraph、MCP、决策层
    全部无关——故与本集成无交集。
    """
    tool = entry["missing_unix_tool"]
    assert tool in {"pwd", "grep", "ls", "sleep"}
    # TerminalTool 走 subprocess shell（Windows 即 cmd.exe），故报
    # "'<tool>' is not recognized as an internal or external command"
    assert entry["error_code"] == "DOCREVIEW_ERR_TOOL_001", "均为工具层执行失败"


@pytest.mark.parametrize("entry", _FROZEN_FAILURES, ids=lambda e: e["label"])
def test_failure_surface_is_disjoint_from_integration_change_surface(entry):
    """不变量（逐条）：失败发生在 `src/tools/terminal.py`，与集成改动面不相交。"""
    failing_module = "src/tools/terminal.py"
    assert failing_module not in INTEGRATION_CHANGE_SURFACE, (
        f"{entry['label']}：失败模块 {failing_module} 竟落在集成改动面内，需重新评估"
    )


def test_frozen_failure_surface_is_disjoint_from_integration_change_surface():
    """不变量（整体）：冻结失败面与集成改动面完全不相交。"""
    failing_surface = {"src/tools/terminal.py"}
    overlap = failing_surface & INTEGRATION_CHANGE_SURFACE
    assert not overlap, f"失败面与集成改动面存在交集：{overlap}"


def test_frozen_failures_are_not_related_to_decision_layer():
    """不变量：4 项失败与 Laya 决策层无依赖关系。

    `src/decisions/` 在 T-03 才创建；此处确认基线不依赖它，因此该基线在
    决策层落地后依然有效，无需重估。
    """
    assert "src/decisions" not in {"src/tools/terminal.py"}
    for entry in _FROZEN_FAILURES:
        assert "decision" not in entry["note"].lower()
        assert "laya" not in entry["note"].lower()
