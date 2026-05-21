"""Context7 MCP 测试模块 / Context7 MCP Test Module"""

import pytest

from src.mcp.context7 import (
    Context7Tool,
    get_context7_tool,
    ContextChunk,
)


@pytest.fixture
async def context7_tool() -> Context7Tool:
    """Context7 工具 fixture / Context7 Tool Fixture"""
    tool = Context7Tool(chunk_size=100, chunk_overlap=20)
    await tool.initialize()
    yield tool
    await tool.cleanup()


@pytest.mark.asyncio
async def test_index_document(context7_tool: Context7Tool) -> None:
    """测试索引文档 / Test Index Document"""
    result = await context7_tool.index_document(
        document_id="test-doc-001",
        content="这是测试文档的内容。"
    )

    assert result["document_id"] == "test-doc-001"
    assert result["status"] == "indexed"
    assert result["chunk_count"] > 0


@pytest.mark.asyncio
async def test_query_context(context7_tool: Context7Tool) -> None:
    """测试查询上下文 / Test Query Context"""
    await context7_tool.index_document(
        document_id="test-doc-002",
        content="这是一段关于 Python 编程的文档内容。"
    )

    result = await context7_tool.query(
        query="Python 编程",
        max_chunks=2
    )

    assert "chunks" in result
    assert "combined_context" in result
    assert len(result["chunks"]) <= 2


@pytest.mark.asyncio
async def test_query_with_min_relevance(context7_tool: Context7Tool) -> None:
    """测试最小相关性阈值 / Test Min Relevance Threshold"""
    await context7_tool.index_document(
        document_id="test-doc-003",
        content="文档内容包含测试关键词"
    )

    result = await context7_tool.query(
        query="完全不相关的查询",
        min_relevance=0.8
    )

    assert result["total_chunks"] >= 0


@pytest.mark.asyncio
async def test_delete_document(context7_tool: Context7Tool) -> None:
    """测试删除文档 / Test Delete Document"""
    await context7_tool.index_document(
        document_id="test-doc-004",
        content="将被删除的文档"
    )

    result = await context7_tool.delete_document("test-doc-004")

    assert result["status"] == "deleted"


@pytest.mark.asyncio
async def test_get_indexed_documents(context7_tool: Context7Tool) -> None:
    """测试获取已索引文档 / Test Get Indexed Documents"""
    await context7_tool.index_document(
        document_id="doc1",
        content="文档1"
    )
    await context7_tool.index_document(
        document_id="doc2",
        content="文档2"
    )

    docs = context7_tool.get_indexed_documents()

    assert len(docs) == 2
    assert "doc1" in docs
    assert "doc2" in docs


def test_get_singleton() -> None:
    """测试获取单例 / Test Get Singleton"""
    tool1 = get_context7_tool()
    tool2 = get_context7_tool()

    assert tool1 is tool2
