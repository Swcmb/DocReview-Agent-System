"""DocReview MCP Server —— HTTP 传输层

本模块只负责 HTTP/REST 与 JSON-RPC 协议信封。工具定义、请求模型、降级判断与
结果拼装等**共享逻辑**位于 `src/mcp_server/core.py`，是 HTTP 与 stdio 的唯一真相源。

⚠️ 契约冻结：对外 schema 由 `tests/test_mcp_server/test_contract.py` 的快照层
逐字节锁定。本层**不得**改变任何既有字段增删；HTTP 与 stdio 在**旧方法**上的
已知分歧（如 `parameters` vs `inputSchema`）是既有契约而非缺陷，详见 core.py 模块文档。

✅ 标准方法（零破坏增量）：JSON-RPC 端点额外接受 MCP 标准方法名
`initialize` / `tools/list` / `tools/call`，返回规范形态（`inputSchema` +
`content`/`metadata`），使标准 MCP 客户端可连接本模式；旧方法 `list_tools` /
`invoke` 的名称、参数形状、返回形态与错误码全部原样保留，老客户端零影响。
两套方法并存由 `test_http_standard_methods_are_mcp_compliant` 锁定。

`_runtime_cache` 与 `run_review_workflow` 刻意保留在本模块命名空间：契约测试按模块
monkeypatch 这两个名字，收敛到 core 会使替身失效并触达真实 LLM。
"""

import asyncio
import logging
from typing import Any, cast

from fastapi import FastAPI, HTTPException

from ..workflows.review_workflow import create_workflow_runtime, run_review_workflow
from . import core
from .core import (  # noqa: F401  — 再导出以维持 http_srv.ReviewRequest 等公开名（契约测试依赖）
    HealthResponse,
    ReviewRequest,
    ReviewResponse,
    SpecGenerateRequest,
    SpecGenerateResponse,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="DocReview MCP Server", version=core.SERVER_VERSION)

# 存储运行时上下文（契约测试按模块 monkeypatch，勿收敛到 core）
_runtime_cache: dict[str, Any] | None = None


async def _get_runtime() -> dict[str, Any]:
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
        return HealthResponse(
            status="healthy",
            llm_available=True,
            mcp_services=core.mcp_service_status(runtime),
        )
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return HealthResponse(
            status="unhealthy",
            llm_available=False,
            mcp_services={"sequential_thinking": False, "context7": False},
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

        result = await run_review_workflow(core.build_review_state(request))
        reports, issues = core.collect_reports(result)

        return ReviewResponse(
            success=True,
            review_conclusion=result.get("review_conclusion", "unknown"),
            iteration_count=result.get("iteration_count", 0),
            total_llm_cost=result.get("total_llm_cost", 0.0),
            issues=issues,
            reports=reports,
        )

    except Exception as e:
        logger.error(f"审查失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


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

        runtime = await _get_runtime()
        supervisor = runtime["supervisor"]

        state = await supervisor.generate_spec(core.build_spec_state(request))

        return SpecGenerateResponse(
            success=True,
            specification=state.get("specification", ""),
            spec_version=state.get("spec_version", 1),
        )

    except Exception as e:
        logger.error(f"规格生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/tools")
async def list_tools():
    """列出所有可用工具"""
    return {"tools": core.render_tools_http()}


# ─────────── MCP 规范方法（供标准 MCP 客户端使用）───────────
# 旧方法 `list_tools` / `invoke` 返回 HTTP 形态（`parameters`）并被契约快照逐字节锁定；
# 标准方法 `tools/list` / `tools/call` 返回 MCP 规范形态（`inputSchema` + content/metadata）。
# 两者并存：老客户端零破坏，新客户端可用标准握手。


async def _mcp_call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """按 MCP 规范返回 `content`/`metadata` 信封（与 stdio 同形状）。"""
    if tool_name == "review_document":
        result = await run_review_workflow(core.build_review_state(ReviewRequest(**arguments)))
        return core.shape_review_result(result)

    if tool_name == "generate_spec":
        runtime = await _get_runtime()
        state = await runtime["supervisor"].generate_spec(
            core.build_spec_state(SpecGenerateRequest(**arguments))
        )
        return core.shape_spec_result(state)

    if tool_name == "health_check":
        try:
            return core.shape_health_result(await _get_runtime())
        except Exception as e:
            return core.shape_health_error(str(e))

    return core.unknown_tool_result(tool_name)


@app.post("/invoke")
async def invoke_tool(request: dict[str, Any]):
    """通用工具调用接口（MCP JSON-RPC 兼容）"""
    try:
        tool_name = request.get("name")
        arguments = request.get("arguments", {})

        if tool_name == "review_document":
            result = await review_document(ReviewRequest(**arguments))
        elif tool_name == "generate_spec":
            result = await generate_spec(SpecGenerateRequest(**arguments))
        elif tool_name == "health_check":
            result = await health_check()
        else:
            raise HTTPException(status_code=404, detail=f"未知工具: {tool_name}")

        return {"result": result.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"工具调用失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


# MCP 协议兼容的 JSON-RPC 端点
@app.post("/")
async def mcp_json_rpc(request: dict[str, Any]):
    """MCP JSON-RPC 端点"""
    try:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        if request.get("jsonrpc") != "2.0":
            return core.jsonrpc_error(request_id, core.ERR_INVALID_REQUEST, "无效的 JSON-RPC 版本")

        # ── MCP 标准方法：标准客户端握手路径，返回规范形态 ──
        if method == "initialize":
            return core.jsonrpc_result(request_id, core.initialize_result())

        if method == "tools/list":
            return core.jsonrpc_result(request_id, {"tools": core.render_tools_stdio()})

        if method == "tools/call":
            if not isinstance(params, dict) or not params.get("name"):
                return core.jsonrpc_error(request_id, core.ERR_INVALID_PARAMS, "缺少工具名称")
            payload = await _mcp_call_tool(
                cast("str", params["name"]),
                cast("dict[str, Any]", params.get("arguments", {})),
            )
            return core.jsonrpc_result(request_id, payload)

        if method == "list_tools":
            return core.jsonrpc_result(request_id, await list_tools())

        if method == "invoke":
            # params 可能是列表或对象（两种形状均被契约快照锁定）。
            # 非列表时契约上即 JSON-RPC 的 params 对象，故此处 cast 是诚实的。
            if isinstance(params, list):
                tool_call: Any = params[0] if params else {}
            else:
                tool_call = cast("dict[str, Any]", params).get("tool", params)
            tool_name = tool_call.get("name")
            arguments = tool_call.get("arguments", {})

            if tool_name == "review_document":
                result = await review_document(ReviewRequest(**arguments))
            elif tool_name == "generate_spec":
                result = await generate_spec(SpecGenerateRequest(**arguments))
            elif tool_name == "health_check":
                result = await health_check()
            else:
                return core.jsonrpc_error(
                    request_id, core.ERR_METHOD_NOT_FOUND, f"未知方法: {method}"
                )

            return core.jsonrpc_result(
                request_id, result.model_dump() if hasattr(result, "model_dump") else result
            )

        return core.jsonrpc_error(request_id, core.ERR_METHOD_NOT_FOUND, f"未知方法: {method}")

    except Exception as e:
        logger.error(f"MCP JSON-RPC 错误: {e}")
        return core.jsonrpc_error(request.get("id"), core.ERR_INTERNAL, str(e))


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
