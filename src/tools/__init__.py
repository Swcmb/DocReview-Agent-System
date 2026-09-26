"""工具模块 / Tools Module

提供文档审查代理系统的核心工具集。
"""

from src.tools.base import (
    BaseTool,
    ToolExecutionError,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    get_tool,
    get_tool_registry,
    register_tool,
)
from src.tools.reading import ReadingTool
from src.tools.terminal import CommandResult, TerminalTool
from src.tools.web_search import ApiValidationResult, SearchResult, WebSearchTool

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
