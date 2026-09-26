"""配置管理模块 / Configuration Management Module

本模块提供应用程序的集中化配置管理，使用 pydantic-settings 实现类型安全的配置加载。
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


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


class LayaConfig(BaseSettings):
    """Laya 决策层配置模型 / Laya Decision Layer Configuration Model

    严格遵循规格 §11.1。**本类不提供任何可覆盖业务阈值的字段**——阈值只能来自
    calibration manifest（§11.1 禁令、`LayaConfig` 不得覆盖）。因此这里没有
    threshold / epsilon / cutover 之类的旋钮：任何「调阈值以改善表现」的诉求都必须
    走重新校准，而不是改配置。

    路径类字段在 ``enabled=true`` 时必须为绝对路径；缺失或相对路径**不抛异常**，
    而是让运行时进入 audit-only 降级（结果 ``uncertain`` / ``no_action``），
    见 §7.1 与 §11.1。
    """

    enabled: bool = Field(default=False, description="Laya 决策层总开关（默认关闭）")
    runtime_commit: str = Field(
        default="",
        description="Laya 运行时权威 commit；启用时必须为完整 40 位 SHA",
    )
    model: str = Field(
        default="auto",
        description="逻辑模型名：auto/english/multilingual/typed-decisions；"
        "auto 与 typed-decisions 仅 audit-only，任何 act 必须显式 english 或 multilingual",
    )
    model_dir: str = Field(
        default="",
        description="权重 bundle 根目录；启用时必须为绝对路径，缺失/相对路径则 audit-only 降级",
    )
    checkpoint_manifest_path: str = Field(
        default="",
        description="checkpoint manifest 绝对路径；不得写入上游权重仓库",
    )
    calibration_path: str = Field(
        default="",
        description="calibration 绝对路径；缺失/相对路径则 audit-only 降级",
    )
    device: str = Field(
        default="auto",
        description="推理设备；可能 act 的运行必须为 cpu，auto 仅 audit-only",
    )
    max_len: int = Field(default=8192, gt=0, description="输入 token 上限，不得超过 checkpoint 上限")
    head_max_len: int = Field(default=256, gt=0, description="head token 上限，必须小于 max_len")
    chunk_limit: int = Field(default=8, gt=0, description="分块上限；规格固定为 8，超过则头尾各 4")
    max_chars_per_chunk: int = Field(default=1024, gt=0, description="单块字符预算（固定安全值）")
    batch_size: int = Field(default=4, gt=0, description="Agent forward batch 上限")
    max_batch_rows: int = Field(default=8, gt=0, description="单个 batch 逻辑行上限（v1 固定为 8）")
    max_batch_encoded_chars: int = Field(
        default=16384,
        gt=0,
        description="单 batch encoded state 字符上限（v1 固定为 16384）",
    )
    timeout_seconds: int = Field(default=120, gt=0, description="单次推理超时时间（秒）")


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
    laya: LayaConfig = Field(default_factory=LayaConfig, description="Laya 决策层配置")

    @model_validator(mode="after")
    def _warn_flat_laya_env(self) -> "AppConfig":
        """扁平 ``LAYA_*`` 环境变量只告警，不写入 ``config.laya``（§11.1）。

        嵌套子配置用 ``__`` 分隔（``env_nested_delimiter="__"``），所以
        ``LAYA_ENABLED`` 不会被解析成 ``config.laya.enabled``——它会因
        ``extra="ignore"`` 被静默丢弃，而静默丢弃正是最难排查的一类配置 bug。
        这里显式告警，把「配了但没生效」变成可见问题。
        """
        flat = sorted(
            name
            for name in os.environ
            if name.upper().startswith("LAYA_") and not name.upper().startswith("LAYA__")
        )
        if flat:
            logger.warning(
                "检测到扁平 Laya 环境变量 %s；它们不会写入 config.laya。"
                "请改用双下划线形式（如 LAYA__ENABLED / LAYA__MODEL）。",
                ", ".join(flat),
            )
        return self


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
