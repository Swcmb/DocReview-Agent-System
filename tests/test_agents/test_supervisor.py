"""SupervisorAgent 特征化测试 / Characterization Tests for SupervisorAgent

与 `test_docreview.py` 同属 T-00a：在未改动实现上锁定可观察行为。

锁定重点（后续任务会触碰这些路径）：

- `generate_spec()` 的**三场景路由**（§6.1）：仅有 task / 仅有文档 / 两者共存。
- `spec_version` 初始化语义：仅当为 0 时置 1，**已有版本不被覆盖**。
- `spec_snapshot` 与 `specification` 同步写入（手动修订检测的基线）。
- `revise_spec()` 的**无报告跳过**语义与**版本递增**语义。
- 两条路径的失败契约：统一 `DOCREVIEW_ERR_SYS_001`，且不抛出。
"""

from typing import Any, cast

import pytest

from src.agents.supervisor import SupervisorAgent
from src.schemas.models import AgentState, IssueStatus, ReviewReport
from src.state.agent_state import create_initial_state

# ─────────────────────────── 测试替身 ───────────────────────────


class _FakeLLM:
    """返回固定文本的 LLM 替身，并记录收到的 prompt。

    `SupervisorAgent` 全部 LLM 调用都走 `llm.agenerate([HumanMessage(...)])`
    并读取 `.generations[0][0].text`（`supervisor.py:273/409`），故只需实现这一个方法。
    """

    def __init__(self, text: str = "生成结果", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.prompts: list[str] = []

    async def agenerate(self, messages):
        self.prompts.append(messages[0].content)
        if self.fail:
            raise RuntimeError("模拟 LLM 故障")
        gen = type("_Gen", (), {"text": self.text})()
        return type("_Resp", (), {"generations": [[gen]]})()


def _make_agent(text: str = "生成结果", fail: bool = False) -> SupervisorAgent:
    return SupervisorAgent(llm=cast("Any", _FakeLLM(text, fail)))


def _state(**overrides: Any) -> AgentState:
    """以 `create_initial_state()` 为基底构造状态，避免重复维护 25 个键。"""
    return cast("AgentState", {**create_initial_state(), **overrides})


def _mk_report(issues: list[IssueStatus] | None = None) -> ReviewReport:
    return ReviewReport(
        iteration=1,
        timestamp="2026-09-26T00:00:00",
        review_conclusion="Fail",
        review_summary="本轮发现 1 个问题",
        issues=issues if issues is not None else [],
        highlights=[],
        open_questions=[],
        next_steps="修订后重审",
    )


def _mk_issue(severity: str = "Blocking") -> IssueStatus:
    return IssueStatus(
        issue_id="BK-1-1",
        severity=severity,
        issue_type="ConsistencyCheck",
        description="核心流程缺少出口点",
        suggestion="补充出口点定义",
        location="第 3 节",
        status="open",
    )


# ─────────────────── generate_spec：三场景路由（§6.1） ───────────────────


async def test_generate_spec_scenario1_task_only_routes_to_generate_from_task(monkeypatch):
    """特征化：场景①（仅 user_task）→ 调用 `generate_spec_from_task`。"""
    agent = _make_agent("从零生成的规格")
    called: list[str] = []

    async def _fake_from_task(task: str) -> str:
        called.append(task)
        return "从零生成的规格"

    monkeypatch.setattr(agent, "generate_spec_from_task", _fake_from_task)
    monkeypatch.setattr(
        agent, "convert_to_spec", _boom_async("场景①不应调用 convert_to_spec")
    )
    monkeypatch.setattr(
        agent, "_convert_with_context", _boom_async("场景①不应调用 _convert_with_context")
    )

    state = await agent.generate_spec(_state(user_task="设计用户认证系统"))

    assert called == ["设计用户认证系统"]
    assert state["specification"] == "从零生成的规格"
    assert state["error_code"] is None


async def test_generate_spec_scenario2_document_only_routes_to_convert(monkeypatch):
    """特征化：场景②（仅 document_content）→ 调用 `convert_to_spec` 且 task_context 为空串。"""
    agent = _make_agent()
    seen: list[tuple[str, str]] = []

    async def _fake_convert(document: str, task_context: str = "") -> str:
        seen.append((document, task_context))
        return "转换后的规格"

    monkeypatch.setattr(agent, "convert_to_spec", _fake_convert)
    monkeypatch.setattr(
        agent, "generate_spec_from_task", _boom_async("场景②不应调用 generate_spec_from_task")
    )
    monkeypatch.setattr(
        agent, "_convert_with_context", _boom_async("场景②不应调用 _convert_with_context")
    )

    state = await agent.generate_spec(_state(document_content="# 原始需求文档"))

    assert seen == [("# 原始需求文档", "")], "场景②必须传空 task_context"
    assert state["specification"] == "转换后的规格"


async def test_generate_spec_scenario3_both_prefers_document_as_subject(monkeypatch):
    """特征化：场景③（文档+任务共存）→ 调用 `_convert_with_context`，**以文档为主体**。

    该路由优先级是规格 §6.1 的明确要求：文档为主体，task 仅作上下文补充。
    """
    agent = _make_agent()
    seen: list[tuple[str, str]] = []

    async def _fake_with_context(document: str, task_context: str) -> str:
        seen.append((document, task_context))
        return "组合规格"

    monkeypatch.setattr(agent, "_convert_with_context", _fake_with_context)
    monkeypatch.setattr(agent, "convert_to_spec", _boom_async("场景③不应调用 convert_to_spec"))
    monkeypatch.setattr(
        agent, "generate_spec_from_task", _boom_async("场景③不应调用 generate_spec_from_task")
    )

    state = await agent.generate_spec(
        _state(document_content="# 原始需求文档", user_task="补充上下文")
    )

    assert seen == [("# 原始需求文档", "补充上下文")]
    assert state["specification"] == "组合规格"


async def test_generate_spec_empty_input_falls_back_to_scenario1(monkeypatch):
    """特征化：**两者皆空时也走场景①**（`supervisor.py:211-216` 的 else 分支）。

    这意味着空输入不会报错，而是把空 task 交给 LLM 生成——需在 T-20 前确认
    是否需要在工作流层拦截。
    """
    agent = _make_agent()
    called: list[str] = []

    async def _fake_from_task(task: str) -> str:
        called.append(task)
        return "兜底规格"

    monkeypatch.setattr(agent, "generate_spec_from_task", _fake_from_task)
    state = await agent.generate_spec(_state())

    assert called == [""], "空输入落到场景①，task 为空串"
    assert state["specification"] == "兜底规格"


# ─────────────────── spec_version / spec_snapshot 语义 ───────────────────


async def test_generate_spec_initializes_version_only_when_zero():
    """特征化：`spec_version` 仅在 0 时初始化为 1（`supervisor.py:222-223`）。"""
    agent = _make_agent("规格正文")

    fresh = await agent.generate_spec(_state(spec_version=0))
    assert fresh["spec_version"] == 1, "首次生成置 1"

    resumed = await agent.generate_spec(_state(spec_version=7))
    assert resumed["spec_version"] == 7, "已有版本不得被覆盖"


async def test_generate_spec_writes_snapshot_equal_to_specification():
    """特征化：`spec_snapshot` 与 `specification` 同值写入（手动修订检测基线，`supervisor.py:226`）。"""
    agent = _make_agent("规格正文")
    state = await agent.generate_spec(_state())

    assert state["spec_snapshot"] == state["specification"] == "规格正文"
    assert state["spec_snapshot"], "快照不得为空，否则 `check_manual_revision` 会误判"


async def test_generate_spec_strips_llm_whitespace():
    """特征化：LLM 返回值经 `.strip()` 后落库（`supervisor.py:274`）。"""
    agent = _make_agent("  \n  规格正文  \n ")
    state = await agent.generate_spec(_state())
    assert state["specification"] == "规格正文"


async def test_generate_spec_failure_sets_error_code_without_raising():
    """特征化：LLM 失败被吞掉，写 `DOCREVIEW_ERR_SYS_001` 并正常返回 state（`supervisor.py:231-235`）。"""
    agent = _make_agent(fail=True)
    state = await agent.generate_spec(_state(user_task="任意任务"))

    assert state["error_code"] == "DOCREVIEW_ERR_SYS_001"
    assert "模拟 LLM 故障" in (state["error_message"] or "")
    assert state["specification"] == "", "失败时不写入半成品规格"


# ─────────────────── revise_spec ───────────────────


async def test_revise_spec_skips_when_no_reports():
    """特征化：无审查报告时**直接返回、不调用 LLM、不递增版本**（`supervisor.py:381-383`）。"""
    agent = _make_agent("不应被调用")
    state = _state(specification="原规格", spec_version=3, review_reports=[])

    out = await agent.revise_spec(state)

    assert out["specification"] == "原规格", "无报告时规格保持不变"
    assert out["spec_version"] == 3, "无报告时不得递增版本"
    assert agent.llm.prompts == [], "无报告时不得调用 LLM"
    assert out["error_code"] is None


async def test_revise_spec_increments_version_and_refreshes_snapshot():
    """特征化：成功修订 → `spec_version + 1`，快照同步刷新（`supervisor.py:413-415`）。"""
    agent = _make_agent("修订后的规格")
    state = _state(
        specification="原规格",
        spec_version=2,
        review_reports=[_mk_report([_mk_issue()])],
    )

    out = await agent.revise_spec(state)

    assert out["specification"] == "修订后的规格"
    assert out["spec_version"] == 3
    assert out["spec_snapshot"] == "修订后的规格", "快照必须与新规格一致"
    assert out["error_code"] is None


async def test_revise_spec_uses_latest_report_only():
    """特征化：只取 `review_reports[-1]`（最新一轮），历史报告不进 prompt（`supervisor.py:386`）。"""
    agent = _make_agent("修订后的规格")
    old = _mk_report([_mk_issue()])
    old["review_summary"] = "旧轮次摘要"
    new = _mk_report([_mk_issue()])
    new["review_summary"] = "新轮次摘要"

    await agent.revise_spec(_state(specification="原规格", review_reports=[old, new]))

    prompt = agent.llm.prompts[0]
    assert "新轮次摘要" in prompt
    assert "旧轮次摘要" not in prompt, "历史轮次不得进入修订 prompt"


async def test_revise_spec_prompt_includes_full_issue_detail():
    """特征化：prompt 含每条 issue 的 severity/type/描述/建议/位置五个字段。"""
    agent = _make_agent("修订后的规格")
    await agent.revise_spec(
        _state(specification="原规格", review_reports=[_mk_report([_mk_issue()])])
    )

    prompt = agent.llm.prompts[0]
    assert "原规格" in prompt
    assert "Blocking" in prompt
    assert "ConsistencyCheck" in prompt
    assert "核心流程缺少出口点" in prompt
    assert "补充出口点定义" in prompt
    assert "第 3 节" in prompt


async def test_revise_spec_failure_sets_error_code_and_keeps_spec():
    """特征化：修订失败 → 写 `DOCREVIEW_ERR_SYS_001`，规格与版本保持原值。"""
    agent = _make_agent(fail=True)
    state = _state(
        specification="原规格",
        spec_version=4,
        review_reports=[_mk_report([_mk_issue()])],
    )

    out = await agent.revise_spec(state)

    assert out["error_code"] == "DOCREVIEW_ERR_SYS_001"
    assert "模拟 LLM 故障" in (out["error_message"] or "")
    assert out["specification"] == "原规格", "失败时不得写入半成品"
    assert out["spec_version"] == 4, "失败时不得递增版本"


async def test_revise_spec_with_empty_issues_list_still_prompts():
    """特征化：报告存在但 `issues` 为空时**仍会调用 LLM**（`supervisor.py:394` 的 for 循环退化为空）。

    这是潜在的无意义调用点，需在 T-15 前确认是否应加守卫。
    """
    agent = _make_agent("修订后的规格")
    state = _state(specification="原规格", review_reports=[_mk_report([])])

    out = await agent.revise_spec(state)

    assert len(agent.llm.prompts) == 1
    assert out["spec_version"] == 1


# ─────────────────── 构造参数默认值（§6.1 config 契约） ───────────────────


def test_supervisor_defaults():
    """特征化：三个 config 项的默认值（`supervisor.py:156-158`）。"""
    agent = SupervisorAgent(llm=None)
    assert agent.max_revision_iterations == 3
    assert agent.auto_approve_threshold == "low"
    assert agent.execution_gate_enabled is True
    assert agent.tools == []


def test_supervisor_config_overrides():
    """特征化：显式 config 覆盖默认值。"""
    agent = SupervisorAgent(
        llm=None,
        config={
            "max_revision_iterations": 9,
            "auto_approve_threshold": "high",
            "execution_gate_enabled": False,
        },
    )
    assert agent.max_revision_iterations == 9
    assert agent.auto_approve_threshold == "high"
    assert agent.execution_gate_enabled is False


# ─────────────────── 辅助 ───────────────────


def _boom_async(name: str):
    """构造一个一旦被调用就失败的协程，用于断言路由未被走到。"""

    async def _boom(*args, **kwargs):
        raise AssertionError(name)

    return _boom


@pytest.mark.parametrize("bad_input", [None, 123, []])
async def test_generate_spec_tolerates_non_string_inputs(bad_input):
    """特征化：非字符串 `document_content` 不会崩溃——错误延后到 LLM 调用或 f-string 拼接。

    工作流层目前不做类型校验（LangGraph 只保证键存在），此测试固定该宽松行为。
    """
    agent = _make_agent("规格正文")
    state = await agent.generate_spec(_state(document_content=cast("Any", bad_input)))
    assert state["error_code"] is None
    assert state["specification"] == "规格正文"
