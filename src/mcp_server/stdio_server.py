"""DocReview MCP Server - stdio 模式

实现基于标准输入输出的 MCP (Model Context Protocol) 服务器，
允许 AI 客户端通过 stdio 方式与 DocReview 智能体系统交互。

协议规范：https://modelcontextprotocol.io/specification/2024-11-05/
- 通过标准输入读取 JSON 请求
- 通过标准输出写入 JSON 响应
- 每行一个 JSON 对象
- 支持 tools/list 和 tools/call 方法

共享逻辑（工具定义、请求模型、降级判断、结果拼装）位于 `core.py`，
是本层与 HTTP 层的唯一真相源。对外契约由 `tests/test_mcp_server/` 的快照锁定，
不可擅自改字段。

`_runtime_cache` 与 `run_review_workflow` 刻意留在本模块命名空间：契约测试按模块
monkeypatch 这两个名字，收敛到 core 会使替身失效并触达真实 LLM。

配置方式：
通过环境变量传递配置：
- LLM_API_KEY: LLM 服务的 API 密钥
- LLM_MODEL: LLM 模型名称（默认: gpt-4o）
- LLM_BASE_URL: LLM 服务基础 URL（可选）
- LOG_LEVEL: 日志级别（默认: INFO）
"""

import asyncio
import json
import logging
import os
import sys
from typing import Any

from ..workflows.review_workflow import create_workflow_runtime, run_review_workflow
from . import core
from .core import (  # noqa: F401  — 再导出以维持 stdio_srv.ReviewRequest 等公开名
    ReviewRequest,
    SpecGenerateRequest,
)

# 配置日志
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("docreview-mcp-stdio")

# 存储运行时上下文（契约测试按模块 monkeypatch，勿收敛到 core）
_runtime_cache: dict[str, Any] | None = None


async def _get_runtime() -> dict[str, Any]:
    """获取或初始化工作流运行时"""
    global _runtime_cache
    if _runtime_cache is None:
        _runtime_cache = await create_workflow_runtime()
    return _runtime_cache


async def list_tools() -> dict[str, Any]:
    """列出所有可用工具（符合 MCP 协议规范）"""
    return {"tools": core.render_tools_stdio()}


async def review_document(doc_path: str | None = None, task: str | None = None, max_iterations: int = 10) -> dict[str, Any]:
    """执行文档审查"""
    try:
        logger.info(f"执行文档审查: doc_path={doc_path}, task={task}")

        request = ReviewRequest(doc_path=doc_path, task=task, max_iterations=max_iterations)
        result = await run_review_workflow(core.build_review_state(request))
        return core.shape_review_result(result)

    except Exception as e:
        logger.error(f"审查失败: {e}", exc_info=True)
        return core.failed_result("审查失败", str(e))


async def generate_spec(task: str, document_content: str | None = None) -> dict[str, Any]:
    """生成规格文档"""
    try:
        logger.info(f"生成规格文档: task={task[:50]}...")

        runtime = await _get_runtime()
        supervisor = runtime["supervisor"]

        request = SpecGenerateRequest(task=task, document_content=document_content)
        state = await supervisor.generate_spec(core.build_spec_state(request))
        return core.shape_spec_result(state)

    except Exception as e:
        logger.error(f"规格生成失败: {e}", exc_info=True)
        return core.failed_result("规格生成失败", str(e))


async def health_check() -> dict[str, Any]:
    """健康检查"""
    try:
        runtime = await _get_runtime()
        return core.shape_health_result(runtime)
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return core.shape_health_error(str(e))


async def invoke_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """调用工具"""
    if tool_name == "review_document":
        return await review_document(**arguments)
    elif tool_name == "generate_spec":
        return await generate_spec(**arguments)
    elif tool_name == "health_check":
        return await health_check()
    else:
        return core.unknown_tool_result(tool_name)


async def process_request(request: dict[str, Any]) -> dict[str, Any]:
    """处理单个请求（符合 MCP 协议规范）"""
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    logger.debug(f"收到请求: id={request_id}, method={method}")

    try:
        if method == "initialize":
            """初始化连接 - MCP 客户端在连接时调用"""
            logger.info("客户端初始化连接")
            return core.jsonrpc_result(request_id, core.initialize_result())

        elif method == "tools/list":
            """列出可用工具"""
            return core.jsonrpc_result(request_id, await list_tools())

        elif method == "tools/call":
            """调用工具"""
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if not tool_name:
                return core.jsonrpc_error(request_id, core.ERR_INVALID_PARAMS, "缺少工具名称")

            return core.jsonrpc_result(request_id, await invoke_tool(tool_name, arguments))

        else:
            return core.jsonrpc_error(request_id, core.ERR_METHOD_NOT_FOUND, f"未知方法: {method}")

    except Exception as e:
        logger.error(f"处理请求失败: {e}", exc_info=True)
        return core.jsonrpc_error(request_id, core.ERR_INTERNAL, str(e))


async def stdio_server():
    """启动 stdio 模式的 MCP Server"""
    logger.info("启动 DocReview MCP Server (stdio 模式)")
    logger.info("配置已从环境变量加载")

    # 初始化运行时（异步）
    asyncio.create_task(_initialize_runtime())

    # 读取输入并处理
    loop = asyncio.get_event_loop()

    while True:
        try:
            # 异步读取一行输入
            line = await loop.run_in_executor(None, sys.stdin.readline)

            if not line:
                # 输入流结束
                logger.info("输入流结束，退出服务器")
                break

            line = line.strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error(f"无效的 JSON: {e}")
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"JSON 解析错误: {e}"}
                }
                print(json.dumps(response))
                sys.stdout.flush()
                continue

            # 处理请求
            response = await process_request(request)

            # 输出响应
            print(json.dumps(response))
            sys.stdout.flush()

        except KeyboardInterrupt:
            logger.info("收到中断信号，退出服务器")
            break
        except Exception as e:
            logger.error(f"服务器运行错误: {e}", exc_info=True)


async def _initialize_runtime():
    """异步初始化运行时"""
    try:
        await _get_runtime()
        logger.info("工作流运行时初始化完成")
    except Exception as e:
        logger.error(f"运行时初始化失败: {e}", exc_info=True)


def main():
    """主入口"""
    try:
        asyncio.run(stdio_server())
    except KeyboardInterrupt:
        logger.info("服务器已停止")
    except Exception as e:
        logger.error(f"服务器启动失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
