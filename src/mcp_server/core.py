"""MCP 共享逻辑层 —— HTTP 与 stdio 两种传输的**唯一真相源**。

## 为什么要这一层

重构前 `server.py`（HTTP）与 `stdio_server.py`（stdio）各自持有一份工具定义、
请求模型、降级判断与结果拼装，且已实际漂移过一次（请求模型曾可各自修改而不被发现）。
本模块把这些**纯逻辑**收敛到一处，两侧传输层只负责协议信封。

## 刻意不收敛的部分（受规格 F8 约束）

以下差异是**已冻结的对外契约**，由 `tests/test_mcp_server/test_contract.py`
的快照层与不变量层逐字节锁定，**不可**在此统一：

| 维度 | HTTP（旧方法） | stdio |
|---|---|---|
| 工具 schema 键 | `parameters`（含 `optional`/`default` 标记） | `inputSchema`（JSON Schema，含 `required` 数组） |
| 工具 `title` 字段 | 无 | 有 |
| 工具枚举方法 | `list_tools` | `tools/list`（MCP 标准） |
| 工具调用方法 | `invoke` | `tools/call`（MCP 标准） |
| 结果信封 | Pydantic 响应模型 | `content:[{type:text}]` + `metadata` |
| 未知工具 | HTTP 404 | `result` + `metadata.success=false` |
| `health_check` 信封 | 含 `llm_available` 等字段 | `metadata` **不含** `success` |

统一它们需要先改规格 F8 并重生成快照，不在本次优化范围内。
若将来要统一，请先改规格，再跑 `python -m tests.test_mcp_server._regen_snapshots`。

## HTTP 侧的标准方法（2026-09 增量，零破坏）

上表的分歧**仍然存在**，但 HTTP 侧额外接受 MCP 标准方法名，使标准 MCP 客户端
可以连上 HTTP 模式：

| 标准方法 | HTTP 返回 | 对应旧方法 |
|---|---|---|
| `initialize` | 与 stdio 逐字段相同 | 无 |
| `tools/list` | `inputSchema` 形态（规范） | `list_tools` → `parameters` |
| `tools/call` | `content` + `metadata` 信封（规范） | `invoke` → Pydantic 模型 |

要点：

- **老客户端零影响**：旧方法名、参数形状、返回形态、错误码全部原样保留。
- **新标准方法复用 `core` 的塑形函数**，因此同名工具在两种传输上返回
  **完全一致**的 MCP 响应（`test_http_standard_methods_are_mcp_compliant` 锁定）。
- 这是**加法**而非统一：F8 的「schema 不变」约束未被触碰，20 份已跟踪快照
  逐字节未变，新增 4 份快照只锁定新增能力。
- 未覆盖：HTTP 侧仍不是完整 MCP Streamable HTTP 传输（无 GET SSE、
  无会话管理），标准客户端若强依赖这些需自行补齐或改用 stdio 模式。

## 运行时缓存不在本层

`create_workflow_runtime` 的结果缓存**故意留在各传输模块内**（`_runtime_cache`），
因为契约测试按模块 monkeypatch 该名字；收敛它会使替身失效并触达真实 LLM。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

# ─────────────────────── MCP 协议常量 ───────────────────────

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "DocReview MCP Server"
SERVER_VERSION = "1.0.0"
SERVER_DESCRIPTION = "智能文档审查代理系统"

# JSON-RPC 标准错误码
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603
ERR_PARSE = -32700

# ─────────────────────── 请求/响应模型 ───────────────────────
# 只在此定义一次，两侧 import，避免字段各自漂移。


class ReviewRequest(BaseModel):
    """文档审查请求"""

    doc_path: str | None = Field(default=None, description="待审查文档路径")
    task: str | None = Field(default=None, description="任务描述")
    max_iterations: int = Field(default=10, description="最大审查迭代次数")


class ReviewResponse(BaseModel):
    """文档审查响应"""

    success: bool = Field(description="是否成功")
    review_conclusion: str = Field(description="审查结论")
    iteration_count: int = Field(description="迭代轮次")
    total_llm_cost: float = Field(description="LLM 成本")
    issues: list[dict[str, Any]] = Field(default_factory=list, description="发现的问题列表")
    reports: list[dict[str, Any]] = Field(default_factory=list, description="审查报告列表")


class SpecGenerateRequest(BaseModel):
    """规格生成请求"""

    task: str = Field(description="任务描述")
    document_content: str | None = Field(default=None, description="参考文档内容")


class SpecGenerateResponse(BaseModel):
    """规格生成响应"""

    success: bool = Field(description="是否成功")
    specification: str = Field(description="生成的规格文档")
    spec_version: int = Field(description="规格版本")


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(description="服务状态")
    llm_available: bool = Field(description="LLM 是否可用")
    mcp_services: dict[str, bool] = Field(description="MCP 服务状态")


# ─────────────────────── 工具注册表（单一真相源） ───────────────────────


class _Param:
    """单个工具参数的规范描述，两种 schema 渲染器共用。"""

    __slots__ = ("name", "type", "description", "required", "default")

    def __init__(
        self,
        name: str,
        type_: str,
        description: str,
        *,
        required: bool = False,
        default: int | None = None,
    ) -> None:
        self.name = name
        self.type = type_
        self.description = description
        self.required = required
        self.default = default


class _Tool:
    """工具的规范定义。顺序即对外顺序，已被契约测试冻结。"""

    __slots__ = ("name", "title", "description", "params")

    def __init__(self, name: str, title: str, description: str, params: tuple[_Param, ...]) -> None:
        self.name = name
        self.title = title
        self.description = description
        self.params = params


TOOLS: tuple[_Tool, ...] = (
    _Tool(
        "review_document",
        "文档审查",
        "执行文档审查，对产品需求文档、技术方案等进行六步审查",
        (
            _Param("doc_path", "string", "待审查文档路径"),
            _Param("task", "string", "任务描述"),
            _Param("max_iterations", "integer", "最大审查迭代次数", default=10),
        ),
    ),
    _Tool(
        "generate_spec",
        "规格文档生成",
        "根据任务描述生成结构化规格文档",
        (
            _Param("task", "string", "任务描述", required=True),
            _Param("document_content", "string", "参考文档内容"),
        ),
    ),
    _Tool("health_check", "健康检查", "检查 MCP Server 健康状态", ()),
)

TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in TOOLS)


def render_tools_http() -> list[dict[str, Any]]:
    """HTTP `/tools` 与 HTTP JSON-RPC 使用的形态：键名 `parameters`，无 `title`。"""
    out: list[dict[str, Any]] = []
    for tool in TOOLS:
        parameters: dict[str, Any] = {}
        for p in tool.params:
            entry: dict[str, Any] = {"type": p.type, "description": p.description}
            # 契约细节：带 `default` 的参数**不再**标注 optional/required
            # （原实现即如此——`max_iterations` 只有 default，没有 optional）。
            if p.default is not None:
                entry["default"] = p.default
            elif p.required:
                entry["required"] = True
            else:
                entry["optional"] = True
            parameters[p.name] = entry
        out.append(
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": parameters,
            }
        )
    return out


def render_tools_stdio() -> list[dict[str, Any]]:
    """stdio `tools/list` 使用的形态：MCP 标准 `inputSchema`，含 `title`。"""
    out: list[dict[str, Any]] = []
    for tool in TOOLS:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in tool.params:
            properties[p.name] = {"type": p.type, "description": p.description}
            if p.required:
                required.append(p.name)
        out.append(
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return out


# ─────────────────────── 工作流入参构造与结果汇总 ───────────────────────


def build_review_state(request: ReviewRequest) -> dict[str, Any]:
    """`review_document` 的工作流初始状态（HTTP 与 stdio 一致）。"""
    return {
        "user_task": request.task or "",
        "document_path": request.doc_path,
        "max_iterations": request.max_iterations,
    }


def build_spec_state(request: SpecGenerateRequest) -> dict[str, Any]:
    """`generate_spec` 的工作流初始状态（HTTP 与 stdio 一致）。"""
    return {
        "user_task": request.task,
        "document_content": request.document_content or "",
        "max_iterations": 1,
    }


def collect_reports(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """从工作流结果汇总 (reports, issues)。

    保持重构前的遍历顺序：报告按出现顺序，issues 按报告顺序展平。
    """
    reports: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for report in result.get("review_reports", []):
        reports.append(report)
        issues.extend(report.get("issues", []))
    return reports, issues


def summarize_issues(issues: list[dict[str, Any]]) -> str:
    """stdio `review_document` 的可读摘要（最多列 5 条）。"""
    summary = f"审查完成！共发现 {len(issues)} 个问题"
    if issues:
        summary += ":\n" + "\n".join(f"- {issue.get('description', '')}" for issue in issues[:5])
        if len(issues) > 5:
            summary += f"\n...（还有 {len(issues) - 5} 个问题）"
    return summary


# ─────────────────────── 运行时健康状态 ───────────────────────


def _service_ok(runtime: Mapping[str, Any], key: str) -> bool:
    """MCP 子服务是否健康（未降级）。

    与重构前逐字等价（含 `{}` 缺省值）：客户端对象没有 `is_degraded`
    属性时一律记为 False。
    """
    client: Any = runtime.get(key, {})
    return not client.is_degraded if hasattr(client, "is_degraded") else False


def mcp_service_status(runtime: Mapping[str, Any]) -> dict[str, bool]:
    """两个 MCP 子服务的健康状态（重构前此逻辑在两个文件里重复）。"""
    return {
        "sequential_thinking": _service_ok(runtime, "seq_thinking"),
        "context7": _service_ok(runtime, "context7"),
    }


# ─────────────────────── 协议信封 ───────────────────────


def jsonrpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    """JSON-RPC 成功响应。"""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """JSON-RPC 错误响应。"""
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def text_result(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """stdio 结果信封：`content:[{type:text,text}]` + `metadata`。"""
    return {"content": [{"type": "text", "text": text}], "metadata": metadata}


def failed_result(prefix: str, error: str) -> dict[str, Any]:
    """stdio 工具失败信封（`success:false` + 错误文本）。"""
    return text_result(f"{prefix}: {error}", {"success": False, "error": error})


def unknown_tool_result(tool_name: str) -> dict[str, Any]:
    """stdio 未知工具：按契约返回 `result` 而非 JSON-RPC `error`。"""
    message = f"未知工具: {tool_name}"
    return text_result(message, {"success": False, "error": message})


# ─────────────────────── MCP 规范形态的结果塑形 ───────────────────────
# 下面三个函数把「领域结果」转成 MCP 规范的 content/metadata 信封。
# stdio 侧与 HTTP 的标准方法（tools/call）共用，保证两种传输对同一工具
# 产出**完全一致**的 MCP 响应——这是 HTTP 模式能服务标准客户端的前提。


def shape_review_result(result: Mapping[str, Any]) -> dict[str, Any]:
    """工作流结果 → MCP `review_document` 响应。"""
    reports, issues = collect_reports(result)
    return text_result(
        summarize_issues(issues),
        {
            "success": True,
            "review_conclusion": result.get("review_conclusion", "unknown"),
            "iteration_count": result.get("iteration_count", 0),
            "total_llm_cost": result.get("total_llm_cost", 0.0),
            "issue_count": len(issues),
            "reports": reports,
        },
    )


def shape_spec_result(state: Mapping[str, Any]) -> dict[str, Any]:
    """Supervisor 状态 → MCP `generate_spec` 响应。"""
    return text_result(
        state.get("specification", ""),
        {"success": True, "spec_version": state.get("spec_version", 1)},
    )


def shape_health_result(runtime: Mapping[str, Any]) -> dict[str, Any]:
    """运行时 → MCP `health_check` 成功响应。

    注意：按冻结契约，此 metadata **不含** `success` 字段。
    """
    return text_result(
        "服务正常运行",
        {
            "status": "healthy",
            "llm_available": True,
            "mcp_services": mcp_service_status(runtime),
        },
    )


def shape_health_error(error: str) -> dict[str, Any]:
    """运行时初始化失败 → MCP `health_check` 失败响应。"""
    return text_result(
        f"服务异常: {error}",
        {
            "status": "unhealthy",
            "llm_available": False,
            "mcp_services": {"sequential_thinking": False, "context7": False},
            "error": error,
        },
    )


def initialize_result() -> dict[str, Any]:
    """MCP `initialize` 握手结果（两种传输共用同一形状）。"""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "description": SERVER_DESCRIPTION,
        },
    }
