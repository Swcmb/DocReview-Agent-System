"""§12.1 懒加载与释放：engine 单例、generation token、reset/aclose。

生命周期契约（§12.1）：

- 模块级 provider 用 :class:`threading.RLock` 保护；健康实例只创建一次；构造阶段
  **不 preload**（权重加载推迟到第一次真正使用时，且经 :func:`asyncio.to_thread`）。
- 同步 Router/Agent **构造、推理、释放**都经 :func:`asyncio.to_thread`，默认超时
  ``timeout_seconds=120``；超时后**不再使用后台结果、不重试**，本进程保持 Null。
- :func:`reset_decision_engine` 只清引用、缓存、降级标记和 generation token，
  **不做阻塞 unload**（同步、立即返回）。
- :func:`aclose_decision_engine` 先在锁内**递增 generation token**、取出并清空
  provider 引用，再经 :func:`asyncio.to_thread` 释放 Router/Agent 并做必要 GC；
  **旧 generation 的迟到结果必须丢弃**（:func:`generation_is_current`）。

导入纪律（§12.1 末条）：本模块不得 import ``laya`` / ``torch`` / ``transformers``。
Null 引擎与真实引擎都**惰性**导入，绝不在模块顶层触发 Laya 导入链。
"""

from __future__ import annotations

import asyncio
import gc
import threading
from collections.abc import Callable
from typing import Any, Final

from src.decisions.types import (
    DEGRADATION_DISABLED,
    DEGRADATION_UNAVAILABLE,
    DecisionEngine,
)

__all__ = [
    "EngineHandle",
    "configure_provider",
    "get_decision_engine",
    "current_generation",
    "generation_is_current",
    "mark_degraded",
    "reset_decision_engine",
    "aclose_decision_engine",
    "await_thread",
    "DEFAULT_TIMEOUT_SECONDS",
]

#: §12.1 默认单次推理/构造/释放超时。
DEFAULT_TIMEOUT_SECONDS: Final[int] = 120

# provider 工厂：接受 timeout，返回一个 DecisionEngine。惰性注入，便于测试用 fake。
_ProviderFactory = Callable[..., Any]

_lock = threading.RLock()
_generation = 0
_engine: DecisionEngine | None = None
_provider_factory: _ProviderFactory | None = None
_degraded_reason: str | None = None


class EngineHandle:
    """一次 engine 借用的快照：engine + 它所属的 generation。

    调用方在返回结果前必须用 :func:`generation_is_current` 复核——若期间发生过
    reset/aclose，本次结果属于**旧 generation**，必须丢弃（§12.1）。
    """

    __slots__ = ("engine", "generation", "degraded_reason")

    def __init__(
        self,
        engine: DecisionEngine | None,
        generation: int,
        degraded_reason: str | None,
    ) -> None:
        self.engine = engine
        self.generation = generation
        self.degraded_reason = degraded_reason

    @property
    def is_degraded(self) -> bool:
        return self.engine is None


# ---------------------------------------------------------------------------
# 注入与获取
# ---------------------------------------------------------------------------
def configure_provider(factory: _ProviderFactory | None) -> None:
    """注入/清除 provider 工厂。**不触发构造**（构造阶段不 preload）。"""
    global _provider_factory
    with _lock:
        _provider_factory = factory


def mark_degraded(reason: str) -> None:
    """标记本进程保持 Null（disabled / unavailable），并作废当前 generation。"""
    global _degraded_reason, _generation, _engine
    with _lock:
        _degraded_reason = reason
        _engine = None
        _generation += 1


def current_generation() -> int:
    with _lock:
        return _generation


def generation_is_current(generation: int) -> bool:
    """复核 generation；False 表示 reset/aclose 已发生，迟到结果必须丢弃。"""
    with _lock:
        return generation == _generation


def get_decision_engine(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> EngineHandle:
    """获取（或首次构造）健康 engine。构造失败/已降级时返回 degraded handle（engine=None）。

    构造**只发生一次**且在锁内同步完成（避免重复加载权重）；重量级同步构造调用方
    应经 :func:`await_thread` 包装本函数，或直接用 :func:`aget_decision_engine`。
    """
    global _engine, _degraded_reason
    with _lock:
        if _degraded_reason is not None:
            return EngineHandle(None, _generation, _degraded_reason)
        if _engine is not None:
            return EngineHandle(_engine, _generation, None)
        factory = _provider_factory
        if factory is None:
            _degraded_reason = DEGRADATION_DISABLED
            return EngineHandle(None, _generation, _degraded_reason)
        try:
            _engine = factory(timeout_seconds=timeout_seconds)
        except Exception:  # noqa: BLE001 - 构造失败一律降级为 Null，不向上冒泡
            _engine = None
            _degraded_reason = DEGRADATION_UNAVAILABLE
            return EngineHandle(None, _generation, _degraded_reason)
        return EngineHandle(_engine, _generation, None)


# ---------------------------------------------------------------------------
# §12.1 释放
# ---------------------------------------------------------------------------
def reset_decision_engine() -> None:
    """**非阻塞** reset：只清引用、缓存、降级标记和 generation token。

    不等待、不 unload Router/Agent（那可能耗时数十秒）。真正资源清理由 runtime 的
    :func:`aclose_decision_engine` 异步接口等待完成（§12.1）。
    """
    global _engine, _degraded_reason, _generation
    with _lock:
        _engine = None
        _degraded_reason = None
        _generation += 1


async def aclose_decision_engine() -> None:
    """异步 cleanup：锁内递增 generation 并取出引用，再经 ``asyncio.to_thread`` 释放。"""
    global _engine, _generation
    with _lock:
        _generation += 1  # 先作废旧 generation，迟到结果随之失效
        engine, _engine = _engine, None
    if engine is None:
        gc.collect()
        return
    await await_thread(_release_engine, engine, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)
    gc.collect()


def _release_engine(engine: Any) -> None:
    """在后台线程释放 engine 持有的 Router/Agent。"""
    for attr in ("aclose", "close", "release"):
        fn = getattr(engine, attr, None)
        if callable(fn):
            fn()
            return


# ---------------------------------------------------------------------------
# 同步 → 异步桥（§12.1：构造/推理/释放都经 to_thread + 超时）
# ---------------------------------------------------------------------------
async def await_thread(
    fn: Callable[..., Any],
    *args: Any,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    **kwargs: Any,
) -> Any:
    """在 ``asyncio.to_thread`` 中跑同步 ``fn``，超时抛 :class:`TimeoutError`。

    超时后**不再使用后台结果、不重试**（§12.1）——底层线程可能仍在跑，但其结果被丢弃。
    """
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, lambda: fn(*args, **kwargs))
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=timeout_seconds)
    except TimeoutError:
        future.cancel()
        raise
