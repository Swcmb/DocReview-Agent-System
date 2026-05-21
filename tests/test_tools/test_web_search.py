"""网络搜索工具测试模块 / Web Search Tool Test Module"""

import pytest

from src.tools.web_search import WebSearchTool, create_web_search_tool


@pytest.fixture
def web_search_tool() -> WebSearchTool:
    """网络搜索工具 fixture / Web Search Tool Fixture"""
    return create_web_search_tool(max_results=5)


@pytest.mark.asyncio
async def test_web_search_basic(web_search_tool: WebSearchTool) -> None:
    """测试基本搜索 / Test Basic Search"""
    result = await web_search_tool.execute("test query")

    assert result["query"] == "test query"
    assert result["count"] > 0
    assert len(result["results"]) > 0


@pytest.mark.asyncio
async def test_web_search_with_custom_max_results(web_search_tool: WebSearchTool) -> None:
    """测试自定义最大结果数 / Test Custom Max Results"""
    result = await web_search_tool.execute(
        "test query",
        max_results=2
    )

    assert result["count"] <= 2


@pytest.mark.asyncio
async def test_web_search_multiple(web_search_tool: WebSearchTool) -> None:
    """测试批量搜索 / Test Multiple Search"""
    result = await web_search_tool.search_multiple([
        "query 1",
        "query 2"
    ])

    assert result["total_queries"] == 2
    assert result["successful"] > 0


@pytest.mark.asyncio
async def test_search_result_structure(web_search_tool: WebSearchTool) -> None:
    """测试搜索结果结构 / Test Search Result Structure"""
    result = await web_search_tool.execute("python")

    for item in result["results"]:
        assert "title" in item
        assert "url" in item
        assert "snippet" in item
        assert "source" in item
