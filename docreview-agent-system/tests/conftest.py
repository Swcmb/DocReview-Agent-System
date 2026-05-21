"""测试配置模块 / Test Configuration Module

提供 pytest 的全局配置和 fixtures。
"""

import asyncio
from pathlib import Path
from typing import Generator
import tempfile

import pytest
from pydantic import BaseModel

from src.utils.llm import CostTracker


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """创建事件循环 fixture / Create Event Loop Fixture"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """创建临时目录 fixture / Create Temp Directory Fixture"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_document_content() -> str:
    """示例文档内容 / Sample Document Content"""
    return """
# 项目需求文档

## 概述
本文档描述了项目的核心需求。

## 背景
项目需要解决现有系统的问题。

## 功能需求
1. 用户管理
2. 权限控制
3. 数据分析

## 非功能需求
- 性能要求
- 安全要求
"""


@pytest.fixture
def sample_document_file(temp_dir: Path, sample_document_content: str) -> Path:
    """创建示例文档文件 / Create Sample Document File"""
    file_path = temp_dir / "test_doc.md"
    file_path.write_text(sample_document_content, encoding="utf-8")
    return file_path


class MockLLMResponse(BaseModel):
    """模拟 LLM 响应 / Mock LLM Response"""
    content: str
    finish_reason: str = "stop"


@pytest.fixture
def mock_llm_response() -> MockLLMResponse:
    """模拟 LLM 响应 fixture / Mock LLM Response Fixture"""
    return MockLLMResponse(
        content="这是一个模拟的 LLM 响应"
    )


@pytest.fixture
def mock_openai_response_metadata() -> dict:
    """模拟 OpenAI 响应元数据 / Mock OpenAI Response Metadata"""
    return {
        "content": "这是一个模拟的 LLM 响应",
        "token_usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150
        },
        "model": "gpt-4o",
    }


@pytest.fixture
def mock_anthropic_response_metadata() -> dict:
    """模拟 Anthropic 响应元数据 / Mock Anthropic Response Metadata"""
    return {
        "content": "这是一个模拟的 Claude 响应",
        "usage": {
            "input_tokens": 200,
            "output_tokens": 100,
        },
        "model": "claude-3-5-sonnet",
    }


@pytest.fixture
def cost_tracker() -> CostTracker:
    """成本追踪器 fixture / Cost Tracker Fixture"""
    return CostTracker()


@pytest.fixture
def sample_spec_document() -> str:
    """示例规格文档 / Sample Spec Document"""
    return """# 示例项目 - 产品需求文档

## 概述
这是一个示例项目。

## 功能需求
- **FR-1**: 用户登录功能

## 验收标准
### AC-1
- **Given**: 用户未登录
- **When**: 访问登录页面
- **Then**: 显示登录表单
"""
