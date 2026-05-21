"""顺序思考 MCP 测试模块 / Sequential Thinking MCP Test Module"""

import pytest

from src.mcp.sequential_thinking import (
    SequentialThinkingTool,
    get_sequential_thinking_tool,
    ThinkingStep,
)


@pytest.fixture
async def thinking_tool() -> SequentialThinkingTool:
    """顺序思考工具 fixture / Sequential Thinking Tool Fixture"""
    tool = SequentialThinkingTool(timeout=60, max_steps=3)
    await tool.initialize()
    yield tool
    await tool.cleanup()


@pytest.mark.asyncio
async def test_sequential_thinking_execute(thinking_tool: SequentialThinkingTool) -> None:
    """测试顺序思考执行 / Test Sequential Thinking Execute"""
    result = await thinking_tool.execute(
        problem="测试问题",
        max_steps=2
    )

    assert "steps" in result
    assert "final_conclusion" in result
    assert result["total_steps"] <= 2


@pytest.mark.asyncio
async def test_sequential_thinking_multiple_steps(thinking_tool: SequentialThinkingTool) -> None:
    """测试多步骤思考 / Test Multiple Steps Thinking"""
    result = await thinking_tool.execute(
        problem="复杂问题需要多步分析",
        max_steps=3
    )

    assert len(result["steps"]) >= 1
    assert all("step_number" in step for step in result["steps"])


@pytest.mark.asyncio
async def test_sequential_thinking_history(thinking_tool: SequentialThinkingTool) -> None:
    """测试思考历史 / Test Thinking History"""
    await thinking_tool.execute(
        problem="问题1",
        include_history=True
    )
    await thinking_tool.execute(
        problem="问题2",
        include_history=True
    )

    history = thinking_tool.get_history()
    assert len(history) >= 1


@pytest.mark.asyncio
async def test_sequential_thinking_clear_history(thinking_tool: SequentialThinkingTool) -> None:
    """测试清除历史 / Test Clear History"""
    await thinking_tool.execute(problem="问题", include_history=True)
    thinking_tool.clear_history()

    assert len(thinking_tool.get_history()) == 0


def test_get_singleton() -> None:
    """测试获取单例 / Test Get Singleton"""
    tool1 = get_sequential_thinking_tool()
    tool2 = get_sequential_thinking_tool()

    assert tool1 is tool2
