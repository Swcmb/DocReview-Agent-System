"""T-14：独立 issue ID 分配与报告流（规格 §3.3）。

本文件锁三件事：

1. **空/缺失 ID 是合法「未分配」**，原位覆盖生成 ID——覆盖真实
   structured/loose parser 输出（两者都产出 `issue_id=""`）。
2. **只拒绝四类必要错误**：非法 severity、非空重复 ID、空 description、
   空 issue_type。
3. **Markdown 编译器无业务副作用**：不分配 ID、不改 issue。

L-03 实施纪律（§15.1）：「非法 severity」与「非空重复 ID」在
`assign_issue_ids()` 的合法调用路径上不可达——正常流程的 issue 由各审查
步骤构造，severity 来自受控枚举且 ID 由本函数保证唯一。因此这两个用例
**直接构造 issue dict** 再调 `validate_issues()`，触达拒绝分支。
写成「先跑 `assign_issue_ids()` 再断言抛错」是假测试，永远绿。
"""

from typing import Any, cast

import pytest

from src.agents.docreview import DocReviewAgent
from src.decisions.issue_id import IssueIdError, assign_issue_ids, validate_issues
from src.schemas.models import (
    SEVERITY_BLOCKING,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    VALID_SEVERITIES,
    IssueStatus,
    generate_issue_id,
)

# ─────────────────────────── 测试替身 ───────────────────────────


def _agent() -> DocReviewAgent:
    """解析器与报告编译器都不碰 LLM，llm=None 即可。"""
    return DocReviewAgent(llm=None, sequential_thinking=None, context7=None, tools=[])


def _issue(
    severity: str = SEVERITY_MEDIUM,
    *,
    issue_id: str | None = None,
    description: str = "描述",
    issue_type: str = "ConsistencyCheck",
) -> IssueStatus:
    """构造一个最小合法 issue；`issue_id=None` 表示**缺失该键**。

    这里刻意用裸 dict 再 cast：`IssueStatus` 是 ``total=True`` TypedDict，
    声明上 7 个键全为必填，无法在类型层表达「缺失 issue_id」。但规格 §3.3
    明确「空字符串或**缺失** issue_id 视为合法未分配」——真实 LLM 解析出的
    dict 确实可能没有这个键，所以运行时必须容忍。用 cast 而不是改 TypedDict
    为 NotRequired，是为了不顺手放宽全代码库的必填契约（那会让所有
    ``issue["issue_id"]`` 读取重新面对 KeyError 风险，超出 T-14 范围）。
    """
    raw: dict[str, str] = {
        "severity": severity,
        "issue_type": issue_type,
        "description": description,
        "suggestion": "修订建议",
        "location": "规格文档",
        "status": "open",
    }
    if issue_id is not None:
        raw["issue_id"] = issue_id
    return cast("IssueStatus", raw)


# ─────────────────── 1. 空/缺失 ID 原位覆盖（合法路径） ───────────────────


def test_structured_parser_output_all_gets_ids():
    """真实 structured parser 输出的 issue_id 均为空串，须被全量分配。"""
    agent = _agent()
    text = (
        "[ISSUE] type=ConsistencyCheck severity=High "
        "description=术语不一致 location=2.1 suggestion=统一术语\n"
        "[ISSUE] type=ConsistencyCheck severity=Blocking "
        "description=缺少验收标准 location=3.0 suggestion=补充验收标准\n"
    )
    issues = agent._parse_structured_issues(text)

    assert len(issues) == 2
    # 解析器确实产出空 ID（这是本任务要覆盖的真实输入形状）。
    assert [i["issue_id"] for i in issues] == ["", ""]

    assign_issue_ids(issues, 1)

    assert [i["issue_id"] for i in issues] == ["HI-1-1", "BK-1-1"]


def test_loose_parser_output_empty_ids_overwritten_in_place():
    """loose parser 同样产出空 ID；缺失/空串都要原位覆盖。"""
    agent = _agent()
    text = "问题: 缺少验收标准\n\n风险: 依赖不可用\n"
    issues = agent._parse_loose_issues(text, ["ConsistencyCheck"], SEVERITY_MEDIUM)

    assert len(issues) == 2
    assert all(i["issue_id"] == "" for i in issues)

    returned = assign_issue_ids(issues, 2)

    assert [i["issue_id"] for i in issues] == ["MD-2-1", "MD-2-2"]
    # 返回同一个列表对象，便于链式使用。
    assert returned is issues


def test_missing_issue_id_key_is_assigned():
    """**缺失** issue_id 键（不是空串）同样是合法未分配。"""
    issues = [_issue(SEVERITY_BLOCKING), _issue(SEVERITY_LOW)]
    assert "issue_id" not in issues[0]

    assign_issue_ids(issues, 3)

    assert [i["issue_id"] for i in issues] == ["BK-3-1", "LO-3-1"]


def test_existing_non_empty_id_is_preserved():
    """已有合法 ID 原样保留，不重新编号、不被覆盖。"""
    issues = [
        _issue(SEVERITY_HIGH, issue_id="HI-1-7"),
        _issue(SEVERITY_HIGH),
    ]
    assign_issue_ids(issues, 1)

    assert issues[0]["issue_id"] == "HI-1-7"
    # 计数器不受已占用 ID 影响：新分配的仍从 1 起。
    assert issues[1]["issue_id"] == "HI-1-1"


def test_whitespace_only_id_treated_as_unassigned():
    """纯空白 ID 等价于未分配（`.strip()` 判定）。"""
    issues = [_issue(SEVERITY_MEDIUM, issue_id="   ")]
    assign_issue_ids(issues, 1)

    assert issues[0]["issue_id"] == "MD-1-1"


def test_per_severity_counters_are_independent():
    """四级计数器各自独立，不共享序号。"""
    issues = [
        _issue(SEVERITY_LOW),
        _issue(SEVERITY_HIGH),
        _issue(SEVERITY_LOW),
        _issue(SEVERITY_BLOCKING),
        _issue(SEVERITY_HIGH),
        _issue(SEVERITY_MEDIUM),
    ]
    assign_issue_ids(issues, 4)

    assert [i["issue_id"] for i in issues] == [
        "LO-4-1",
        "HI-4-1",
        "LO-4-2",
        "BK-4-1",
        "HI-4-2",
        "MD-4-1",
    ]


def test_input_order_preserved_not_severity_sorted():
    """§3.3：ID 按**输入顺序**推进，排序只是展示行为。

    旧实现先按 severity 排序再编号，导致同一 issue 集合因展示顺序不同而
    拿到不同 ID。这里锁定新契约。
    """
    issues = [
        _issue(SEVERITY_LOW, description="第一个（Low）"),
        _issue(SEVERITY_BLOCKING, description="第二个（Blocking）"),
        _issue(SEVERITY_LOW, description="第三个（Low）"),
    ]
    assign_issue_ids(issues, 5)

    # 列表顺序不变，且序号按输入顺序而非 severity 顺序。
    assert [i["description"] for i in issues] == [
        "第一个（Low）",
        "第二个（Blocking）",
        "第三个（Low）",
    ]
    assert [i["issue_id"] for i in issues] == ["LO-5-1", "BK-5-1", "LO-5-2"]


def test_generated_ids_match_generate_issue_id_format():
    """ID 格式沿用既有 generate_issue_id()。"""
    issues = [_issue(sev) for sev in VALID_SEVERITIES]
    assign_issue_ids(issues, 6)

    for issue, severity in zip(issues, VALID_SEVERITIES, strict=True):
        assert issue["issue_id"] == generate_issue_id(severity, 6, 1)


def test_empty_issue_list_is_noop():
    """空列表不报错。"""
    assert assign_issue_ids([], 1) == []


# ─────────── 2. 拒绝分支（L-03：直接构造 dict，绕过 assign 路径） ───────────


def test_validate_rejects_illegal_severity():
    """非法 severity 必须拒绝，且**不得**静默规范化。"""
    issues = [_issue("critical")]  # type: ignore[arg-type]

    with pytest.raises(IssueIdError, match="severity"):
        validate_issues(issues)

    # 拒绝时不得留下任何修复痕迹。
    assert issues[0]["severity"] == "critical"


def test_validate_rejects_duplicate_non_empty_id():
    """两个相同的**非空** issue_id 必须拒绝。"""
    issues = [_issue(issue_id="BK-1-1"), _issue(issue_id="BK-1-1")]

    with pytest.raises(IssueIdError, match="重复"):
        validate_issues(issues)


def test_duplicate_empty_ids_are_allowed():
    """多个空 ID 不算重复——它们是「都未分配」，不是「重复」。"""
    issues = [_issue(issue_id=""), _issue(issue_id="")]

    validate_issues(issues)  # 不抛

    assign_issue_ids(issues, 1)
    assert [i["issue_id"] for i in issues] == ["MD-1-1", "MD-1-2"]


@pytest.mark.parametrize("field", ["description", "issue_type"])
@pytest.mark.parametrize("bad_value", ["", "   "])
def test_validate_rejects_empty_required_text(field: str, bad_value: str):
    """空/纯空白的 description、issue_type 必须拒绝。"""
    issues = [_issue()]
    issues[0][field] = bad_value  # type: ignore[literal-required]

    with pytest.raises(IssueIdError, match=field):
        validate_issues(issues)


def test_validate_rejects_missing_required_fields():
    """必要字段缺失（非空串）同样拒绝。"""
    issue = _issue()
    del issue["description"]  # type: ignore[misc]

    with pytest.raises(IssueIdError, match="description"):
        validate_issues([issue])


def test_assign_issue_ids_is_all_or_nothing():
    """校验失败时不得留下半分配状态。"""
    issues = [
        _issue(SEVERITY_HIGH),  # 本可正常分配
        _issue("critical"),  # type: ignore[arg-type]  # 第二个非法
    ]

    with pytest.raises(IssueIdError):
        assign_issue_ids(issues, 1)

    # 第一个 issue 必须仍是未分配状态——先整体校验再赋值。
    assert "issue_id" not in issues[0]
    assert "issue_id" not in issues[1]


def test_issue_id_error_is_value_error():
    """继承 ValueError，调用方只需捕获一类。"""
    assert issubclass(IssueIdError, ValueError)


# ─────────────── 3. Markdown 编译器无业务副作用（§3.3 步骤 6） ───────────────


def test_compile_markdown_report_has_no_side_effects():
    """编译器只渲染：不分配 ID、不改 issue。

    用真实 parser 前置形状（``issue_id=""``）而非缺失键——规格 §3.3 把 ID
    分配钉在编译器**之前**，所以编译器按契约可以假定键已存在。
    """
    agent = _agent()
    issues = [_issue(SEVERITY_HIGH, issue_id="", description="高危问题")]

    report = agent._compile_markdown_report(issues, 1)  # type: ignore[arg-type]

    # 输入未被修改：ID 仍是空串，没有被凭空补齐。
    assert issues[0]["issue_id"] == ""
    assert issues[0]["description"] == "高危问题"
    # 报告里不应出现被凭空分配的 ID。
    assert "HI-1-1" not in report
    assert "高危问题" in report


def test_compile_markdown_report_does_not_mutate_assigned_issues():
    """已分配 ID 的 issue：报告渲染不得再改动任何字段。"""
    agent = _agent()
    issues: list[Any] = [_issue(SEVERITY_HIGH)]
    assign_issue_ids(issues, 1)
    before: list[Any] = [dict(i) for i in issues]

    agent._compile_markdown_report(issues, 1)

    assert issues == before


def test_compile_markdown_report_sorts_for_display_only():
    """排序只影响展示顺序，不影响已分配的 ID。"""
    agent = _agent()
    issues: list[Any] = [
        _issue(SEVERITY_LOW, description="低危"),
        _issue(SEVERITY_BLOCKING, description="阻断"),
    ]
    assign_issue_ids(issues, 1)

    report = agent._compile_markdown_report(issues, 1)

    # 分配按输入顺序：LO 先、BK 后。
    assert [i["issue_id"] for i in issues] == ["LO-1-1", "BK-1-1"]
    # 展示按 severity：BK 排在 LO 之前。
    assert report.index("BK-1-1") < report.index("LO-1-1")
