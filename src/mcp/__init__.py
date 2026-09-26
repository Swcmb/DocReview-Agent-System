"""MCP 客户端模块 / MCP Client Module

提供 MCP（Model Context Protocol）客户端实现，支持与 MCP Server 的标准 JSON-RPC 协议通信。
"""

from .base import (
    BaseMCPClient,
    MCPConnectionError,
    MCPError,
    MCPProcess,
    MCPResponseError,
    MCPTimeoutError,
)
from .context7 import (
    Context7Client,
    ContextResult,
    DocResult,
)
from .sequential_thinking import (
    SequentialThinkingClient,
    ThinkingResult,
    ThinkingStep,
)

__all__ = [
    "MCPError",
    "MCPTimeoutError",
    "MCPConnectionError",
    "MCPResponseError",
    "MCPProcess",
    "BaseMCPClient",
    "ThinkingStep",
    "ThinkingResult",
    "SequentialThinkingClient",
    "DocResult",
    "ContextResult",
    "Context7Client",
]
