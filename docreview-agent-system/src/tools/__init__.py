"""工具模块 / Tools Module

提供文档审查代理系统的核心工具集。
"""

from src.tools.base import (
    BaseTool,
    ToolResult,
    ToolExecutionError,
    ToolValidationError,
    ToolRegistry,
    get_tool_registry,
    register_tool,
    get_tool
)
from src.tools.reading import ReadingTool
from src.tools.terminal import TerminalTool, CommandResult
from src.tools.web_search import WebSearchTool, SearchResult, ApiValidationResult

__all__ = [
    "BaseTool",
    "ToolResult",
    "ToolExecutionError",
    "ToolValidationError",
    "ToolRegistry",
    "get_tool_registry",
    "register_tool",
    "get_tool",
    "ReadingTool",
    "TerminalTool",
    "CommandResult",
    "WebSearchTool",
    "SearchResult",
    "ApiValidationResult"
]
