"""DocReview MCP Server

将 DocReview 智能体系统封装为 MCP Server，提供文档审查、规格生成等功能。

MCP (Model Context Protocol) 是一种标准协议，允许 LLM 与外部工具和服务进行交互。
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..workflows.review_workflow import create_workflow_runtime, run_review_workflow

logger = logging.getLogger(__name__)

app = FastAPI(title="DocReview MCP Server", version="1.0.0")

# 存储运行时上下文
_runtime_cache: Optional[Dict[str, Any]] = None


class ReviewRequest(BaseModel):
    """文档审查请求"""
    doc_path: Optional[str] = Field(default=None, description="待审查文档路径")
    task: Optional[str] = Field(default=None, description="任务描述")
    max_iterations: int = Field(default=10, description="最大审查迭代次数")


class ReviewResponse(BaseModel):
    """文档审查响应"""
    success: bool = Field(description="是否成功")
    review_conclusion: str = Field(description="审查结论")
    iteration_count: int = Field(description="迭代轮次")
    total_llm_cost: float = Field(description="LLM 成本")
    issues: List[Dict[str, Any]] = Field(default_factory=list, description="发现的问题列表")
    reports: List[Dict[str, Any]] = Field(default_factory=list, description="审查报告列表")


class SpecGenerateRequest(BaseModel):
    """规格生成请求"""
    task: str = Field(description="任务描述")
    document_content: Optional[str] = Field(default=None, description="参考文档内容")


class SpecGenerateResponse(BaseModel):
    """规格生成响应"""
    success: bool = Field(description="是否成功")
    specification: str = Field(description="生成的规格文档")
    spec_version: int = Field(description="规格版本")


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = Field(description="服务状态")
    llm_available: bool = Field(description="LLM 是否可用")
    mcp_services: Dict[str, bool] = Field(description="MCP 服务状态")


async def _get_runtime() -> Dict[str, Any]:
    """获取或初始化工作流运行时"""
    global _runtime_cache
    if _runtime_cache is None:
        _runtime_cache = await create_workflow_runtime()
    return _runtime_cache


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    try:
        runtime = await _get_runtime()
        llm_available = True
        mcp_services = {
            "sequential_thinking": not runtime.get("seq_thinking", {}).is_degraded if hasattr(runtime.get("seq_thinking"), "is_degraded") else False,
            "context7": not runtime.get("context7", {}).is_degraded if hasattr(runtime.get("context7"), "is_degraded") else False
        }
        return HealthResponse(
            status="healthy",
            llm_available=llm_available,
            mcp_services=mcp_services
        )
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return HealthResponse(
            status="unhealthy",
            llm_available=False,
            mcp_services={"sequential_thinking": False, "context7": False}
        )


@app.post("/review", response_model=ReviewResponse)
async def review_document(request: ReviewRequest):
    """执行文档审查

    Args:
        request: 审查请求参数
    
    Returns:
        ReviewResponse: 审查结果
    """
    try:
        logger.info(f"收到审查请求: doc_path={request.doc_path}, task={request.task}")
        
        initial_state = {
            "user_task": request.task or "",
            "document_path": request.doc_path,
            "max_iterations": request.max_iterations
        }
        
        result = await run_review_workflow(initial_state)
        
        issues = []
        reports = []
        for report in result.get("review_reports", []):
            reports.append(report)
            issues.extend(report.get("issues", []))
        
        return ReviewResponse(
            success=True,
            review_conclusion=result.get("review_conclusion", "unknown"),
            iteration_count=result.get("iteration_count", 0),
            total_llm_cost=result.get("total_llm_cost", 0.0),
            issues=issues,
            reports=reports
        )
    
    except Exception as e:
        logger.error(f"审查失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generate-spec", response_model=SpecGenerateResponse)
async def generate_spec(request: SpecGenerateRequest):
    """生成规格文档

    Args:
        request: 规格生成请求参数
    
    Returns:
        SpecGenerateResponse: 规格文档
    """
    try:
        logger.info(f"收到规格生成请求: task={request.task[:50]}...")
        
        initial_state = {
            "user_task": request.task,
            "document_content": request.document_content or "",
            "max_iterations": 1
        }
        
        runtime = await _get_runtime()
        supervisor = runtime["supervisor"]
        
        state = await supervisor.generate_spec(initial_state)
        
        return SpecGenerateResponse(
            success=True,
            specification=state.get("specification", ""),
            spec_version=state.get("spec_version", 1)
        )
    
    except Exception as e:
        logger.error(f"规格生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/tools")
async def list_tools():
    """列出所有可用工具"""
    tools = [
        {
            "name": "review_document",
            "description": "执行文档审查，对产品需求文档、技术方案等进行六步审查",
            "parameters": {
                "doc_path": {"type": "string", "description": "待审查文档路径", "optional": True},
                "task": {"type": "string", "description": "任务描述", "optional": True},
                "max_iterations": {"type": "integer", "description": "最大审查迭代次数", "default": 10}
            }
        },
        {
            "name": "generate_spec",
            "description": "根据任务描述生成结构化规格文档",
            "parameters": {
                "task": {"type": "string", "description": "任务描述", "required": True},
                "document_content": {"type": "string", "description": "参考文档内容", "optional": True}
            }
        },
        {
            "name": "health_check",
            "description": "检查 MCP Server 健康状态",
            "parameters": {}
        }
    ]
    return {"tools": tools}


@app.post("/invoke")
async def invoke_tool(request: Dict[str, Any]):
    """通用工具调用接口（MCP JSON-RPC 兼容）"""
    try:
        tool_name = request.get("name")
        arguments = request.get("arguments", {})
        
        if tool_name == "review_document":
            result = await review_document(ReviewRequest(**arguments))
            return {"result": result.dict()}
        
        elif tool_name == "generate_spec":
            result = await generate_spec(SpecGenerateRequest(**arguments))
            return {"result": result.dict()}
        
        elif tool_name == "health_check":
            result = await health_check()
            return {"result": result.dict()}
        
        else:
            raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"工具调用失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# MCP 协议兼容的 JSON-RPC 端点
@app.post("/")
async def mcp_json_rpc(request: Dict[str, Any]):
    """MCP JSON-RPC 端点"""
    try:
        jsonrpc_version = request.get("jsonrpc")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        
        if jsonrpc_version != "2.0":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32600, "message": "无效的 JSON-RPC 版本"}
            }
        
        if method == "list_tools":
            tools = await list_tools()
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": tools
            }
        
        elif method == "invoke":
            # params 可能是列表或对象
            if isinstance(params, list) and len(params) > 0:
                tool_call = params[0] if params else {}
            else:
                tool_call = params.get("tool", params)
            
            tool_name = tool_call.get("name")
            arguments = tool_call.get("arguments", {})
            
            if tool_name == "review_document":
                result = await review_document(ReviewRequest(**arguments))
            elif tool_name == "generate_spec":
                result = await generate_spec(SpecGenerateRequest(**arguments))
            elif tool_name == "health_check":
                result = await health_check()
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"未知方法: {method}"}
                }
            
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result.dict() if hasattr(result, "dict") else result
            }
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"未知方法: {method}"}
            }
    
    except Exception as e:
        logger.error(f"MCP JSON-RPC 错误: {e}")
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32603, "message": str(e)}
        }


async def start_server(host: str = "127.0.0.1", port: int = 8000):
    """启动 MCP Server"""
    import uvicorn
    logger.info(f"启动 DocReview MCP Server: http://{host}:{port}")
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_server())
