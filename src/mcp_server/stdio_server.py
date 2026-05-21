"""DocReview MCP Server - stdio 模式

实现基于标准输入输出的 MCP (Model Context Protocol) 服务器，
允许 AI 客户端通过 stdio 方式与 DocReview 智能体系统交互。

协议规范：https://modelcontextprotocol.io/specification/2024-11-05/
- 通过标准输入读取 JSON 请求
- 通过标准输出写入 JSON 响应
- 每行一个 JSON 对象
- 支持 tools/list 和 tools/call 方法

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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from ..workflows.review_workflow import create_workflow_runtime, run_review_workflow

# 配置日志
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("docreview-mcp-stdio")

# 存储运行时上下文
_runtime_cache: Optional[Dict[str, Any]] = None


class ReviewRequest(BaseModel):
    """文档审查请求"""
    doc_path: Optional[str] = Field(default=None, description="待审查文档路径")
    task: Optional[str] = Field(default=None, description="任务描述")
    max_iterations: int = Field(default=10, description="最大审查迭代次数")


class SpecGenerateRequest(BaseModel):
    """规格生成请求"""
    task: str = Field(description="任务描述")
    document_content: Optional[str] = Field(default=None, description="参考文档内容")


async def _get_runtime() -> Dict[str, Any]:
    """获取或初始化工作流运行时"""
    global _runtime_cache
    if _runtime_cache is None:
        _runtime_cache = await create_workflow_runtime()
    return _runtime_cache


async def list_tools() -> Dict[str, Any]:
    """列出所有可用工具（符合 MCP 协议规范）"""
    tools = [
        {
            "name": "review_document",
            "title": "文档审查",
            "description": "执行文档审查，对产品需求文档、技术方案等进行六步审查",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "doc_path": {"type": "string", "description": "待审查文档路径"},
                    "task": {"type": "string", "description": "任务描述"},
                    "max_iterations": {"type": "integer", "description": "最大审查迭代次数"}
                },
                "required": []
            }
        },
        {
            "name": "generate_spec",
            "title": "规格文档生成",
            "description": "根据任务描述生成结构化规格文档",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "任务描述"},
                    "document_content": {"type": "string", "description": "参考文档内容"}
                },
                "required": ["task"]
            }
        },
        {
            "name": "health_check",
            "title": "健康检查",
            "description": "检查 MCP Server 健康状态",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    ]
    return {"tools": tools}


async def review_document(doc_path: Optional[str] = None, task: Optional[str] = None, max_iterations: int = 10) -> Dict[str, Any]:
    """执行文档审查"""
    try:
        logger.info(f"执行文档审查: doc_path={doc_path}, task={task}")
        
        initial_state = {
            "user_task": task or "",
            "document_path": doc_path,
            "max_iterations": max_iterations
        }
        
        result = await run_review_workflow(initial_state)
        
        issues = []
        reports = []
        for report in result.get("review_reports", []):
            reports.append(report)
            issues.extend(report.get("issues", []))
        
        summary = f"审查完成！共发现 {len(issues)} 个问题"
        if issues:
            summary += ":\n" + "\n".join([f"- {issue.get('description', '')}" for issue in issues[:5]])
            if len(issues) > 5:
                summary += f"\n...（还有 {len(issues) - 5} 个问题）"
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": summary
                }
            ],
            "metadata": {
                "success": True,
                "review_conclusion": result.get("review_conclusion", "unknown"),
                "iteration_count": result.get("iteration_count", 0),
                "total_llm_cost": result.get("total_llm_cost", 0.0),
                "issue_count": len(issues),
                "reports": reports
            }
        }
    
    except Exception as e:
        logger.error(f"审查失败: {e}", exc_info=True)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"审查失败: {str(e)}"
                }
            ],
            "metadata": {
                "success": False,
                "error": str(e)
            }
        }


async def generate_spec(task: str, document_content: Optional[str] = None) -> Dict[str, Any]:
    """生成规格文档"""
    try:
        logger.info(f"生成规格文档: task={task[:50]}...")
        
        initial_state = {
            "user_task": task,
            "document_content": document_content or "",
            "max_iterations": 1
        }
        
        runtime = await _get_runtime()
        supervisor = runtime["supervisor"]
        
        state = await supervisor.generate_spec(initial_state)
        
        specification = state.get("specification", "")
        
        return {
            "content": [
                {
                    "type": "text",
                    "text": specification
                }
            ],
            "metadata": {
                "success": True,
                "spec_version": state.get("spec_version", 1)
            }
        }
    
    except Exception as e:
        logger.error(f"规格生成失败: {e}", exc_info=True)
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"规格生成失败: {str(e)}"
                }
            ],
            "metadata": {
                "success": False,
                "error": str(e)
            }
        }


async def health_check() -> Dict[str, Any]:
    """健康检查"""
    try:
        runtime = await _get_runtime()
        llm_available = True
        mcp_services = {
            "sequential_thinking": not runtime.get("seq_thinking", {}).is_degraded if hasattr(runtime.get("seq_thinking"), "is_degraded") else False,
            "context7": not runtime.get("context7", {}).is_degraded if hasattr(runtime.get("context7"), "is_degraded") else False
        }
        
        status_text = "服务正常运行"
        return {
            "content": [
                {
                    "type": "text",
                    "text": status_text
                }
            ],
            "metadata": {
                "status": "healthy",
                "llm_available": llm_available,
                "mcp_services": mcp_services
            }
        }
    except Exception as e:
        logger.error(f"健康检查失败: {e}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"服务异常: {str(e)}"
                }
            ],
            "metadata": {
                "status": "unhealthy",
                "llm_available": False,
                "mcp_services": {"sequential_thinking": False, "context7": False},
                "error": str(e)
            }
        }


async def invoke_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """调用工具"""
    if tool_name == "review_document":
        return await review_document(**arguments)
    elif tool_name == "generate_spec":
        return await generate_spec(**arguments)
    elif tool_name == "health_check":
        return await health_check()
    else:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"未知工具: {tool_name}"
                }
            ],
            "metadata": {
                "success": False,
                "error": f"未知工具: {tool_name}"
            }
        }


async def process_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """处理单个请求（符合 MCP 协议规范）"""
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})
    
    logger.debug(f"收到请求: id={request_id}, method={method}")
    
    try:
        if method == "initialize":
            """初始化连接 - MCP 客户端在连接时调用"""
            logger.info("客户端初始化连接")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "DocReview MCP Server",
                        "version": "1.0.0",
                        "description": "智能文档审查代理系统"
                    }
                }
            }
        
        elif method == "tools/list":
            """列出可用工具"""
            result = await list_tools()
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
        
        elif method == "tools/call":
            """调用工具"""
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            
            if not tool_name:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": "缺少工具名称"}
                }
            
            result = await invoke_tool(tool_name, arguments)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": result
            }
        
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"未知方法: {method}"}
            }
    
    except Exception as e:
        logger.error(f"处理请求失败: {e}", exc_info=True)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32603, "message": str(e)}
        }


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