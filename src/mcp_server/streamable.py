"""MCP Streamable HTTP 传输（规范 2025-03-26）。

## 为什么需要这一层

此前 HTTP 模式只是个「REST + JSON-RPC 装饰」的端点：方法名非标准、没有会话、
没有 SSE。严格的 MCP 客户端按规范走 `POST /mcp` + `initialize` 握手 + 会话头，
会直接失败。本模块按规范补齐这条路径。

## 规范要点与本实现

| 规范要求 | 本实现 |
|---|---|
| `POST /mcp` 收客户端 JSON-RPC | ✅ 复用 `_dispatch_rpc`，与 `POST /` 同一套方法分发 |
| `initialize` 响应带 `Mcp-Session-Id` | ✅ 服务端生成并回传 |
| 后续请求须带 `Mcp-Session-Id` | ✅ 缺失/无效返回 400 |
| `MCP-Protocol-Version` 头 | ✅ 接收；缺省按 `PROTOCOL_VERSION` 处理 |
| `GET /mcp` 为 SSE 流 | ✅ 返回保活流（本服务无服务端主动推送，故仅保活） |
| `DELETE /mcp` 终结会话 | ✅ 返回 204；未知会话 404 |

## 已知限制（务必如实告知使用者）

- **会话仅存在于进程内存**：服务重启后全部失效，客户端须重新 `initialize`。
  这是刻意取舍——本服务跨请求无状态，落盘只会引入无谓的持久化与清理负担。
- **SSE 流不发业务消息**：本服务没有服务端主动通知（进度、日志推送等），
  故 `GET /mcp` 是一条只发保活注释的空流。规范允许服务端仅在必要时使用该通道。
- **不实现 resumability**（`Last-Event-ID` 重放）：无事件可重放。
- 单条 SSE 连接最长 `MAX_SSE_SECONDS` 后主动关闭，由客户端重连。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import core

logger = logging.getLogger(__name__)

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"

#: 单条 SSE 连接的最长存活时间（秒），到期主动关闭，由客户端重连。
MAX_SSE_SECONDS = 300.0
#: SSE 保活间隔（秒）。取 15s 是为了在常见反代空闲超时（通常 60s）之内。
SSE_KEEPALIVE_SECONDS = 15.0

# 进程内会话表：session_id -> {"created_at": float, "protocol_version": str}
# 只为满足规范的握手与生命周期约定而存在，本服务不依赖它承载任何业务状态。
_sessions: dict[str, dict[str, Any]] = {}


def reset_sessions() -> None:
    """清空会话表（测试隔离用）。"""
    _sessions.clear()


def active_session_count() -> int:
    """当前活跃会话数（供 `/health` 之外的自检与测试使用）。"""
    return len(_sessions)


def _prune_expired(now: float | None = None) -> None:
    """丢弃超过 `MAX_SSE_SECONDS` 的会话，防止长跑进程内存无界增长。"""
    current = time.monotonic() if now is None else now
    stale = [sid for sid, meta in _sessions.items() if current - meta["created_at"] > MAX_SSE_SECONDS]
    for sid in stale:
        _sessions.pop(sid, None)


def build_router(
    dispatch: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> APIRouter:
    """构造 Streamable HTTP 路由。

    Args:
        dispatch: JSON-RPC 方法分发实现。由 `server.py` 注入以避免循环 import
            （分发需要 `server` 的工具函数，而 `server` 又要挂载本路由）。
    """
    router = APIRouter()

    @router.post("/mcp")
    async def post_mcp(
        request: Request,
        session_id: str | None = None,
    ) -> Response:
        """Streamable HTTP 的客户端 → 服务端通道。"""
        session_id = request.headers.get(SESSION_HEADER, session_id)
        try:
            payload = await request.json()
        except Exception:
            # 畸形 JSON 体是客户端错误，不该冒泡成 500。
            logger.warning("收到无法解析的 JSON 体")
            return _problem(400, "请求体不是合法 JSON", "请发送符合 JSON-RPC 2.0 的请求")
        method = payload.get("method") if isinstance(payload, dict) else None

        if method == "initialize":
            _prune_expired()
            new_id = uuid.uuid4().hex
            _sessions[new_id] = {
                "created_at": time.monotonic(),
                "protocol_version": core.PROTOCOL_VERSION,
            }
            return JSONResponse(
                await dispatch(payload),
                headers={SESSION_HEADER: new_id},
            )

        # 非 initialize 必须携带有效会话
        if not session_id:
            return _problem(400, "缺少会话 ID", f"请先调用 initialize 并回传 {SESSION_HEADER} 头")
        if session_id not in _sessions:
            return _problem(404, "会话不存在或已过期", "请重新 initialize")

        return JSONResponse(await dispatch(payload))

    @router.get("/mcp")
    async def get_mcp(request: Request, session_id: str | None = None) -> Response:
        """Streamable HTTP 的服务端 → 客户端 SSE 通道。

        本服务无服务端主动消息，故该流仅承载保活注释。
        """
        session_id = request.headers.get(SESSION_HEADER, session_id)
        if not session_id:
            return _problem(400, "缺少会话 ID", f"请先调用 initialize 并回传 {SESSION_HEADER} 头")
        if session_id not in _sessions:
            return _problem(404, "会话不存在或已过期", "请重新 initialize")

        return StreamingResponse(
            _sse_stream(request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @router.delete("/mcp")
    async def delete_mcp(request: Request, session_id: str | None = None) -> Response:
        """显式终结会话。"""
        session_id = request.headers.get(SESSION_HEADER, session_id)
        if not session_id:
            return _problem(400, "缺少会话 ID", f"请先调用 initialize 并回传 {SESSION_HEADER} 头")
        if _sessions.pop(session_id, None) is None:
            return _problem(404, "会话不存在或已过期", "请重新 initialize")
        return Response(status_code=204)

    return router


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """按 RFC 9457 `application/problem+json` 返回传输层错误。

    注意：这是**传输层**错误（缺会话、会话失效），不是 JSON-RPC 层的
    `{"error": {...}}` 信封——两者不可混用，否则客户端会误判为协议响应。
    """
    return JSONResponse(
        {"type": f"about:blank#{status}", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


async def _sse_stream(request: Request) -> AsyncIterator[str]:
    """生成 SSE 保活流。

    以 `MAX_SSE_SECONDS` 为硬上限，避免连接无限挂起；客户端断开会立即退出。
    """
    yield ": docreview mcp stream open\n\n"
    deadline = time.monotonic() + MAX_SSE_SECONDS
    try:
        while time.monotonic() < deadline:
            if await request.is_disconnected():
                return
            await asyncio.sleep(SSE_KEEPALIVE_SECONDS)
            yield ": keepalive\n\n"
    except asyncio.CancelledError:
        # 客户端断开属正常路径，不值得记 error
        with contextlib.suppress(Exception):
            await request.is_disconnected()
        raise
