"""配置管理模块 / Configuration Management Module

本模块提供应用程序的集中化配置管理，使用 pydantic-settings 实现类型安全的配置加载。
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """LLM 配置模型 / LLM Configuration Model"""

    provider: Literal["openai", "anthropic", "azure"] = Field(
        default="openai",
        description="LLM 提供商"
    )
    model: str = Field(default="gpt-4o", description="模型名称")
    api_key: str = Field(default="", description="API 密钥")
    base_url: str = Field(default="https://api.openai.com/v1", description="API 基础 URL")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="生成温度")
    request_timeout: int = Field(default=120, gt=0, description="请求超时时间（秒）")


class MCPConfig(BaseSettings):
    """MCP 配置模型 / MCP Configuration Model"""

    sequential_thinking_enabled: bool = Field(
        default=True,
        description="是否启用顺序思考 MCP"
    )
    context7_enabled: bool = Field(
        default=True,
        description="是否启用 Context7 MCP"
    )
    call_timeout: int = Field(default=60, gt=0, description="MCP 调用超时时间（秒）")


class AgentBehaviorConfig(BaseSettings):
    """代理行为配置模型 / Agent Behavior Configuration Model"""

    max_review_iterations: int = Field(
        default=10,
        gt=0,
        description="最大审查迭代次数"
    )
    stagnation_threshold: int = Field(
        default=3,
        gt=0,
        description="停滞阈值（连续相同结果次数）"
    )
    user_approval_timeout: int = Field(
        default=300,
        gt=0,
        description="用户批准超时时间（秒）"
    )
    max_cost_per_task: float = Field(
        default=10.0,
        gt=0,
        description="单任务最大成本限制（美元）"
    )


class SystemConfig(BaseSettings):
    """系统配置模型 / System Configuration Model"""

    workspace_dir: Path = Field(default=Path("./workspace"), description="工作空间目录")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="日志级别"
    )
    log_file: Path = Field(default=Path("./logs/docreview.log"), description="日志文件路径")
    log_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0, description="日志最大大小（字节）")
    log_backup_count: int = Field(default=5, ge=0, description="日志备份数量")


class AppConfig(BaseSettings):
    """应用程序主配置类 / Application Main Configuration Class

    该类整合所有子配置模块，提供统一的配置访问接口。
    支持从环境变量和 .env 文件加载配置。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore"
    )

    llm: LLMConfig = Field(default_factory=LLMConfig, description="LLM 配置")
    mcp: MCPConfig = Field(default_factory=MCPConfig, description="MCP 配置")
    agent_behavior: AgentBehaviorConfig = Field(
        default_factory=AgentBehaviorConfig,
        description="代理行为配置"
    )
    system: SystemConfig = Field(default_factory=SystemConfig, description="系统配置")


@lru_cache
def get_config() -> AppConfig:
    """获取配置单例 / Get Configuration Singleton

    使用 lru_cache 缓存配置实例，避免重复解析。

    Returns:
        AppConfig: 应用程序配置实例
    """
    return AppConfig()


def reload_config() -> AppConfig:
    """重新加载配置 / Reload Configuration

    清除缓存并重新加载配置。

    Returns:
        AppConfig: 重新加载后的配置实例
    """
    get_config.cache_clear()
    return get_config()
