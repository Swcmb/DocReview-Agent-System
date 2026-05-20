"""文档审查代理测试模块 / Document Review Agent Test Module"""

import pytest

from src.agents.docreview import DocumentReviewAgent
from src.state.agent_state import AgentState
from src.schemas.models import DocumentInfo


@pytest.fixture
def docreview_agent() -> DocumentReviewAgent:
    """文档审查代理 fixture / Document Review Agent Fixture"""
    return DocumentReviewAgent()


@pytest.fixture
def state_with_document() -> AgentState:
    """带文档的状态 fixture / State with Document Fixture"""
    doc_info = DocumentInfo(
        document_id="test-002",
        document_type="prd",
        title="测试文档",
        content="# 概述\n\n测试内容\n\n## 背景\n\n测试背景"
    )
    return AgentState(document_info=doc_info)


@pytest.mark.asyncio
async def test_docreview_process_basic(
    docreview_agent: DocumentReviewAgent,
    state_with_document: AgentState
) -> None:
    """测试文档审查代理基本处理 / Test Document Review Agent Basic Process"""
    response = await docreview_agent.process(state_with_document)

    assert response.message is not None
    assert response.next_agent == "supervisor"
    assert state_with_document.current_agent == "docreview"
    assert state_with_document.current_iteration == 1


@pytest.mark.asyncio
async def test_docreview_finds_issues(
    docreview_agent: DocumentReviewAgent,
    state_with_document: AgentState
) -> None:
    """测试文档审查发现问题 / Test Document Review Finds Issues"""
    await docreview_agent.process(state_with_document)

    assert state_with_document.review_report is not None
    assert state_with_document.current_iteration >= 1


@pytest.mark.asyncio
async def test_docreview_without_document(docreview_agent: DocumentReviewAgent) -> None:
    """测试无文档情况 / Test Without Document"""
    state = AgentState()

    response = await docreview_agent.process(state)

    assert response.metadata.get("error") is not None


@pytest.mark.asyncio
async def test_docreview_stagnation_detection(
    docreview_agent: DocumentReviewAgent,
    state_with_document: AgentState
) -> None:
    """测试停滞检测 / Test Stagnation Detection"""
    await docreview_agent.process(state_with_document)

    first_hash = state_with_document.previous_finding_hash

    await docreview_agent.process(state_with_document)

    assert state_with_document.consecutive_same_results >= 0


@pytest.mark.asyncio
async def test_load_document(
    docreview_agent: DocumentReviewAgent,
    sample_document_file
) -> None:
    """测试加载文档 / Test Load Document"""
    doc_info = await docreview_agent.load_document(
        str(sample_document_file),
        document_type="test"
    )

    assert doc_info.document_id is not None
    assert doc_info.title == "test_doc.md"
    assert "项目需求文档" in doc_info.content
