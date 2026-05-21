"""工具基类模块 / Tools Base Module

定义代理工具的基础接口和通用功能。
"""

from abc import ABC, abstractmethod
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, Field
from src.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class ToolExecutionError(Exception):
    """工具执行异常 / Tool Execution Error"""
    pass


class ToolValidationError(ToolExecutionError):
    """工具参数验证异常 / Tool Validation Error"""
    pass


class ToolResult(BaseModel):
    """工具执行结果基类 / Tool Result Base Model"""
    success: bool = Field(description="执行是否成功 / Whether execution succeeded")
    data: Optional[Any] = Field(default=None, description="执行结果数据 / Result data")
    error: Optional[str] = Field(default=None, description="错误信息 / Error message")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据 / Additional metadata")


class BaseTool(ABC):
    """工具基类 / Tool Base Class

    定义工具的通用接口，所有工具需继承此类并实现具体逻辑。
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None
    ) -> None:
        """初始化工具 / Initialize Tool

        Args:
            name: 工具名称
            description: 工具描述
            parameters: 工具参数模式（JSON Schema）
        """
        self.name = name
        self.description = description
        self.parameters = parameters or {}
        self.logger = get_logger(f"{__name__}.{name}")

    @abstractmethod
    def execute(self, *args, **kwargs) -> ToolResult:
        """执行工具的核心逻辑 / Execute Tool Core Logic

        Args:
            *args: 位置参数
            **kwargs: 关键字参数

        Returns:
            ToolResult: 工具执行结果
        """
        pass

    def validate_params(self, **params) -> tuple[bool, Optional[str]]:
        """验证参数 / Validate Parameters

        Args:
            **params: 待验证的参数

        Returns:
            tuple[bool, Optional[str]]: (是否有效, 错误信息)
        """
        return True, None

    def validate_parameters(self, parameters: dict[str, Any]) -> bool:
        """验证工具参数 / Validate Tool Parameters

        Args:
            parameters: 待验证的参数

        Returns:
            bool: 验证是否通过
        """
        required = self.parameters.get("required", [])
        for param in required:
            if param not in parameters:
                raise ToolValidationError(f"缺少必需参数: {param}")
        return True

    def _create_success_result(self, data: Any, **metadata) -> ToolResult:
        """创建成功结果 / Create Success Result

        Args:
            data: 结果数据
            **metadata: 附加元数据

        Returns:
            ToolResult: 成功结果
        """
        return ToolResult(success=True, data=data, metadata=metadata)

    def _create_error_result(self, error: str, **metadata) -> ToolResult:
        """创建错误结果 / Create Error Result

        Args:
            error: 错误信息
            **metadata: 附加元数据

        Returns:
            ToolResult: 错误结果
        """
        return ToolResult(success=False, error=error, metadata=metadata)

    def get_schema(self) -> dict[str, Any]:
        """获取工具参数模式 / Get Tool Parameter Schema

        Returns:
            dict[str, Any]: 参数模式
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }


class ToolRegistry:
    """工具注册表 / Tool Registry

    管理和注册所有可用工具。
    """

    def __init__(self) -> None:
        """初始化工具注册表 / Initialize Tool Registry"""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册工具 / Register Tool

        Args:
            tool: 工具实例
        """
        self._tools[tool.name] = tool
        logger.info(f"已注册工具: {tool.name}")

    def unregister(self, name: str) -> bool:
        """注销工具 / Unregister Tool

        Args:
            name: 工具名称

        Returns:
            bool: 是否成功注销
        """
        if name in self._tools:
            del self._tools[name]
            logger.info(f"已注销工具: {name}")
            return True
        return False

    def get(self, name: str) -> BaseTool | None:
        """获取工具 / Get Tool

        Args:
            name: 工具名称

        Returns:
            BaseTool | None: 工具实例
        """
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, Any]]:
        """列出所有工具 / List All Tools

        Returns:
            list[dict[str, Any]]: 工具列表
        """
        return [tool.get_schema() for tool in self._tools.values()]

    def has_tool(self, name: str) -> bool:
        """检查工具是否存在 / Check if Tool Exists

        Args:
            name: 工具名称

        Returns:
            bool: 是否存在
        """
        return name in self._tools


_tool_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """获取工具注册表单例 / Get Tool Registry Singleton

    Returns:
        ToolRegistry: 工具注册表实例
    """
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


def register_tool(tool: BaseTool) -> None:
    """快捷函数：注册工具 / Convenience Function: Register Tool

    Args:
        tool: 工具实例
    """
    get_tool_registry().register(tool)


def get_tool(name: str) -> BaseTool | None:
    """快捷函数：获取工具 / Convenience Function: Get Tool

    Args:
        name: 工具名称

    Returns:
        BaseTool | None: 工具实例
    """
    return get_tool_registry().get(name)
