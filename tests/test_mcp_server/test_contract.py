"""MCP 对外契约特征化测试 / Characterization Tests for the MCP Contract (T-00b)

冻结 HTTP 与 stdio 两个对外面的 JSON 契约（规格 F8／§0.5）。集成决策层时
**不得**改动这些 schema——任何改动都会破坏既有 MCP 客户端。

契约以**快照文件**形式固定在 `tests/test_mcp_server/snapshots/*.json`，
逐字节比对（`json.dumps(..., ensure_ascii=False, indent=2, sort_keys=True)`）。
断言分两层：

1. **快照层**：20 份快照逐字节一致，捕获任何字段增删改。
2. **不变量层**：显式写出最容易被重构悄悄破坏的契约点，即使快照被误重生成
   也能拦住（工具名集合、JSON-RPC 错误码、MCP 协议版本、HTTP 与 stdio
   的 schema 键名分歧等）。

若确需变更对外契约，必须先由规格显式批准，再重跑
`& $python -m tests.test_mcp_server._regen_snapshots` 并在同一提交中说明原因。

未覆盖说明：`-32700`（JSON 解析错误）只在 `stdio_server()` 的读循环内产生，
不经由 `process_request`，故不在快照范围内。
"""

import json
from pathlib import Path
from typing import Any

import pytest

from src.mcp_server import server as http_srv
from src.mcp_server import stdio_server as stdio_srv

SNAP_DIR = Path(__file__).parent / "snapshots"

# 快照名 → 契约域，仅用于失败信息分组
SNAPSHOT_DOMAINS = {
    "http_review_request": "HTTP 请求模型",
    "http_review_response": "HTTP 响应模型",
    "http_spec_request": "HTTP 请求模型",
    "http_spec_response": "HTTP 响应模型",
    "http_health_response": "HTTP 响应模型",
    "http_health_llm_missing": "HTTP 降级运行时",
    "http_tools": "HTTP /tools",
    "http_openapi": "HTTP OpenAPI",
    "http_jsonrpc_list_tools": "HTTP JSON-RPC",
    "http_jsonrpc_bad_version": "HTTP JSON-RPC",
    "http_jsonrpc_unknown_method": "HTTP JSON-RPC",
    "http_jsonrpc_invoke_health": "HTTP JSON-RPC",
    "http_jsonrpc_invoke_params_list": "HTTP JSON-RPC",
    "http_jsonrpc_initialize": "HTTP 标准方法",
    "http_jsonrpc_tools_list": "HTTP 标准方法",
    "http_jsonrpc_tools_call_health": "HTTP 标准方法",
    "http_jsonrpc_tools_call_missing_name": "HTTP 标准方法",
    "stdio_initialize": "stdio 握手",
    "stdio_tools_list": "stdio tools/list",
    "stdio_tools_call_missing_name": "stdio tools/call",
    "stdio_unknown_method": "stdio JSON-RPC",
    "stdio_unknown_tool": "stdio tools/call",
    "stdio_call_health": "stdio tools/call",
    "stdio_call_health_llm_missing": "stdio 降级运行时",
    "stdio_call_review": "stdio tools/call",
    "stdio_call_generate_spec": "stdio tools/call",
}

TOOL_NAMES = ("review_document", "generate_spec", "health_check")


# ─────────────────────────── 替身与夹具 ───────────────────────────


class _NotDegraded:
    """`hasattr(x, "is_degraded")` 为真的最小健康 MCP 客户端。"""

    is_degraded = False


class _FakeLLM:
    """最小健康 LLM 客户端替身。

    代表「运行时初始化成功」的真实形态——真实 runtime 必然含 `llm` 键。
    缺了它，`llm_available` 派生判断会误报 False，把「健康运行时」快照
    钉成谎话。
    """


class _FakeSupervisor:
    async def generate_spec(self, state: Any) -> dict:
        return {"specification": "# 规格\n\n## 概述\n占位", "spec_version": 1}


async def _fake_run_review_workflow(initial_state: Any) -> dict:
    """固定一轮审查结果，避免测试触达真实 LLM。"""
    return {
        "review_reports": [
            {
                "iteration": 1,
                "timestamp": "2026-09-26T00:00:00",
                "review_conclusion": "Pass",
                "review_summary": "发现 1 个问题",
                "issues": [
                    {
                        "issue_id": "MD-1-1",
                        "severity": "Medium",
                        "issue_type": "ConsistencyCheck",
                        "description": "术语不一致",
                        "suggestion": "统一术语",
                        "location": "第 3 节",
                        "status": "open",
                    }
                ],
                "highlights": [],
                "open_questions": [],
                "next_steps": "",
            }
        ],
        "review_conclusion": "Pass",
        "iteration_count": 1,
        "total_llm_cost": 0.0123,
    }


def _install_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """用替身接管运行时，使契约采集不触达 LLM / MCP / checkpoint。"""
    runtime = {
        "llm": _FakeLLM(),
        "seq_thinking": _NotDegraded(),
        "context7": _NotDegraded(),
        "supervisor": _FakeSupervisor(),
    }
    monkeypatch.setattr(http_srv, "_runtime_cache", runtime)
    monkeypatch.setattr(stdio_srv, "_runtime_cache", runtime)
    monkeypatch.setattr(http_srv, "run_review_workflow", _fake_run_review_workflow)
    monkeypatch.setattr(stdio_srv, "run_review_workflow", _fake_run_review_workflow)


def _install_llm_missing_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """装一个**缺 LLM 客户端**的运行时。

    代表「运行时起来了但 LLM 不可用」的降级形态（写死 `True` 的旧实现
    在此场景下会说谎——报告一个并不存在的能力）。
    """
    runtime = {
        "seq_thinking": _NotDegraded(),
        "context7": _NotDegraded(),
        "supervisor": _FakeSupervisor(),
    }
    monkeypatch.setattr(http_srv, "_runtime_cache", runtime)
    monkeypatch.setattr(stdio_srv, "_runtime_cache", runtime)


# ─────────────────────────── 契约采集 ───────────────────────────


async def collect_snapshots(mp: pytest.MonkeyPatch | None = None) -> dict:
    """从真实模块采集当前对外契约。

    同时被本模块的测试与 `_regen_snapshots.py` 使用，保证「基线怎么生成」
    与「测试怎么比对」永远同源。

    Args:
        mp: 可选。传入后额外采集「缺 LLM 客户端」的降级运行时快照——
            该场景需要切换 runtime，依赖 monkeypatch。
    """
    out: dict[str, Any] = {}

    out["http_review_request"] = http_srv.ReviewRequest.model_json_schema()
    out["http_review_response"] = http_srv.ReviewResponse.model_json_schema()
    out["http_spec_request"] = http_srv.SpecGenerateRequest.model_json_schema()
    out["http_spec_response"] = http_srv.SpecGenerateResponse.model_json_schema()
    out["http_health_response"] = http_srv.HealthResponse.model_json_schema()
    out["http_tools"] = await http_srv.list_tools()
    out["http_openapi"] = http_srv.app.openapi()

    out["http_jsonrpc_list_tools"] = await http_srv.mcp_json_rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "list_tools"}
    )
    out["http_jsonrpc_bad_version"] = await http_srv.mcp_json_rpc(
        {"jsonrpc": "1.0", "id": 2, "method": "list_tools"}
    )
    out["http_jsonrpc_unknown_method"] = await http_srv.mcp_json_rpc(
        {"jsonrpc": "2.0", "id": 3, "method": "no_such_method"}
    )
    out["http_jsonrpc_invoke_health"] = await http_srv.mcp_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "invoke",
            "params": {"tool": {"name": "health_check", "arguments": {}}},
        }
    )
    out["http_jsonrpc_invoke_params_list"] = await http_srv.mcp_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "invoke",
            "params": [{"name": "health_check", "arguments": {}}],
        }
    )

    # HTTP 侧的标准 MCP 方法（返回规范形态，与旧方法并存互不影响）
    out["http_jsonrpc_initialize"] = await http_srv.mcp_json_rpc(
        {"jsonrpc": "2.0", "id": 6, "method": "initialize"}
    )
    out["http_jsonrpc_tools_list"] = await http_srv.mcp_json_rpc(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}
    )
    out["http_jsonrpc_tools_call_health"] = await http_srv.mcp_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        }
    )
    out["http_jsonrpc_tools_call_missing_name"] = await http_srv.mcp_json_rpc(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {}}
    )

    out["stdio_initialize"] = await stdio_srv.process_request({"id": 1, "method": "initialize"})
    out["stdio_tools_list"] = await stdio_srv.process_request({"id": 2, "method": "tools/list"})
    out["stdio_tools_call_missing_name"] = await stdio_srv.process_request(
        {"id": 3, "method": "tools/call", "params": {}}
    )
    out["stdio_unknown_method"] = await stdio_srv.process_request({"id": 4, "method": "no/such"})
    out["stdio_unknown_tool"] = await stdio_srv.process_request(
        {"id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    out["stdio_call_health"] = await stdio_srv.process_request(
        {"id": 6, "method": "tools/call", "params": {"name": "health_check", "arguments": {}}}
    )
    out["stdio_call_review"] = await stdio_srv.process_request(
        {
            "id": 7,
            "method": "tools/call",
            "params": {"name": "review_document", "arguments": {"task": "评审"}},
        }
    )
    out["stdio_call_generate_spec"] = await stdio_srv.process_request(
        {
            "id": 8,
            "method": "tools/call",
            "params": {"name": "generate_spec", "arguments": {"task": "设计认证"}},
        }
    )

    # 降级运行时（缺 LLM 客户端）：`llm_available` 必须诚实报 false。
    # 需要 monkeypatch 才能切换 runtime，故仅在传入时采集。
    if mp is not None:
        _install_llm_missing_runtime(mp)
        out["http_health_llm_missing"] = (await http_srv.health_check()).model_dump()
        out["stdio_call_health_llm_missing"] = await stdio_srv.invoke_tool("health_check", {})

    return out


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _read_snapshot(name: str) -> str:
    path = SNAP_DIR / f"{name}.json"
    assert path.exists(), (
        f"缺少契约快照 {path.name}。若确为已批准的契约变更，"
        f"请运行 `& $python -m tests.test_mcp_server._regen_snapshots`"
    )
    return path.read_text(encoding="utf-8")


# ─────────────────────── 快照层：逐字节比对 ───────────────────────


async def test_contract_snapshots_match_byte_for_byte(monkeypatch: pytest.MonkeyPatch):
    """一次比对全部 22 份快照；任一字段增删改都会在此失败并给出完整 diff 上下文。"""
    _install_stubs(monkeypatch)
    live = await collect_snapshots(monkeypatch)

    assert set(live) == set(SNAPSHOT_DOMAINS), (
        f"契约集合与基线不一致：多出 {set(live) - set(SNAPSHOT_DOMAINS)}，"
        f"缺少 {set(SNAPSHOT_DOMAINS) - set(live)}"
    )

    mismatched = []
    for name in sorted(live):
        expected = _read_snapshot(name)
        actual = _canonical(live[name])
        if expected != actual:
            mismatched.append(name)

    assert not mismatched, (
        "以下 MCP 对外契约快照发生变化："
        + "、".join(f"{n}（{SNAPSHOT_DOMAINS[n]}）" for n in mismatched)
        + "。若非规格批准的变更，请回退实现；确需变更请重跑 _regen_snapshots 并说明原因。"
    )


# ─────────────────── 不变量层：最易被重构破坏的契约点 ───────────────────


async def test_stdio_reports_mcp_2024_11_05(monkeypatch: pytest.MonkeyPatch):
    """不变量：stdio 握手必须宣告 MCP 协议版本 `2024-11-05`。"""
    _install_stubs(monkeypatch)
    result = (await stdio_srv.process_request({"id": 1, "method": "initialize"}))["result"]

    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "DocReview MCP Server"
    assert result["serverInfo"]["version"] == "1.0.0"


async def test_tool_name_set_is_frozen(monkeypatch: pytest.MonkeyPatch):
    """不变量：三个工具的名称与顺序在 HTTP 与 stdio 两侧都不可变。"""
    _install_stubs(monkeypatch)

    http_names = [t["name"] for t in (await http_srv.list_tools())["tools"]]
    stdio_names = [t["name"] for t in (await stdio_srv.list_tools())["tools"]]

    assert http_names == list(TOOL_NAMES)
    assert stdio_names == list(TOOL_NAMES)


async def test_http_and_stdio_schema_keys_diverge(monkeypatch: pytest.MonkeyPatch):
    """不变量（**已知的既有分歧**）：HTTP 用 `parameters`，stdio 用 MCP 标准的 `inputSchema`。

    两侧键名不同是既有事实。规格只要求「schema 不变」，故此处锁定现状而非
    修正它——若将来统一，必须先更新规格与本测试。
    """
    _install_stubs(monkeypatch)

    http_tool = (await http_srv.list_tools())["tools"][0]
    stdio_tool = (await stdio_srv.list_tools())["tools"][0]

    assert "parameters" in http_tool and "inputSchema" not in http_tool
    assert "inputSchema" in stdio_tool and "parameters" not in stdio_tool
    assert stdio_tool["inputSchema"]["type"] == "object"


async def test_http_standard_methods_are_mcp_compliant(monkeypatch: pytest.MonkeyPatch):
    """不变量：HTTP 侧新增的标准方法返回 **MCP 规范形态**，旧方法形态不变。

    2026-09 增量引入，非破坏：`list_tools`/`invoke` 仍返回 HTTP 形态（`parameters`），
    而 `tools/list`/`tools/call` 返回规范形态（`inputSchema` + content/metadata）。
    这使标准 MCP 客户端能连上 HTTP 模式，同时老客户端零改动。
    """
    _install_stubs(monkeypatch)

    # 标准 tools/list 必须给 inputSchema，否则标准客户端无法解析工具定义
    std_shape = (
        await http_srv.mcp_json_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    )["result"]["tools"][0]
    assert "inputSchema" in std_shape and "parameters" not in std_shape

    # 旧 list_tools 形态必须原样保留（老客户端依赖 parameters）
    legacy = (await http_srv.list_tools())["tools"][0]
    assert "parameters" in legacy and "inputSchema" not in legacy

    # 标准 tools/call 必须返回 content/metadata 信封
    called = await http_srv.mcp_json_rpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        }
    )
    assert called["result"]["metadata"]["status"] == "healthy"
    assert called["result"]["content"][0]["type"] == "text"

    # 两侧同名工具的响应形状应完全一致（塑形逻辑共用 core）
    stdio_called = await stdio_srv.process_request(
        {
            "id": 2,
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        }
    )
    assert called["result"] == stdio_called["result"]


async def test_http_standard_handshake_matches_stdio(monkeypatch: pytest.MonkeyPatch):
    """不变量：两种传输的 `initialize` 握手结果完全相同。"""
    _install_stubs(monkeypatch)

    http_init = await http_srv.mcp_json_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    stdio_init = await stdio_srv.process_request({"id": 1, "method": "initialize"})

    assert http_init["result"] == stdio_init["result"]
    assert http_init["result"]["protocolVersion"] == "2024-11-05"


async def test_stdio_required_lists_are_frozen(monkeypatch: pytest.MonkeyPatch):
    """不变量：仅 `generate_spec` 的 `task` 为必填，其余全部可选。"""
    _install_stubs(monkeypatch)
    tools = {t["name"]: t for t in (await stdio_srv.list_tools())["tools"]}

    assert tools["generate_spec"]["inputSchema"]["required"] == ["task"]
    assert tools["review_document"]["inputSchema"]["required"] == []
    assert tools["health_check"]["inputSchema"]["required"] == []
    assert tools["health_check"]["inputSchema"]["properties"] == {}


@pytest.mark.parametrize(
    ("payload_name", "method", "params", "expected_code"),
    [
        ("http_jsonrpc_bad_version", "list_tools", {}, -32600),
        ("http_jsonrpc_unknown_method", "no_such_method", {}, -32601),
        ("stdio_unknown_method", "no/such", {}, -32601),
        ("stdio_tools_call_missing_name", "tools/call", {}, -32602),
    ],
)
async def test_jsonrpc_error_codes_are_frozen(
    monkeypatch: pytest.MonkeyPatch,
    payload_name: str,
    method: str,
    params: dict,
    expected_code: int,
):
    """不变量：JSON-RPC 标准错误码不得被改写。

    -32600 无效请求 / -32601 方法未知 / -32602 参数非法。
    """
    _install_stubs(monkeypatch)
    request: dict = {"id": 99, "method": method, "params": params}
    if payload_name.startswith("http"):
        request["jsonrpc"] = "1.0" if expected_code == -32600 else "2.0"
        response = await http_srv.mcp_json_rpc(request)
    else:
        response = await stdio_srv.process_request(request)

    assert response["error"]["code"] == expected_code
    assert response["jsonrpc"] == "2.0"


async def test_stdio_unknown_tool_is_result_not_protocol_error(monkeypatch: pytest.MonkeyPatch):
    """不变量（**已知的既有不对称**）：stdio 未知工具返回 `result` + `success: false`，
    而非 JSON-RPC `error`；HTTP 侧则返回 HTTP 404。锁定现状。"""
    _install_stubs(monkeypatch)

    stdio_resp = await stdio_srv.process_request(
        {"id": 5, "method": "tools/call", "params": {"name": "nope", "arguments": {}}}
    )
    assert "error" not in stdio_resp
    assert stdio_resp["result"]["metadata"]["success"] is False
    assert stdio_resp["result"]["metadata"]["error"] == "未知工具: nope"


async def test_stdio_result_envelope_shape(monkeypatch: pytest.MonkeyPatch):
    """不变量：stdio 所有工具结果统一为 `content:[{type:text,text}]` + `metadata`。

    注意（**已知的既有不对称**）：`health_check` 的 metadata **不含** `success`，
    而 `review_document` / `generate_spec` 含。此处按现状锁定，不做统一。
    """
    _install_stubs(monkeypatch)

    for tool, args in (
        ("health_check", {}),
        ("review_document", {"task": "评审"}),
        ("generate_spec", {"task": "设计认证"}),
    ):
        resp = await stdio_srv.process_request(
            {"id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
        )
        result = resp["result"]
        assert set(result) == {"content", "metadata"}, f"{tool} 结果信封键变了"
        assert result["content"][0]["type"] == "text"
        assert isinstance(result["content"][0]["text"], str)

    # 只有两个业务工具带 success 标志
    for tool, args in (("review_document", {}), ("generate_spec", {"task": "设计认证"})):
        metadata = (
            await stdio_srv.process_request(
                {"id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
            )
        )["result"]["metadata"]
        assert metadata["success"] is True, f"{tool} 成功路径 metadata.success 不为 True"

    health_metadata = (
        await stdio_srv.process_request(
            {"id": 1, "method": "tools/call", "params": {"name": "health_check", "arguments": {}}}
        )
    )["result"]["metadata"]
    assert "success" not in health_metadata, "health_check 历史上不带 success 字段"
    assert set(health_metadata) == {"status", "llm_available", "mcp_services"}


async def test_stdio_review_metadata_keys(monkeypatch: pytest.MonkeyPatch):
    """不变量：`review_document` 的 metadata 键集固定（客户端据此读取结论与成本）。"""
    _install_stubs(monkeypatch)
    resp = await stdio_srv.process_request(
        {"id": 7, "method": "tools/call", "params": {"name": "review_document", "arguments": {}}}
    )
    metadata = resp["result"]["metadata"]

    assert set(metadata) == {
        "success",
        "review_conclusion",
        "iteration_count",
        "total_llm_cost",
        "issue_count",
        "reports",
    }
    assert metadata["issue_count"] == 1
    assert metadata["review_conclusion"] == "Pass"


async def test_http_response_models_keep_required_fields(monkeypatch: pytest.MonkeyPatch):
    """不变量：HTTP 响应模型的必填/默认属性不得增删。"""
    _install_stubs(monkeypatch)

    assert set(http_srv.ReviewResponse.model_fields) == {
        "success",
        "review_conclusion",
        "iteration_count",
        "total_llm_cost",
        "issues",
        "reports",
    }
    # issues / reports 为 default_factory，缺省为空列表而非必填
    assert http_srv.ReviewResponse.model_fields["issues"].default_factory is not None
    assert set(http_srv.HealthResponse.model_fields) == {
        "status",
        "llm_available",
        "mcp_services",
    }
    # max_iterations 默认 10 —— 客户端依赖该缺省
    assert http_srv.ReviewRequest().max_iterations == 10
    assert http_srv.ReviewRequest().doc_path is None


def test_openapi_route_surface_is_frozen():
    """不变量：对外路由面固定为 9 条。

    前 6 条是既有契约（规格要求决策层接入不得增删端点）；后 3 条是
    2026-09 批准的 MCP Streamable HTTP 传输补全（`/mcp` 的 POST/GET/DELETE）。
    两者都不得再增删——新增端点须先改规格。
    """
    spec = http_srv.app.openapi()
    routes = {(method.upper(), path) for path, ops in spec["paths"].items() for method in ops}

    assert routes == {
        ("GET", "/health"),
        ("POST", "/review"),
        ("POST", "/generate-spec"),
        ("GET", "/tools"),
        ("POST", "/invoke"),
        ("POST", "/"),
        # MCP Streamable HTTP（2025-03-26）
        ("POST", "/mcp"),
        ("GET", "/mcp"),
        ("DELETE", "/mcp"),
    }


def test_openapi_documents_all_endpoints():
    """不变量：每条路由都必须有 summary，避免对外文档退化。"""
    spec = http_srv.app.openapi()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("summary"), f"{method.upper()} {path} 缺少 summary"
