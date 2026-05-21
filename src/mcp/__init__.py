"""MCP 客户端模块 / MCP Client Module

提供 MCP（Model Context Protocol）客户端实现，支持与 MCP Server 的标准 JSON-RPC 协议通信。
"""

from .base import (
    MCPError,
    MCPTimeoutError,
    MCPConnectionError,
    MCPResponseError,
    MCPProcess,
    BaseMCPClient,
)

from .sequential_thinking import (
    ThinkingStep,
    ThinkingResult,
    SequentialThinkingClient,
)

from .context7 import (
    DocResult,
    ContextResult,
    Context7Client,
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
