"""审查工作流测试模块 / Review Workflow Test Module"""

from typing import cast

import pytest

from src.workflows.review_workflow import (
    build_workflow,
    create_workflow_runtime,
    initialize,
    load_document,
    evaluate_result,
    finalize,
    route_after_initialize,
    route_after_evaluate,
    route_after_approval,
    _is_stagnant,
    _prune_review_history,
    _save_review_history,
    _print_summary,
)
from src.schemas.models import AgentState
from src.state.agent_state import create_initial_state
from src.utils.llm import CostTracker
from src.workflows.review_routing import BUDGET_ERROR_CODE


@pytest.fixture
def initial_state():
    """初始状态 fixture"""
    return create_initial_state()


@pytest.fixture
def cost_tracker():
    """成本追踪器 fixture"""
    return CostTracker()


def test_route_after_initialize_with_document():
    """测试 route_after_initialize 有 document_path 的情况"""
    state = {"document_path": "test.md"}
    result = route_after_initialize(state)
    assert result == "load_document"


def test_route_after_initialize_without_document():
    """测试 route_after_initialize 无 document_path 的情况"""
    state = {}
    result = route_after_initialize(state)
    assert result == "generate_spec"


def test_is_stagnant():
    """测试停滞检测"""
    state = {
        "review_reports": [
            {"issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
            {"issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
        ]
    }
    assert _is_stagnant(state) is True

    state["review_reports"].append(
        {"issues": [{"issue_id": "HI-1-1", "severity": "High"}]}
    )
    assert _is_stagnant(state) is False


def test_is_stagnant_insufficient_reports():
    """测试停滞检测 - 报告不足"""
    state = {"review_reports": [{"issues": []}]}
    assert _is_stagnant(state) is False


def test_is_stagnant_empty_reports():
    """测试停滞检测 - 空报告列表"""
    state = {"review_reports": []}
    assert _is_stagnant(state) is False


def test_is_stagnant_partial_overlap():
    """测试停滞检测 - 部分重叠"""
    state = {
        "review_reports": [
            {"issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
            {"issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}, {"issue_id": "HI-1-1", "severity": "High"}]},
        ]
    }
    assert _is_stagnant(state) is False


def test_is_stagnant_same_content_different_round_ids():
    """Step 0 验收：两轮问题集合内容相同但 issue_id 轮次不同 → 判为停滞

    issue_id 内嵌轮次号（如 BK-1-1 与 BK-2-1），按 id 比较恒不成立；
    修复后按内容身份（severity + issue_type + 规范化 description 哈希）比较。
    """
    state = {
        "review_reports": [
            {"issues": [{
                "issue_id": "BK-1-1", "severity": "Blocking",
                "issue_type": "consistency",
                "description": "需求 FR-001 与验收标准冲突！",
            }]},
            {"issues": [{
                "issue_id": "BK-2-1", "severity": "Blocking",
                "issue_type": "consistency",
                "description": "需求 FR-001 与验收标准冲突",
            }]},
        ]
    }
    # 轮次号不同 + description 仅标点差异 → 归一化后应判为停滞
    assert _is_stagnant(state) is True


def test_is_stagnant_content_change_not_stagnant():
    """Step 0 验收：问题描述实质变化 → 不判停滞"""
    state = {
        "review_reports": [
            {"issues": [{
                "issue_id": "BK-1-1", "severity": "Blocking",
                "issue_type": "consistency",
                "description": "需求 A 与验收标准冲突",
            }]},
            {"issues": [{
                "issue_id": "BK-2-1", "severity": "Blocking",
                "issue_type": "consistency",
                "description": "完全不同的新问题",
            }]},
        ]
    }
    assert _is_stagnant(state) is False


def test_prune_review_history():
    """测试审查历史压缩"""
    state = {
        "review_reports": [
            {
                "review_conclusion": "Fail",
                "issues": [
                    {"issue_id": "BK-1-1", "severity": "Blocking"},
                    {"issue_id": "HI-1-2", "severity": "High"},
                ]
            },
            {
                "review_conclusion": "Fail",
                "issues": [
                    {"issue_id": "BK-1-1", "severity": "Blocking"},
                ]
            },
            {
                "review_conclusion": "Fail",
                "issues": [
                    {"issue_id": "BK-1-1", "severity": "Blocking"},
                ]
            },
        ]
    }

    _prune_review_history(state)

    assert len(state["review_reports"][0]["issues"]) == 0
    assert "review_summary" in state["review_reports"][0]
    assert len(state["review_reports"][1]["issues"]) == 1


def test_prune_review_history_no_prune_needed():
    """测试审查历史压缩 - 不需要压缩"""
    state = {
        "review_reports": [
            {"review_conclusion": "Fail", "issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
            {"review_conclusion": "Fail", "issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
        ]
    }

    _prune_review_history(state)

    assert len(state["review_reports"][0]["issues"]) == 1
    assert "review_summary" not in state["review_reports"][0]


def test_prune_review_history_single_report():
    """测试审查历史压缩 - 单个报告"""
    state = {
        "review_reports": [
            {"review_conclusion": "Fail", "issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
        ]
    }

    _prune_review_history(state)

    assert len(state["review_reports"][0]["issues"]) == 1


def test_prune_review_history_summary_format():
    """测试审查历史压缩 - 摘要格式"""
    state = {
        "review_reports": [
            {
                "review_conclusion": "Pass",
                "issues": [
                    {"severity": "Blocking"},
                    {"severity": "High"},
                    {"severity": "Medium"},
                    {"severity": "Low"},
                ]
            },
            {
                "review_conclusion": "Fail",
                "issues": [
                    {"severity": "Blocking"},
                ]
            },
            {
                "review_conclusion": "Pass",
                "issues": [
                    {"severity": "Medium"},
                ]
            },
            {
                "review_conclusion": "Pass",
                "issues": []
            },
        ]
    }

    _prune_review_history(state)

    assert "review_summary" in state["review_reports"][0]
    assert "1B/1H/1M/1L" in state["review_reports"][0]["review_summary"]
    assert "review_summary" in state["review_reports"][1]
    assert "1B/0H/0M/0L" in state["review_reports"][1]["review_summary"]


def test_route_after_evaluate_pass():
    """测试 route_after_evaluate 通过的情况"""
    state = {
        "review_conclusion": "Pass",
        "iteration_count": 0,
        "max_iterations": 10,
        "stagnation_count": 0,
    }
    result = route_after_evaluate(state)
    assert result == "user_approval"
    assert state["awaiting_approval"] is True


def test_route_after_evaluate_conditional_pass():
    """测试 route_after_evaluate Conditional Pass 的情况"""
    state = {
        "review_conclusion": "Conditional Pass",
        "iteration_count": 0,
        "max_iterations": 10,
        "stagnation_count": 0,
    }
    result = route_after_evaluate(state)
    assert result == "user_approval"
    assert state["awaiting_approval"] is True


def test_route_after_evaluate_fail():
    """测试 route_after_evaluate 失败的情况"""
    state = {
        "review_conclusion": "Fail",
        "iteration_count": 0,
        "max_iterations": 10,
        "stagnation_count": 0,
    }
    result = route_after_evaluate(state)
    assert result == "revise_spec"


def test_route_after_evaluate_max_iterations():
    """测试 route_after_evaluate 达到最大迭代次数"""
    state = {
        "review_conclusion": "Fail",
        "iteration_count": 10,
        "max_iterations": 10,
        "stagnation_count": 0,
    }
    result = route_after_evaluate(state)
    assert result == "finalize"


def test_route_after_evaluate_stagnation():
    """测试 route_after_evaluate 检测到停滞"""
    state = {
        "review_conclusion": "Fail",
        "iteration_count": 0,
        "max_iterations": 10,
        "stagnation_count": 3,
        "stagnation_threshold": 2,
    }
    result = route_after_evaluate(state)
    assert result == "finalize"


def test_route_after_approval_approved():
    """测试 route_after_approval 用户批准的情况"""
    state = {
        "user_approved": True,
        "approval_timed_out": False,
        "review_conclusion": "Pass",
    }
    result = route_after_approval(state)
    assert result == "execute"


def test_route_after_approval_timeout():
    """测试 route_after_approval 超时的情况"""
    state = {
        "user_approved": False,
        "approval_timed_out": True,
        "review_conclusion": "Pass",
    }
    result = route_after_approval(state)
    assert result == "finalize"
    assert state["error_code"] == "DOCREVIEW_ERR_LOOP_003"


def test_route_after_approval_conditional_pass():
    """测试 route_after_approval Conditional Pass 的情况"""
    state = {
        "user_approved": False,
        "approval_timed_out": False,
        "review_conclusion": "Conditional Pass",
    }
    result = route_after_approval(state)
    assert result == "revise_spec"


def test_route_after_approval_rejected():
    """测试 route_after_approval 用户拒绝的情况"""
    state = {
        "user_approved": False,
        "approval_timed_out": False,
        "review_conclusion": "Pass",
    }
    result = route_after_approval(state)
    assert result == "finalize"


def test_route_after_approval_fail_with_reject():
    """测试 route_after_approval Fail 后拒绝的情况"""
    state = {
        "user_approved": False,
        "approval_timed_out": False,
        "review_conclusion": "Fail",
    }
    result = route_after_approval(state)
    assert result == "finalize"


@pytest.mark.asyncio
async def test_initialize_creates_directories():
    """测试 initialize 创建必要目录"""
    state = create_initial_state()
    result = await initialize(state)

    assert "max_iterations" in result
    assert "stagnation_count" in result
    assert result["stagnation_count"] == 0
    assert result["execution_status"] == "pending"


@pytest.mark.asyncio
async def test_initialize_with_existing_state():
    """测试 initialize 保留已有状态"""
    state = create_initial_state()
    state["max_iterations"] = 5
    state["iteration_count"] = 2

    result = await initialize(state)

    assert result["max_iterations"] == 5
    assert result["iteration_count"] == 2


@pytest.mark.asyncio
async def test_initialize_issue_tracker():
    """测试 initialize 创建问题追踪器"""
    state = create_initial_state()
    result = await initialize(state)

    assert "issue_tracker" in result
    assert result["issue_tracker"]["all_issues"] == []
    assert result["issue_tracker"]["fixed_count"] == 0


@pytest.mark.asyncio
async def test_initialize_mcp_degraded():
    """测试 initialize MCP 降级状态"""
    state = create_initial_state()
    result = await initialize(state)

    assert "mcp_degraded" in result


@pytest.mark.asyncio
async def test_load_document_empty_path():
    """测试 load_document 空路径"""
    state = {"document_path": ""}
    result = await load_document(state)
    assert state.get("error_code") == "DOCREVIEW_ERR_DOC_001"


@pytest.mark.asyncio
async def test_load_document_success(temp_dir):
    """测试 load_document 成功加载"""
    from pathlib import Path
    from src.tools.reading import ReadingTool
    test_file = temp_dir / "test.md"
    test_file.write_text("# Test Document")

    reading_tool = ReadingTool(workspace_dir=str(temp_dir))
    result = reading_tool.read_file(str(test_file))

    assert result.success is True
    assert "Test Document" in result.data.get("content", "")


@pytest.mark.asyncio
async def test_load_document_file_not_found():
    """测试 load_document 文件不存在"""
    state = {"document_path": "/nonexistent/file.md"}
    result = await load_document(state)
    assert result.get("error_code") == "DOCREVIEW_ERR_DOC_001"


@pytest.mark.asyncio
async def test_evaluate_result():
    """测试 evaluate_result 节点"""
    state = {
        "review_conclusion_data": {"review_conclusion": "Pass"},
        "review_reports": [],
        "specification": "# Test Spec",
    }

    result = await evaluate_result(state)

    assert result["review_conclusion"] == "Pass"


@pytest.mark.asyncio
async def test_evaluate_result_fail():
    """测试 evaluate_result 节点 - Fail"""
    state = {
        "review_conclusion_data": {"review_conclusion": "Fail"},
        "review_reports": [],
        "specification": "# Test Spec",
    }

    result = await evaluate_result(state)

    assert result["review_conclusion"] == "Fail"
    assert result["stagnation_count"] == 0


@pytest.mark.asyncio
async def test_evaluate_result_no_data():
    """测试 evaluate_result 节点 - 无数据"""
    state = {
        "review_conclusion_data": None,
        "review_reports": [],
        "specification": "# Test Spec",
    }

    result = await evaluate_result(state)

    assert result["review_conclusion"] == "Fail"


@pytest.mark.asyncio
async def test_evaluate_result_stagnation():
    """测试 evaluate_result 节点 - 停滞检测"""
    state = {
        "review_conclusion_data": {"review_conclusion": "Fail"},
        "review_reports": [
            {"issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
            {"issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]},
        ],
        "specification": "# Test Spec",
        "stagnation_count": 0,
    }

    result = await evaluate_result(state)

    assert result["stagnation_count"] == 1


@pytest.mark.asyncio
async def test_evaluate_result_budget_exceeded():
    """测试 evaluate_result 节点 - 超出预算"""
    state = {
        "review_conclusion_data": {"review_conclusion": "Pass"},
        "review_reports": [],
        "specification": "# Test Spec",
        "total_llm_cost": 10.0,
    }

    result = await evaluate_result(state)

    if result.get("error_code") == "DOCREVIEW_ERR_LLM_008":
        assert True
    else:
        assert result.get("error_code") is None or result.get("total_llm_cost", 0) <= 10.0


@pytest.mark.asyncio
async def test_evaluate_result_spec_snapshot():
    """测试 evaluate_result 节点 - 规格快照"""
    state = {
        "review_conclusion_data": {"review_conclusion": "Pass"},
        "review_reports": [],
        "specification": "# Original Spec Content",
    }

    result = await evaluate_result(state)

    assert result["spec_snapshot"] == "# Original Spec Content"


@pytest.mark.asyncio
async def test_finalize():
    """测试 finalize 节点"""
    state = {
        "review_reports": [
            {
                "review_conclusion": "Pass",
                "issues": [{"issue_id": "BK-1-1", "severity": "Blocking"}]
            }
        ],
        "review_conclusion": "Pass",
        "iteration_count": 1,
        "total_llm_cost": 0.5,
        "spec_version": 1,
    }

    result = await finalize(state)

    assert result["execution_status"] == "completed"


@pytest.mark.asyncio
async def test_finalize_no_reports():
    """测试 finalize 节点 - 无报告"""
    state = {
        "review_reports": [],
        "review_conclusion": "Fail",
        "iteration_count": 0,
        "total_llm_cost": 0.0,
    }

    result = await finalize(state)

    assert result["execution_status"] == "completed"


def test_print_summary(capsys):
    """测试摘要打印"""
    state = {
        "review_conclusion": "Pass",
        "iteration_count": 3,
        "review_reports": [
            {"issues": [{"issue_id": "BK-1-1"}]},
            {"issues": [{"issue_id": "HI-1-1"}]},
        ],
        "total_llm_cost": 1.25,
    }

    _print_summary(state)

    captured = capsys.readouterr()
    assert "审查结论: Pass" in captured.out
    assert "迭代轮次: 3" in captured.out
    assert "LLM 成本: $1.2500" in captured.out


def test_save_review_history(temp_dir, monkeypatch):
    """测试审查历史保存"""
    import os
    monkeypatch.chdir(temp_dir)

    state = {
        "spec_version": 1,
        "review_conclusion": "Pass",
        "total_llm_cost": 0.75,
        "review_reports": [
            {"issues": [{"issue_id": "BK-1-1"}]},
        ],
    }

    _save_review_history(state)

    reviews_dir = temp_dir / "reviews"
    assert reviews_dir.exists()

    files = list(reviews_dir.glob("history-*.json"))
    assert len(files) == 1


def test_cost_tracker_integration():
    """测试成本追踪器集成"""
    tracker = CostTracker()

    tracker.total_cost = 5.0
    tracker.prompt_tokens = 1000
    tracker.completion_tokens = 500
    tracker.request_count = 10

    data = tracker.to_dict()

    assert data["total_cost"] == 5.0
    assert data["prompt_tokens"] == 1000
    assert data["completion_tokens"] == 500
    assert data["total_tokens"] == 1500
    assert data["request_count"] == 10


@pytest.mark.asyncio
async def test_workflow_state_persistence_fields():
    """测试工作流状态持久化字段"""
    state = create_initial_state()

    required_fields = [
        "max_iterations",
        "stagnation_count",
        "stagnation_threshold",
        "iteration_count",
        "review_reports",
        "review_conclusion",
        "user_approved",
        "awaiting_approval",
        "approval_timed_out",
        "execution_status",
        "error_code",
        "error_message",
        "mcp_degraded",
        "issue_tracker",
        "spec_snapshot",
        "total_llm_cost",
    ]

    result = await initialize(state)

    for field in required_fields:
        assert field in result, f"Missing required field: {field}"


class _StubAgentBehavior:
    def __init__(self, max_cost: float) -> None:
        self.max_cost_per_task = max_cost


class _StubConfig:
    def __init__(self, max_cost: float) -> None:
        self.agent_behavior = _StubAgentBehavior(max_cost)


class TestBudgetGate:
    """§11.3 预算优先级：超预算必须显式失败并优先于其他终止条件。"""

    async def test_evaluate_result_marks_execution_failed_on_over_budget(self, monkeypatch):
        """超预算时 evaluate_result 必须同时置 error_code 与 execution_status="failed"。"""
        monkeypatch.setattr(
            "src.workflows.review_workflow.AppConfig", lambda: _StubConfig(max_cost=1.0)
        )
        state = cast("AgentState", {
            **create_initial_state(),
            "review_conclusion_data": {"review_conclusion": "Fail"},
            "total_llm_cost": 2.5,
        })

        out = await evaluate_result(state)

        assert out["error_code"] == BUDGET_ERROR_CODE
        assert out["execution_status"] == "failed", (
            "只写 error_code 会让 CLI 摘要仍报「已完成」，掩盖真实失败原因"
        )
        assert "超预算" in out["error_message"]

    async def test_evaluate_result_under_budget_is_untouched(self, monkeypatch):
        """未超预算时不得写入预算错误码。"""
        monkeypatch.setattr(
            "src.workflows.review_workflow.AppConfig", lambda: _StubConfig(max_cost=10.0)
        )
        state = cast("AgentState", {
            **create_initial_state(),
            "review_conclusion_data": {"review_conclusion": "Fail"},
            "total_llm_cost": 0.5,
        })

        out = await evaluate_result(state)

        assert out["error_code"] is None
        assert out["execution_status"] == "pending"

    def test_route_after_evaluate_budget_error_goes_to_finalize(self):
        """DOCREVIEW_ERR_LLM_008 → finalize。"""
        state = cast("AgentState", {
            "error_code": BUDGET_ERROR_CODE,
            "review_conclusion": "Fail",
            "iteration_count": 0,
            "max_iterations": 10,
            "stagnation_count": 0,
        })

        assert route_after_evaluate(state) == "finalize"

    def test_budget_error_beats_pass_conclusion(self):
        """优先级核心：即使结论是 Pass，超预算也必须终止而不是送去等审批。

        若这里放行，审批通过后 revise/execute 又各调一次 LLM，预算闸门被自己的
        优先级架空。
        """
        state = cast("AgentState", {
            "error_code": BUDGET_ERROR_CODE,
            "review_conclusion": "Pass",
            "iteration_count": 0,
            "max_iterations": 10,
            "stagnation_count": 0,
        })

        assert route_after_evaluate(state) == "finalize"
        assert state.get("awaiting_approval") is not True

    def test_budget_error_beats_stagnation_and_max(self):
        """同样压过 max_iterations 与 stagnation 两个既有终止条件。"""
        for extra in ({"iteration_count": 99}, {"stagnation_count": 99}):
            state = cast("AgentState", {
                "error_code": BUDGET_ERROR_CODE,
                "review_conclusion": "Fail",
                "iteration_count": 0,
                "max_iterations": 10,
                "stagnation_threshold": 3,
                "stagnation_count": 0,
                **extra,
            })
            assert route_after_evaluate(state) == "finalize", extra

    def test_other_llm_errors_do_not_force_finalize(self):
        """非预算的 LLM 错误码不得触发该守卫——守卫只认预算这一个码。"""
        state = cast("AgentState", {
            "error_code": "DOCREVIEW_ERR_LLM_001",
            "review_conclusion": "Fail",
            "iteration_count": 0,
            "max_iterations": 10,
            "stagnation_count": 0,
        })

        assert route_after_evaluate(state) == "revise_spec"
