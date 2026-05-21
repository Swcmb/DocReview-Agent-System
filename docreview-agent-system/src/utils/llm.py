"""LLM 工具模块 / LLM Utility Module

提供 LLM 调用封装，支持多种提供商和成本追踪。
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Dict

from src.config import get_config
from src.utils.logger import get_logger

logger = get_logger(__name__)

# LLM 定价表（$/1M tokens）- prompt_price, completion_price
LLM_PRICING: Dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4-turbo": (10.00, 30.00),
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
}


@dataclass
class CostTracker:
    """成本追踪器 / Cost Tracker
    
    用于追踪 LLM 调用的 token 用量和累计成本。
    """
    total_cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    request_count: int = 0
    _request_history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """转换为字典 / Convert to Dictionary"""
        return {
            "total_cost": self.total_cost,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "request_count": self.request_count,
        }

    def reset(self) -> None:
        """重置追踪器 / Reset Tracker"""
        self.total_cost = 0.0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.request_count = 0
        self._request_history.clear()


def track_llm_cost(
    model: str,
    response_metadata: dict,
    cost_tracker: Optional[CostTracker] = None
) -> float:
    """追踪 LLM 成本 / Track LLM Cost
    
    从响应元数据中提取 token 用量并计算成本。
    
    Args:
        model: 模型名称
        response_metadata: LLM 响应元数据（包含 token_usage 或 usage）
        cost_tracker: 成本追踪器（可选）
        
    Returns:
        float: 本次调用的成本（美元）
    """
    pricing = LLM_PRICING.get(model, (2.50, 10.00))
    prompt_price, completion_price = pricing
    
    tokens = _extract_tokens(response_metadata)
    
    if tokens:
        prompt_tokens, completion_tokens = tokens
    else:
        content = response_metadata.get("content", "")
        estimated_tokens = len(content) / 4 * 1.3
        prompt_tokens = int(estimated_tokens * 0.3)
        completion_tokens = int(estimated_tokens * 0.7)
    
    cost = (prompt_tokens / 1_000_000) * prompt_price + \
           (completion_tokens / 1_000_000) * completion_price
    
    if cost_tracker:
        cost_tracker.total_cost += cost
        cost_tracker.prompt_tokens += prompt_tokens
        cost_tracker.completion_tokens += completion_tokens
        cost_tracker.request_count += 1
        cost_tracker._request_history.append({
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost,
        })
    
    logger.debug(
        f"LLM 成本: ${cost:.6f} "
        f"(model={model}, prompt={prompt_tokens}, completion={completion_tokens})"
    )
    
    return cost


def _extract_tokens(response_metadata: dict) -> Optional[tuple[int, int]]:
    """从响应元数据中提取 token 用量 / Extract Tokens from Response Metadata
    
    支持 OpenAI 和 Anthropic 格式。
    
    Args:
        response_metadata: LLM 响应元数据
        
    Returns:
        Optional[tuple[int, int]]: (prompt_tokens, completion_tokens) 或 None
    """
    if "token_usage" in response_metadata:
        usage = response_metadata["token_usage"]
        return (
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0)
        )
    
    if "usage" in response_metadata:
        usage = response_metadata["usage"]
        return (
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0)
        )
    
    return None


def check_budget(
    cost_tracker: CostTracker,
    max_budget: float
) -> tuple[bool, str]:
    """检查是否超出预算 / Check Budget
    
    Args:
        cost_tracker: 成本追踪器
        max_budget: 最大预算（美元），<=0 表示不限制
        
    Returns:
        tuple[bool, str]: (是否超预算, 状态消息)
    """
    if max_budget <= 0:
        return False, ""
    
    if cost_tracker.total_cost > max_budget:
        return True, f"超出预算: ${cost_tracker.total_cost:.4f} > ${max_budget:.2f}"
    
    return False, ""


class LLMError(Exception):
    """LLM 调用异常 / LLM Error"""
    pass


class LLMClient:
    """LLM 客户端 / LLM Client

    封装 LLM 调用逻辑，支持多种提供商。
    """

    def __init__(self) -> None:
        """初始化 LLM 客户端 / Initialize LLM Client"""
        self.config = get_config()
        self._client: Any = None

    async def initialize(self) -> None:
        """初始化 LLM 客户端 / Initialize LLM Client"""
        provider = self.config.llm.provider

        if provider == "openai":
            await self._init_openai()
        elif provider == "anthropic":
            await self._init_anthropic()
        else:
            raise LLMError(f"不支持的 LLM 提供商: {provider}")

        logger.info(f"LLM 客户端初始化完成，提供商: {provider}")

    async def _init_openai(self) -> None:
        """初始化 OpenAI 客户端 / Initialize OpenAI Client"""
        try:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(
                api_key=self.config.llm.api_key,
                base_url=self.config.llm.base_url,
                timeout=self.config.llm.request_timeout
            )
        except ImportError:
            raise LLMError("请安装 openai 包: pip install openai")

    async def _init_anthropic(self) -> None:
        """初始化 Anthropic 客户端 / Initialize Anthropic Client"""
        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=self.config.llm.api_key,
                timeout=self.config.llm.request_timeout
            )
        except ImportError:
            raise LLMError("请安装 anthropic 包: pip install anthropic")

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """生成文本 / Generate Text

        Args:
            prompt: 用户提示
            system: 系统提示
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            str: 生成的文本
        """
        if self._client is None:
            await self.initialize()

        temperature = temperature or self.config.llm.temperature

        try:
            if self.config.llm.provider == "openai":
                return await self._generate_openai(
                    prompt, system, temperature, max_tokens
                )
            elif self.config.llm.provider == "anthropic":
                return await self._generate_anthropic(
                    prompt, system, temperature, max_tokens
                )
        except Exception as e:
            logger.error(f"LLM 生成失败: {e}")
            raise LLMError(f"LLM 生成失败: {e}") from e

        raise LLMError("未初始化的 LLM 客户端")

    async def _generate_openai(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        max_tokens: Optional[int]
    ) -> str:
        """使用 OpenAI 生成 / Generate with OpenAI

        Args:
            prompt: 用户提示
            system: 系统提示
            temperature: 温度
            max_tokens: 最大 token 数

        Returns:
            str: 生成的文本
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self.config.llm.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )

        return response.choices[0].message.content

    async def _generate_anthropic(
        self,
        prompt: str,
        system: Optional[str],
        temperature: float,
        max_tokens: Optional[int]
    ) -> str:
        """使用 Anthropic 生成 / Generate with Anthropic

        Args:
            prompt: 用户提示
            system: 系统提示
            temperature: 温度
            max_tokens: 最大 token 数

        Returns:
            str: 生成的文本
        """
        response = await self._client.messages.create(
            model=self.config.llm.model,
            system=system or "",
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens or 4096
        )

        return response.content[0].text


_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端单例 / Get LLM Client Singleton

    Returns:
        LLMClient: LLM 客户端实例
    """
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


async def generate_response(
    prompt: str,
    system: Optional[str] = None
) -> str:
    """快捷函数：生成 LLM 响应 / Convenience Function: Generate LLM Response

    Args:
        prompt: 用户提示
        system: 系统提示

    Returns:
        str: 生成的文本
    """
    client = get_llm_client()
    return await client.generate(prompt, system=system)
