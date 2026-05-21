"""文档读取工具测试模块 / Document Reading Tool Test Module"""

import pytest
from pathlib import Path

from src.tools.reading import ReadingTool
from src.tools.base import ToolExecutionError, ToolResult


@pytest.fixture
def reading_tool(temp_dir) -> ReadingTool:
    """文档读取工具 fixture / Document Reading Tool Fixture"""
    return ReadingTool(workspace_dir=str(temp_dir))


@pytest.mark.asyncio
async def test_read_document_success(
    reading_tool: ReadingTool,
    sample_document_file
) -> None:
    """测试成功读取文档 / Test Successful Document Reading"""
    result = reading_tool.read_file(str(sample_document_file))

    assert result.success is True
    assert "项目需求文档" in result.data.get("content", "")
    assert result.data.get("path") is not None


@pytest.mark.asyncio
async def test_read_nonexistent_file(reading_tool: ReadingTool) -> None:
    """测试读取不存在的文件 / Test Reading Non-existent File"""
    result = reading_tool.read_file("nonexistent_file.md")

    assert result.success is False


@pytest.mark.asyncio
async def test_read_with_encoding(reading_tool: ReadingTool, sample_document_file) -> None:
    """测试指定编码读取 / Test Reading with Encoding"""
    result = reading_tool.read_file(str(sample_document_file), encoding="utf-8")

    assert result.success is True


@pytest.mark.asyncio
async def test_read_multiple_documents(
    reading_tool: ReadingTool,
    temp_dir
) -> None:
    """测试批量读取文档 / Test Batch Reading Documents"""
    file1 = temp_dir / "doc1.md"
    file2 = temp_dir / "doc2.md"
    file1.write_text("文档1内容")
    file2.write_text("文档2内容")

    result1 = reading_tool.read_file(str(file1))
    result2 = reading_tool.read_file(str(file2))

    assert result1.success is True
    assert result2.success is True
    assert "文档1内容" in result1.data.get("content", "")
    assert "文档2内容" in result2.data.get("content", "")


@pytest.mark.asyncio
async def test_list_files(reading_tool: ReadingTool, temp_dir) -> None:
    """测试列出文件 / Test List Files"""
    (temp_dir / "test1.md").write_text("内容1")
    (temp_dir / "test2.txt").write_text("内容2")

    result = reading_tool.list_directory()

    assert result.success is True
    assert result.data.get("count", 0) >= 1


@pytest.mark.asyncio
async def test_read_file_with_different_encoding(
    reading_tool: ReadingTool,
    temp_dir
) -> None:
    """测试不同编码读取 / Test Reading with Different Encoding"""
    file_path = temp_dir / "gbk_file.txt"
    file_path.write_text("中文内容", encoding="gbk")

    result = reading_tool.read_file(str(file_path), encoding="gbk")

    assert result.success is True


@pytest.mark.asyncio
async def test_read_file_path_traversal_attempt(
    reading_tool: ReadingTool,
    temp_dir
) -> None:
    """测试路径遍历攻击防护 / Test Path Traversal Attack Prevention"""
    result = reading_tool.read_file("../../../etc/passwd")

    assert result.success is False


@pytest.mark.asyncio
async def test_read_file_outside_workspace(
    reading_tool: ReadingTool,
) -> None:
    """测试读取工作目录外的文件 / Test Reading Files Outside Workspace"""
    result = reading_tool.read_file("/tmp/test.txt")

    assert result.success is False


@pytest.mark.asyncio
async def test_read_empty_file(
    reading_tool: ReadingTool,
    temp_dir
) -> None:
    """测试读取空文件 / Test Reading Empty File"""
    empty_file = temp_dir / "empty.md"
    empty_file.write_text("")

    result = reading_tool.read_file(str(empty_file))

    assert result.success is True


@pytest.mark.asyncio
async def test_search_text_success(
    reading_tool: ReadingTool,
    sample_document_file
) -> None:
    """测试文本搜索成功 / Test Text Search Success"""
    content = reading_tool.read_file(str(sample_document_file))
    result = reading_tool.search_text(content.data.get("content", ""), r"用户")

    assert result.success is True
    assert result.data.get("count", 0) > 0


@pytest.mark.asyncio
async def test_search_text_no_matches(
    reading_tool: ReadingTool,
    sample_document_file
) -> None:
    """测试文本搜索无匹配 / Test Text Search No Matches"""
    content = reading_tool.read_file(str(sample_document_file))
    result = reading_tool.search_text(content.data.get("content", ""), r"不存在的关键词123")

    assert result.success is True
    assert result.data.get("count", 0) == 0


@pytest.mark.asyncio
async def test_search_text_invalid_regex(
    reading_tool: ReadingTool,
    sample_document_file
) -> None:
    """测试无效正则表达式 / Test Invalid Regex"""
    content = reading_tool.read_file(str(sample_document_file))
    result = reading_tool.search_text(content.data.get("content", ""), r"[invalid(")

    assert result.success is False


@pytest.mark.asyncio
async def test_list_directory_with_pattern(
    reading_tool: ReadingTool,
    temp_dir
) -> None:
    """测试目录列表模式匹配 / Test Directory List with Pattern"""
    (temp_dir / "file1.md").write_text("内容1")
    (temp_dir / "file2.md").write_text("内容2")
    (temp_dir / "file3.txt").write_text("内容3")

    result = reading_tool.list_directory(pattern="*.md")

    assert result.success is True
    assert result.data.get("count", 0) == 2


@pytest.mark.asyncio
async def test_list_directory_nonexistent(
    reading_tool: ReadingTool
) -> None:
    """测试列出不存在的目录 / Test List Non-existent Directory"""
    result = reading_tool.list_directory(path="nonexistent_dir")

    assert result.success is False


@pytest.mark.asyncio
async def test_compare_versions_different(
    reading_tool: ReadingTool
) -> None:
    """测试版本对比（不同）/ Test Version Compare (Different)"""
    v1 = "第一行内容\n第二行内容\n"
    v2 = "第一行内容\n修改后的第二行\n第三行内容\n"

    result = reading_tool.compare_versions(v1, v2)

    assert result.success is True
    assert result.data.get("has_changes") is True
    assert "---" in result.data.get("diff", "")
    assert "+++" in result.data.get("diff", "")


@pytest.mark.asyncio
async def test_compare_versions_identical(
    reading_tool: ReadingTool
) -> None:
    """测试版本对比（相同）/ Test Version Compare (Identical)"""
    content = "相同的内容\n"

    result = reading_tool.compare_versions(content, content)

    assert result.success is True
    assert result.data.get("has_changes") is False


@pytest.mark.asyncio
async def test_execute_invalid_operation(
    reading_tool: ReadingTool
) -> None:
    """测试无效操作 / Test Invalid Operation"""
    result = reading_tool.execute("invalid_operation")

    assert result.success is False


def test_reading_tool_initialization(temp_dir) -> None:
    """测试 ReadingTool 初始化 / Test ReadingTool Initialization"""
    tool = ReadingTool(workspace_dir=str(temp_dir))

    assert tool.workspace_dir == Path(temp_dir).resolve()
    assert tool.max_file_size == 10 * 1024 * 1024


def test_reading_tool_is_path_safe(temp_dir) -> None:
    """测试路径安全校验 / Test Path Safety Check"""
    tool = ReadingTool(workspace_dir=str(temp_dir))

    assert tool._is_path_safe("test.txt") is True
    assert tool._is_path_safe("../etc/passwd") is False
    assert tool._is_path_safe("/etc/passwd") is False


def test_tool_result_model() -> None:
    """测试 ToolResult 模型 / Test ToolResult Model"""
    success_result = ToolResult(success=True, data={"key": "value"})
    assert success_result.success is True
    assert success_result.data == {"key": "value"}

    error_result = ToolResult(success=False, error="错误信息")
    assert error_result.success is False
    assert error_result.error == "错误信息"
