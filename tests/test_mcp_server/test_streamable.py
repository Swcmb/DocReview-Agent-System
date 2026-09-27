"""MCP Streamable HTTP 传输（`/mcp`）的行为测试。

覆盖规范要求的会话生命周期、SSE 通道与传输层错误语义。
与 `test_contract.py` 分工：那里锁「对外长什么样」，这里验「怎么用」。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.mcp_server import core, streamable
from src.mcp_server import server as http_srv
from tests.test_mcp_server.test_contract import _install_stubs

INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": core.PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    _install_stubs(monkeypatch)
    streamable.reset_sessions()
    with TestClient(http_srv.app) as c:
        yield c
    streamable.reset_sessions()


def _init(client: TestClient) -> str:
    r = client.post("/mcp", json=INIT_BODY)
    assert r.status_code == 200, r.text
    return r.headers[streamable.SESSION_HEADER]


# ─────────────────────── 会话握手 ───────────────────────


def test_initialize_returns_session_header(client: TestClient) -> None:
    """`initialize` 必须回传 `Mcp-Session-Id`，否则客户端无法开始会话。"""
    r = client.post("/mcp", json=INIT_BODY)
    assert r.status_code == 200
    assert streamable.SESSION_HEADER in r.headers
    assert r.json()["result"]["protocolVersion"] == core.PROTOCOL_VERSION
    assert streamable.active_session_count() == 1


def test_two_initializes_get_distinct_sessions(client: TestClient) -> None:
    """不同客户端不得共用会话 ID。"""
    a = _init(client)
    b = _init(client)
    assert a != b
    assert streamable.active_session_count() == 2


# ─────────────────────── 会话校验 ───────────────────────


def test_request_without_session_is_rejected(client: TestClient) -> None:
    """未 initialize 就发业务请求 → 400 problem+json。"""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["status"] == 400


def test_request_with_unknown_session_is_rejected(client: TestClient) -> None:
    """伪造/失效会话 ID → 404。"""
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={streamable.SESSION_HEADER: "deadbeef"},
    )
    assert r.status_code == 404


def test_valid_session_allows_business_call(client: TestClient) -> None:
    """带有效会话的标准调用应返回 MCP 规范形态。"""
    sid = _init(client)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "health_check", "arguments": {}},
        },
        headers={streamable.SESSION_HEADER: sid},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["metadata"]["status"] == "healthy"
    assert body["result"]["content"][0]["type"] == "text"


def test_legacy_methods_work_over_mcp_endpoint(client: TestClient) -> None:
    """`/mcp` 与 `/` 共用同一套分发：旧方法名在此同样可用。"""
    sid = _init(client)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 4, "method": "list_tools"},
        headers={streamable.SESSION_HEADER: sid},
    )
    assert r.status_code == 200
    assert "parameters" in r.json()["result"]["tools"][0]


# ─────────────────────── 会话终结 ───────────────────────


def test_delete_terminates_session(client: TestClient) -> None:
    """DELETE 后同一会话不可再用。"""
    sid = _init(client)
    assert client.delete("/mcp", headers={streamable.SESSION_HEADER: sid}).status_code == 204
    assert streamable.active_session_count() == 0

    again = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        headers={streamable.SESSION_HEADER: sid},
    )
    assert again.status_code == 404


def test_delete_unknown_session_is_404(client: TestClient) -> None:
    r = client.delete("/mcp", headers={streamable.SESSION_HEADER: "nope"})
    assert r.status_code == 404


def test_delete_without_session_is_400(client: TestClient) -> None:
    assert client.delete("/mcp").status_code == 400


# ─────────────────────── SSE 通道 ───────────────────────


def test_get_mcp_streams_sse(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """`GET /mcp` 返回 text/event-stream，并以保活注释开头。"""
    monkeypatch.setattr(streamable, "SSE_KEEPALIVE_SECONDS", 0.01)
    monkeypatch.setattr(streamable, "MAX_SSE_SECONDS", 0.05)
    sid = _init(client)

    with client.stream("GET", "/mcp", headers={streamable.SESSION_HEADER: sid}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        chunks = []
        for line in r.iter_lines():
            if line.startswith(":"):
                chunks.append(line)
            if len(chunks) >= 2:
                break
    assert any("stream open" in c for c in chunks)
    assert any("keepalive" in c for c in chunks)


def test_get_mcp_requires_session(client: TestClient) -> None:
    assert client.get("/mcp").status_code == 400


def test_get_mcp_rejects_unknown_session(client: TestClient) -> None:
    r = client.get("/mcp", headers={streamable.SESSION_HEADER: "nope"})
    assert r.status_code == 404


# ─────────────────────── 会话过期清理 ───────────────────────


def test_expired_sessions_are_pruned() -> None:
    """超龄会话被清理，避免长跑进程内存无界增长。"""
    streamable.reset_sessions()
    streamable._sessions["old"] = {"created_at": 0.0, "protocol_version": core.PROTOCOL_VERSION}
    streamable._prune_expired(now=streamable.MAX_SSE_SECONDS + 1)
    assert streamable.active_session_count() == 0


def test_fresh_sessions_survive_prune() -> None:
    streamable.reset_sessions()
    streamable._sessions["new"] = {"created_at": 0.0, "protocol_version": core.PROTOCOL_VERSION}
    streamable._prune_expired(now=1.0)
    assert streamable.active_session_count() == 1


# ─────────────────────── 与旧端点的隔离 ───────────────────────


def test_root_endpoint_needs_no_session(client: TestClient) -> None:
    """`POST /` 保持无会话的旧行为，不受 Streamable HTTP 约束影响。"""
    r = client.post("/", json={"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
    assert r.status_code == 200
    assert streamable.active_session_count() == 0


def test_protocol_version_header_is_accepted(client: TestClient) -> None:
    """客户端回传协议版本头时不得报错（协商由 dispatch 内的 initialize 决定）。"""
    sid = _init(client)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
        headers={
            streamable.SESSION_HEADER: sid,
            streamable.PROTOCOL_HEADER: core.PROTOCOL_VERSION,
        },
    )
    assert r.status_code == 200


def test_malformed_json_returns_problem(client: TestClient) -> None:
    """非法 JSON 体不得冒泡成 500。"""
    r = client.post(
        "/mcp",
        content=b"{not json",
        headers={"content-type": "application/json", streamable.SESSION_HEADER: _init(client)},
    )
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")


def test_problem_body_shape(client: TestClient) -> None:
    """problem+json 必须是 RFC 9457 形状，且不含 JSON-RPC error 字段。"""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 8, "method": "tools/list"})
    body = json.loads(r.text)
    assert set(body) == {"type", "title", "status", "detail"}
    assert "error" not in body
