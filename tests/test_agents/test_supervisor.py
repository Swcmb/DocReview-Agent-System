"""监督代理测试模块 / Supervisor Agent Test Module"""

import pytest

from src.agents.supervisor import SupervisorAgent
from src.state.agent_state import AgentState
from src.schemas.models import ReviewReport, DocumentInfo, ReviewStatus


@pytest.fixture
def supervisor() -> SupervisorAgent:
    """监督代理 fixture / Supervisor Agent Fixture"""
    return SupervisorAgent()


@pytest.fixture
def initial_state() -> AgentState:
    """初始状态 fixture / Initial State Fixture"""
    doc_info = DocumentInfo(
        document_id="test-001",
        document_type="prd",
        title="测试文档"
    )
    return AgentState(document_info=doc_info)


@pytest.mark.asyncio
async def test_supervisor_process_initial_state(
    supervisor: SupervisorAgent,
    initial_state: AgentState
) -> None:
    """测试监督代理处理初始状态 / Test Supervisor Process Initial State"""
    response = await supervisor.process(initial_state)

    assert response.message is not None
    assert response.should_continue is True
    assert initial_state.current_agent == "supervisor"


@pytest.mark.asyncio
async def test_supervisor_detects_completion(
    supervisor: SupervisorAgent,
    initial_state: AgentState
) -> None:
    """测试监督代理检测完成状态 / Test Supervisor Detects Completion"""
    initial_state.pending_issues = []

    response = await supervisor.process(initial_state)

    assert response.should_continue is False


@pytest.mark.asyncio
async def test_supervisor_max_iterations(
    supervisor: SupervisorAgent,
    initial_state: AgentState
) -> None:
    """测试监督代理最大迭代 / Test Supervisor Max Iterations"""
    supervisor.set_max_iterations(1)
    initial_state.current_iteration = 1

    response = await supervisor.process(initial_state)

    assert response.should_continue is False
    assert initial_state.status == ReviewStatus.FAILED


@pytest.mark.asyncio
async def test_supervisor_evaluate_approved(
    supervisor: SupervisorAgent,
    initial_state: AgentState
) -> None:
    """测试监督代理评估批准 / Test Supervisor Evaluate Approved"""
    response = await supervisor.evaluate_user_feedback(
        initial_state,
        approved=True,
        comment="审查通过"
    )

    assert response.should_continue is False
    assert initial_state.user_approved is True


@pytest.mark.asyncio
async def test_supervisor_evaluate_rejected(
    supervisor: SupervisorAgent,
    initial_state: AgentState
) -> None:
    """测试监督代理评估拒绝 / Test Supervisor Evaluate Rejected"""
    response = await supervisor.evaluate_user_feedback(
        initial_state,
        approved=False,
        comment="需要修改"
    )

    assert response.should_continue is True
    assert response.next_agent == "docreview"
