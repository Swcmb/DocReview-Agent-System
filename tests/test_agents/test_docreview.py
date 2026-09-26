"""DocReviewAgent 特征化测试 / Characterization Tests for DocReviewAgent

**这是 T-00a 的产物：先在未改动实现上锁定可观察行为**，作为后续 Laya 决策层
接入的「零回归」判据（F8／§0.5）。

三条纪律：

1. **不修改被测实现**。若某项行为无法稳定断言，记录为已知不确定项并申请裁决，
   不得为了让测试变绿而改实现。
2. **`_check_ac_coverage()` 的三行情形（规格 §6.6）是回归锁**：本次重构不得
   改变既有 Pass/Fail 契约。
3. **Step 0 已落地的行为也要锁**（失败轮次递增、issue 指纹归一化），
   防止后续任务无意中回退它们。
"""

from typing import Any, cast

import pytest

from src.agents.docreview import (
    ISSUE_TYPES,
    SEVERITY_BLOCKING,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    AtomicRequirement,
    CoreLoopAnalysis,
    DocReviewAgent,
)
from src.mcp.sequential_thinking import SequentialThinkingClient
from src.schemas.models import (
    AgentState,
    IssueStatus,
    ReviewReport,
    generate_issue_id,
)

# ─────────────────────────── 测试替身 ───────────────────────────


class _FakeAgent(DocReviewAgent):
    """把 `_think_step` 换成固定输出的 DocReviewAgent。

    六步全部经由 `_think_step` 取得 LLM 输出（`docreview.py:370/403/434/471/502/537`），
    因此替换它即可在不触碰 LLM 的前提下确定性地驱动整条审查流程。
    """

    def __init__(self, responses: dict[str, str] | None = None, default: str = "") -> None:
        super().__init__(llm=None, sequential_thinking=None, context7=None, tools=[])
        self.responses = responses or {}
        self.default = default
        self.calls: list[str] = []

    async def _think_step(self, step_name, context, num_thoughts=2, tracker=None):  # type: ignore[override]
        # T-17：签名多了 `tracker`（§11.3 局部成本追踪器）。double 必须跟着生产
        # 签名走，否则 review() 传 `tracker=` 时直接 TypeError。
        self.calls.append(step_name)
        return self.responses.get(step_name, self.default)


def _issue_line(
    issue_type: str = "ConsistencyCheck",
    severity: str = "Medium",
    description: str = "术语不一致",
    location: str = "第 3 节",
    suggestion: str = "统一术语",
) -> str:
    """构造一行合法的 `[ISSUE]` 结构化文本。"""
    return (
        f"[ISSUE] type={issue_type} severity={severity} "
        f"description={description} location={location} suggestion={suggestion}"
    )


def _state(**overrides: Any) -> AgentState:
    """构造一个可供 `review()` 直接消费的最小 AgentState。"""
    state: AgentState = {
        "user_task": "评审这份规格",
        "document_path": None,
        "document_content": "",
        "specification": "",
        "spec_version": 1,
        "review_reports": [],
        "review_conclusion_data": None,
        "iteration_count": 0,
        "review_conclusion": "pending",
        "max_iterations": 10,
        "stagnation_count": 0,
        "stagnation_threshold": 3,
        "user_approved": False,
        "awaiting_approval": False,
        "approval_timed_out": False,
        "execution_status": "pending",
        "execution_output": "",
        "mcp_degraded": False,
        "issue_tracker": {
            "all_issues": [],
            "fixed_count": 0,
            "partially_fixed_count": 0,
            "unfixed_count": 0,
            "new_in_current_round": [],
        },
        "spec_snapshot": "",
        "error_code": None,
        "error_message": None,
        "total_llm_cost": 0.0,
        "messages": [],
        # T-16：AgentState 新增 thread_id（入口生成一次并写回）。
        "thread_id": "",
    }
    state.update(cast("Any", overrides))
    return state


def _mk_report(iteration: int, issues: list[IssueStatus] | None = None) -> ReviewReport:
    """构造一个字段完整的 ReviewReport（TypedDict 必填键缺一不可）。"""
    return ReviewReport(
        iteration=iteration,
        timestamp="2026-09-26T00:00:00",
        review_conclusion="Pass",
        review_summary="上一轮摘要",
        issues=issues if issues is not None else [],
        highlights=[],
        open_questions=[],
        next_steps="",
    )


# ─────────────────── 六步流程：调用顺序与返回结构 ───────────────────

SIX_STEPS = [
    "core_loop_extraction",
    "consistency_check",
    "requirement_atomization",
    "feasibility_deduction",
    "risk_detection",
    "executability_review",
]


async def test_review_invokes_six_steps_in_order():
    """特征化：六步严格按固定顺序各调用一次 `_think_step`。"""
    agent = _FakeAgent(default="[FLOW] 用户提交 -> 系统处理")
    await agent.review(_state())

    assert agent.calls == SIX_STEPS


async def test_review_returns_documented_state_shape():
    """特征化：`review()` 返回结构与文档一致（规格 §0.5）。"""
    agent = _FakeAgent(
        responses={
            "core_loop_extraction": "[FLOW] 用户提交 -> 系统处理 -> 完成",
            "consistency_check": _issue_line(
                issue_type=ISSUE_TYPES["CONSISTENCY"],
                severity=SEVERITY_BLOCKING,
                description="核心流程缺少出口点",
            ),
        }
    )
    state = _state()
    out = await agent.review(state)

    assert out is state, "review() 就地修改并返回同一 state 对象"
    assert len(out["review_reports"]) == 1
    report = out["review_reports"][0]

    # ReviewReport 必填键（models.py:62-75）
    for key in (
        "iteration",
        "timestamp",
        "review_conclusion",
        "review_summary",
        "issues",
        "highlights",
        "open_questions",
        "next_steps",
    ):
        assert key in report, f"ReviewReport 缺少必需键 {key}"

    assert report["iteration"] == 1
    assert isinstance(report["issues"], list)
    assert isinstance(report["open_questions"], list)

    # review_conclusion_data 为 structured conclusion 的 by_alias=False dump
    data = out["review_conclusion_data"]
    assert data is not None
    for key in (
        "review_conclusion",
        "blocking_count",
        "high_count",
        "medium_count",
        "low_count",
        "ac_coverage_complete",
    ):
        assert key in data, f"review_conclusion_data 缺少键 {key}"

    assert out["iteration_count"] == 1
    assert out["review_conclusion"] == data["review_conclusion"]
    assert out["error_code"] is None


async def test_review_appends_report_after_conclusion_compiled():
    """特征化：`review_reports.append` 发生在结论编译之后（`docreview.py:220-235`）。

    这决定了「上一轮」在 `review()` 内是 `[-1]`、在 `evaluate_result` 内是 `[-2]`
    （规格 §20 证据、`review_workflow.py` 的 `[-2]` 语义）。
    """
    agent = _FakeAgent(default="[FLOW] x")
    state = _state(review_reports=[{"iteration": 0, "issues": []}])
    await agent.review(state)

    assert len(state["review_reports"]) == 2
    assert state["review_reports"][-1]["iteration"] == 1


async def test_review_issue_ids_assigned_by_markdown_compiler():
    """特征化：**当前实现由 `_compile_markdown_report` 分配 issue_id**（`docreview.py:580-588`）。

    规格 §3.3／T-14 要求把 ID 分配抽到独立的 `assign_issue_ids()`，使 Markdown
    编译器无副作用。本测试锁住重构前的行为，作为该重构的起点。
    """
    agent = _FakeAgent(
        responses={
            "consistency_check": "\n".join(
                [
                    _issue_line(severity=SEVERITY_BLOCKING, description="A"),
                    _issue_line(severity=SEVERITY_BLOCKING, description="B"),
                    _issue_line(severity=SEVERITY_MEDIUM, description="C"),
                ]
            )
        }
    )
    state = _state()
    await agent.review(state)

    ids = [i["issue_id"] for i in state["review_reports"][0]["issues"]]
    assert ids == ["BK-1-1", "BK-1-2", "MD-1-1"], "按级别独立计数，格式 {short}-{round}-{seq}"
    assert ids == [generate_issue_id("Blocking", 1, 1), generate_issue_id("Blocking", 1, 2),
                   generate_issue_id("Medium", 1, 1)]


# ─────────────── §6.6 `_check_ac_coverage()` 三行真值表（回归锁） ───────────────


def test_ac_coverage_p0_present_and_uncovered_is_false():
    """真值表第 1 行：有 P0 标记但无对应 AC → False（触发 Fail）。"""
    agent = _FakeAgent()
    spec = "## 需求\n- **FR-1**(P0) 用户登录\n- **FR-2**(P0) 权限控制\n"
    assert agent._check_ac_coverage(spec, []) is False


def test_ac_coverage_p0_present_and_covered_is_true():
    """真值表第 1 行的正向分支：P0 全部被 AC 覆盖 → True。"""
    agent = _FakeAgent()
    spec = (
        "## 需求\n- **FR-1**(P0) 用户登录\n- **FR-2**(P0) 权限控制\n"
        "## 验收\n[AC-1] covers=FR-1,FR-2 criteria=可测试\n"
    )
    assert agent._check_ac_coverage(spec, []) is True


def test_ac_coverage_no_p0_but_has_fr_is_true():
    """真值表第 2 行：无 P0 标记但存在任意 `FR-N` → True（默认已覆盖）。

    这是规格 F3 纠正的边界：根设计曾把第 2、3 行统称为「默认返回已覆盖」，
    实际只有本行如此。
    """
    agent = _FakeAgent()
    spec = "## 需求\n- **FR-1**: 用户登录\n- **FR-2**: 权限控制\n"
    assert agent._check_ac_coverage(spec, []) is True


def test_ac_coverage_no_p0_and_no_fr_is_false():
    """真值表第 3 行：连一个 `FR-N` 都没有 → False（经 `docreview.py:639` 触发 Fail）。

    这是**极易被误改**的边界：任何「顺手修正」都会改变 Pass/Fail 契约。
    """
    agent = _FakeAgent()
    spec = "## 概述\n本项目描述了一个系统。\n"
    assert agent._check_ac_coverage(spec, []) is False


def test_ac_coverage_six_p0_regex_variants_recognized():
    """特征化：6 条 P0 正则变体全部被识别（`docreview.py:672-679`）。"""
    agent = _FakeAgent()
    variants = [
        "- **FR-1**(P0) a",
        "- FR-2 (P0) b",
        "- FR-3: P0 c",
        "- FR-4 - P0 d",
        "- FR-5 [P0] e",
        "- **FR-6** 优先级: P0 f",
    ]
    for spec in variants:
        assert agent._check_ac_coverage(spec, []) is False, f"未识别 P0 变体: {spec}"


# ─────────────── `_compile_structured_conclusion()` 四个分支 ───────────────


def _mk_issue(severity: str) -> IssueStatus:
    return IssueStatus(
        issue_id="",
        severity=severity,
        issue_type=ISSUE_TYPES["CONSISTENCY"],
        description="d",
        suggestion="s",
        location="l",
        status="open",
    )


def test_conclusion_blocking_wins_over_everything():
    """特征化：Blocking ≥ 1 → Fail（最高优先级，`docreview.py:639`）。"""
    agent = _FakeAgent()
    issues = [_mk_issue(SEVERITY_BLOCKING), _mk_issue(SEVERITY_HIGH)]
    result = agent._compile_structured_conclusion(issues, "## x\n- **FR-1**(P0) y\n")
    assert result.review_conclusion == "Fail"
    assert result.blocking_count == 1
    assert result.high_count == 1


def test_conclusion_high_only_is_conditional_pass():
    """特征化：无 Blocking、有 High → Conditional Pass。"""
    agent = _FakeAgent()
    issues = [_mk_issue(SEVERITY_HIGH), _mk_issue(SEVERITY_MEDIUM)]
    result = agent._compile_structured_conclusion(issues, "## 需求\n- **FR-1**: 用户登录\n")
    assert result.review_conclusion == "Conditional Pass"
    assert result.high_count == 1


def test_conclusion_medium_low_only_is_pass():
    """特征化：仅 Medium/Low 或零问题 → Pass。"""
    agent = _FakeAgent()
    issues = [_mk_issue(SEVERITY_MEDIUM), _mk_issue(SEVERITY_LOW)]
    result = agent._compile_structured_conclusion(issues, "## 需求\n- **FR-1**: 用户登录\n")
    assert result.review_conclusion == "Pass"
    assert result.medium_count == 1
    assert result.low_count == 1


def test_conclusion_incomplete_ac_forces_fail():
    """特征化：AC 覆盖不完整 → Fail，即使零 Blocking（`docreview.py:639`）。"""
    agent = _FakeAgent()
    result = agent._compile_structured_conclusion([], "## 概述\n没有任何 FR。\n")
    assert result.ac_coverage_complete is False
    assert result.review_conclusion == "Fail"


# ─────────────── issue 解析：结构化 / 宽松 / 20 条上限 ───────────────


def test_parse_structured_issues_normalizes_severity():
    """特征化：非法 severity 被规范化为 Medium（`docreview.py:742`）。"""
    agent = _FakeAgent()
    text = _issue_line(severity="Critical")
    issues = agent._parse_structured_issues(text)
    assert len(issues) == 1
    assert issues[0]["severity"] == SEVERITY_MEDIUM
    assert issues[0]["issue_id"] == ""
    assert issues[0]["status"] == "open"


def test_parse_structured_issues_truncates_description_to_200():
    """特征化：description 截断到 200 字符（`docreview.py:744`）。"""
    agent = _FakeAgent()
    text = _issue_line(description="x" * 500)
    issues = agent._parse_structured_issues(text)
    assert len(issues[0]["description"]) == 200


def test_parse_issues_falls_back_to_loose_mode():
    """特征化：结构化解析为空时降级到宽松匹配（`docreview.py:722-724`）。"""
    agent = _FakeAgent()
    text = "问题: 需求 FR-1 缺少验收标准\n\n风险: 上线时间紧张\n"
    issues = agent._parse_issues_from_text(
        text, expected_types=[ISSUE_TYPES["CONSISTENCY"]], default_severity=SEVERITY_MEDIUM
    )
    assert len(issues) == 2
    assert all(i["issue_type"] == ISSUE_TYPES["CONSISTENCY"] for i in issues)
    assert all(i["location"] == "规格文档" for i in issues)


def test_parse_issues_caps_at_20():
    """特征化：单次解析上限 20 条，超出截断（`docreview.py:727-731`）。"""
    agent = _FakeAgent()
    text = "\n".join(_issue_line(description=f"d{i}") for i in range(25))
    issues = agent._parse_issues_from_text(
        text, expected_types=[ISSUE_TYPES["CONSISTENCY"]], default_severity=SEVERITY_MEDIUM
    )
    assert len(issues) == 20


def test_parse_issues_never_raises_on_empty_text():
    """特征化：空输入返回空列表，不抛异常。"""
    agent = _FakeAgent()
    assert agent._parse_issues_from_text("", [], SEVERITY_MEDIUM) == []


# ─────────────── Markdown 编译器行为 ───────────────


def test_markdown_report_sorts_by_severity_descending_priority():
    """按 Blocking→High→Medium→Low 排序渲染（仅展示顺序）。

    T-14 起排序不再参与 ID 编号：ID 已由 `assign_issue_ids()` 在上游按**输入
    顺序**分配完毕，故此处不再断言 `BK-3-1` 之类由编译器生成的 ID。
    """
    agent = _FakeAgent()
    issues: list[IssueStatus] = [
        {**_mk_issue(SEVERITY_LOW), "description": "low-1"},
        {**_mk_issue(SEVERITY_BLOCKING), "description": "block-1"},
        {**_mk_issue(SEVERITY_MEDIUM), "description": "med-1"},
    ]
    md = agent._compile_markdown_report(issues, 3)
    assert md.index("block-1") < md.index("med-1") < md.index("low-1")


def test_markdown_report_preserves_existing_issue_ids():
    """已有非空 issue_id 的条目不被覆盖。"""
    agent = _FakeAgent()
    issues: list[IssueStatus] = [{**_mk_issue(SEVERITY_MEDIUM), "issue_id": "MD-9-9"}]
    agent._compile_markdown_report(issues, 1)
    assert issues[0]["issue_id"] == "MD-9-9"


def test_markdown_report_does_not_mutate_input():
    """T-14 已落地：Markdown 编译器**无业务副作用**（§3.3 item 6）。

    本测试原为 `test_markdown_report_mutates_input_assigning_ids`，锁定重构前
    「就地写回 issue_id」的行为，其 docstring 明确要求重构完成后改为断言
    `issues` 不被修改。ID 分配已迁移到独立的 `assign_issue_ids()`。
    """
    agent = _FakeAgent()
    issues: list[IssueStatus] = [{**_mk_issue(SEVERITY_BLOCKING), "issue_id": ""}]
    agent._compile_markdown_report(issues, 2)
    assert issues[0]["issue_id"] == "", "编译器不得分配或写回 issue_id"


# ─────────────── 辅助解析器 ───────────────


def test_parse_atomic_requirements_extracts_fr_and_priority():
    """特征化：`[FR-N] ... priority=P0|P1|P2` 解析（`docreview.py:789-795`）。

    ID 保留 `FR-` 前缀（`id=f"FR-{m[0]}"`），缺省优先级为 P1。
    """
    agent = _FakeAgent()
    text = "[FR-1] 用户登录 priority=P0\n[FR-2] 权限控制 priority=P1\n[FR-3] 数据导出\n"
    reqs = agent._parse_atomic_requirements(text)
    assert [r.id for r in reqs] == ["FR-1", "FR-2", "FR-3"]
    assert [r.priority for r in reqs] == ["P0", "P1", "P1"], "缺省优先级为 P1"
    assert reqs[0].description == "用户登录"
    assert all(isinstance(r, AtomicRequirement) for r in reqs)


def test_parse_atomic_requirements_loose_fallback_defaults_p1():
    """特征化：结构化格式无命中时降级到 `**FR-N**` 宽松匹配，优先级恒为 P1（`docreview.py:797-804`）。"""
    agent = _FakeAgent()
    text = "**FR-1**: 用户登录\n**FR-2**：权限控制\n"
    reqs = agent._parse_atomic_requirements(text)
    assert [r.id for r in reqs] == ["FR-1", "FR-2"]
    assert {r.priority for r in reqs} == {"P1"}


def test_parse_dependency_graph_extracts_nodes_and_edges():
    """特征化：`[DEP] src depends_on dst` → {"nodes": [...], "edges": [...]}（`docreview.py:819-823`）。"""
    agent = _FakeAgent()
    text = "[DEP] 前端 depends_on 后端\n[DEP] 后端 depends_on 数据库\n"
    graph = agent._parse_dependency_graph(text)

    assert set(graph["nodes"]) == {"前端", "后端", "数据库"}
    assert graph["edges"] == [
        {"from": "前端", "to": "后端"},
        {"from": "后端", "to": "数据库"},
    ], "边的顺序遵循文本出现顺序"


def test_extract_tech_stack_detects_language():
    """特征化：技术栈抽取命中 `技术栈：` 前缀（`docreview.py:343-353`）。"""
    agent = _FakeAgent()
    assert agent._extract_tech_stack("技术栈：Python + LangGraph") == "Python + LangGraph"
    assert agent._extract_tech_stack("使用 React 构建前端") == "React"
    assert agent._extract_tech_stack("毫无技术线索") == ""


def test_core_loop_analysis_defaults():
    """特征化：`CoreLoopAnalysis` 的默认空集合。"""
    analysis = CoreLoopAnalysis()
    assert analysis.flows == [] and analysis.breaks == []
    assert analysis.entry_points == [] and analysis.exit_points == []


# ─────────────── Step 0 基线：失败轮次递增 ───────────────


async def test_failure_path_still_increments_iteration_count():
    """Step 0 基线（规格 §2.2）：`review()` 抛异常时 `iteration_count` 仍前进。

    该改动使 `route_after_evaluate` 的轮次上限分支最终可达，避免失败轮次下
    工作流不终止。**不得回退。**
    """
    agent = _FakeAgent(default="x")

    async def _boom(*args, **kwargs):
        raise RuntimeError("模拟 LLM 故障")

    agent._think_step = _boom  # type: ignore[assignment]

    state = _state(iteration_count=2)
    out = await agent.review(state)

    assert out["iteration_count"] == 3, "失败轮次也必须推进一格"
    assert out["error_code"] == "DOCREVIEW_ERR_SYS_001"
    assert "模拟 LLM 故障" in (out["error_message"] or "")
    assert out["review_reports"] == [], "失败时不追加报告"


async def test_failure_path_does_not_overwrite_existing_error_code():
    """特征化：失败路径只写 `DOCREVIEW_ERR_SYS_001`，不清空既有 `review_reports`。"""
    agent = _FakeAgent(default="x")

    async def _boom(*args, **kwargs):
        raise ValueError("bad")

    agent._think_step = _boom  # type: ignore[assignment]
    state = _state(iteration_count=0, review_reports=[_mk_report(7)])
    out = await agent.review(state)

    assert out["error_code"] == "DOCREVIEW_ERR_SYS_001"
    assert len(out["review_reports"]) == 1, "失败不清空既有报告"


# ─────────────── MCP 降级路径 ───────────────


async def test_think_step_falls_back_when_no_sequential_thinking():
    """特征化：无 `sequential_thinking` 时 `_think_step` 直接降级到 `_llm_think`。

    这是缺陷 1（`create_workflow_runtime()` 从不调用 `start()`）造成的既有行为：
    MCP 推理路径实际不可用，恒走 LLM 兜底。
    """
    class _Resp:
        def __init__(self, text: str) -> None:
            self.generations = [[type("G", (), {"text": text})()]]

    class _LLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def agenerate(self, messages):
            self.prompts.append(messages[0].content)
            return _Resp("降级输出")

    llm = _LLM()
    agent = DocReviewAgent(llm=llm, sequential_thinking=None)
    out = await agent._think_step("step", "context")
    assert out == "降级输出"
    assert len(llm.prompts) == 1


@pytest.mark.parametrize("degraded_flag", [True, False])
async def test_think_step_falls_back_when_mcp_degraded(degraded_flag):
    """特征化：`sequential_thinking.is_degraded` 为真时降级（`docreview.py:274`）。"""
    class _Resp:
        def __init__(self, text: str) -> None:
            self.generations = [[type("G", (), {"text": text})()]]

    class _LLM:
        async def agenerate(self, messages):
            return _Resp("降级输出")

    class _Seq:
        is_degraded = degraded_flag

        async def think(self, **kwargs):
            raise AssertionError("不应调用 MCP think")

    agent = DocReviewAgent(
        llm=cast("Any", _LLM()),
        sequential_thinking=cast("SequentialThinkingClient", _Seq()),
    )
    assert await agent._think_step("step", "ctx") == "降级输出"


# ─────────────── T-17 §11.3：六步审查成本记账 ───────────────


class _ChargingFakeAgent(_FakeAgent):
    """每步向 tracker 记一笔账，模拟真实 `_llm_think` 的记账行为。

    `_FakeAgent` 直接短路 `_think_step`，因此不产生任何成本；这里补上记账，
    用来验证 `review()` 是否把六步成本汇总进 `state["total_llm_cost"]`。
    """

    def __init__(self, *args: Any, per_step: float = 0.01, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.per_step = per_step

    async def _think_step(self, step_name, context, num_thoughts=2, tracker=None):  # type: ignore[override]
        self.calls.append(step_name)
        if tracker is not None:
            tracker.total_cost += self.per_step
            tracker.request_count += 1
        return self.responses.get(step_name, self.default)


async def test_review_accumulates_six_step_llm_cost():
    """T-17：六步审查的成本必须汇总进 `state["total_llm_cost"]`。

    改动前 `review()` 建了 tracker 却从不读取，六步 LLM 支出对预算闸门完全隐形。
    """
    agent = _ChargingFakeAgent(default="无问题", per_step=0.01)

    out = await agent.review(_state(specification="# 规格\n内容"))

    assert len(agent.calls) == 6, "六步都应被调用"
    assert out["total_llm_cost"] == pytest.approx(0.06), "六步成本必须按 6×0.01 汇总"


async def test_review_cost_accumulates_across_rounds():
    """T-17：多轮审查成本必须**累加**而非覆盖——覆盖会让长会话成本凭空归零。"""
    agent = _ChargingFakeAgent(default="无问题", per_step=0.01)

    first = await agent.review(_state(specification="# 规格\n内容"))
    second = await agent.review(
        _state(specification="# 规格\n内容", total_llm_cost=first["total_llm_cost"])
    )

    assert second["total_llm_cost"] > first["total_llm_cost"]


async def test_review_accumulates_llm_cost_through_real_invoke_path():
    """T-17：真实 LLM 路径（`_llm_think` → `invoke_with_cost`）的成本同样进 state。"""

    class _Resp:
        def __init__(self, text: str) -> None:
            self.generations = [[type("G", (), {"text": text})()]]

    class _LLM:
        async def agenerate(self, messages):
            # 文本需足够长：代价 fallback 走 int(len/4*1.3*0.3)，短文本会截断成 0 token
            return _Resp("审查输出" * 200)

    agent = DocReviewAgent(llm=cast("Any", _LLM()), sequential_thinking=None)

    out = await agent.review(_state(specification="# 规格\n内容"))

    assert out["total_llm_cost"] > 0, "真实 LLM 路径的成本必须记账"
