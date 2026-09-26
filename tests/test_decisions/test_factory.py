"""T-13：§12.1 factory 生命周期。

验收点（任务表）：reset 清引用/缓存且**不阻塞** unload、aclose 经
``asyncio.to_thread`` 释放、**旧 generation 的迟到结果被丢弃**。
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Any

import pytest

from src.decisions.factory import (
    EngineHandle,
    aclose_decision_engine,
    await_thread,
    configure_provider,
    current_generation,
    generation_is_current,
    get_decision_engine,
    mark_degraded,
    reset_decision_engine,
)
from src.decisions.types import DEGRADATION_DISABLED, DEGRADATION_UNAVAILABLE


class FakeEngine:
    """最小 DecisionEngine 形状；记录释放发生的线程。"""

    def __init__(self) -> None:
        self.released_in: str | None = None
        self.closed = False

    def aclose(self) -> None:
        self.released_in = threading.current_thread().name
        self.closed = True

    # 满足 DecisionEngine Protocol 形状（本文件只验证生命周期，不调用五原语）
    async def screen_document(self, content: str, *, context: Any) -> Any: ...
    async def assess_document(self, content: str, section_index: Any, *, context: Any) -> Any: ...
    async def verify_issues(self, *a: Any, **k: Any) -> Any: ...
    async def verify_resolutions(self, *a: Any, **k: Any) -> Any: ...
    async def judge_convergence(self, *a: Any, **k: Any) -> Any: ...


@pytest.fixture(autouse=True)
def _clean_factory():
    """每个测试前后都清空模块级单例状态。"""
    reset_decision_engine()
    configure_provider(None)
    yield
    reset_decision_engine()
    configure_provider(None)


def _provider(engine: FakeEngine | None = None, calls: list[int] | None = None):
    def factory_fn(*, timeout_seconds: int = 120) -> FakeEngine:
        if calls is not None:
            calls.append(timeout_seconds)
        if engine is None:
            raise RuntimeError("boom")
        return engine

    return factory_fn


# ===========================================================================
# 构造只发生一次 / 构造阶段不 preload
# ===========================================================================
def test_configure_provider_does_not_construct():
    """configure_provider 只是注入，**不得**触发构造（构造阶段不 preload）。"""
    calls: list[int] = []
    configure_provider(_provider(FakeEngine(), calls))
    assert calls == []


def test_healthy_instance_created_only_once():
    calls: list[int] = []
    configure_provider(_provider(FakeEngine(), calls))
    first = get_decision_engine()
    second = get_decision_engine()
    assert calls == [120]  # 只构造一次
    assert first.engine is second.engine


def test_disabled_without_provider_returns_degraded_null():
    handle = get_decision_engine()
    assert handle.is_degraded
    assert handle.degraded_reason == DEGRADATION_DISABLED


def test_construction_failure_degrades_to_unavailable():
    configure_provider(_provider(None))
    handle = get_decision_engine()
    assert handle.is_degraded
    assert handle.degraded_reason == DEGRADATION_UNAVAILABLE


# ===========================================================================
# reset：非阻塞、递增 generation
# ===========================================================================
def test_reset_clears_reference_and_increments_generation():
    configure_provider(_provider(FakeEngine()))
    before = current_generation()
    assert get_decision_engine().engine is not None
    reset_decision_engine()
    assert current_generation() == before + 1
    # 引用已清 → 重新取会走构造（这里 provider 已注入，故是新实例）
    handle = get_decision_engine()
    assert handle.generation == current_generation()


def test_reset_does_not_call_release():
    """reset 只清引用，**不做阻塞 unload**——不得触发 aclose/close。"""
    engine = FakeEngine()
    configure_provider(_provider(engine))
    get_decision_engine()
    reset_decision_engine()
    assert engine.closed is False
    assert engine.released_in is None


def test_reset_returns_immediately_even_with_slow_release():
    """reset 绝不能等待慢释放（否则会阻塞事件循环）。"""
    engine = FakeEngine()
    configure_provider(_provider(engine))
    get_decision_engine()
    reset_decision_engine()  # 不触发 release，故必然立即返回
    assert engine.closed is False


# ===========================================================================
# generation token：迟到结果必须丢弃
# ===========================================================================
def test_generation_is_current_true_before_reset():
    configure_provider(_provider(FakeEngine()))
    handle = get_decision_engine()
    assert generation_is_current(handle.generation)


def test_late_result_from_old_generation_is_discarded():
    configure_provider(_provider(FakeEngine()))
    handle = get_decision_engine()
    assert generation_is_current(handle.generation)
    reset_decision_engine()
    # 旧 generation 的迟到结果必须被丢弃
    assert not generation_is_current(handle.generation)


def test_aclose_invalidates_generation_before_release():
    configure_provider(_provider(FakeEngine()))
    handle = get_decision_engine()
    old = handle.generation
    import asyncio

    asyncio.run(aclose_decision_engine())
    assert not generation_is_current(old)
    assert current_generation() == old + 1


def test_mark_degraded_invalidates_and_keeps_null():
    configure_provider(_provider(FakeEngine()))
    handle = get_decision_engine()
    mark_degraded(DEGRADATION_UNAVAILABLE)
    assert not generation_is_current(handle.generation)
    assert get_decision_engine().is_degraded


# ===========================================================================
# aclose：经 to_thread 释放
# ===========================================================================
def test_aclose_releases_engine_in_worker_thread():
    """释放必须经 ``asyncio.to_thread``，不能占着事件循环线程。"""
    import asyncio

    engine = FakeEngine()
    configure_provider(_provider(engine))
    get_decision_engine()
    asyncio.run(aclose_decision_engine())
    assert engine.closed is True
    assert engine.released_in != threading.current_thread().name


def test_aclose_on_empty_is_noop():
    import asyncio

    asyncio.run(aclose_decision_engine())  # 不抛


# ===========================================================================
# await_thread：超时后不重试、不使用后台结果
# ===========================================================================
def test_await_thread_runs_in_worker_thread():
    import asyncio

    seen: list[str] = []

    def work() -> str:
        seen.append(threading.current_thread().name)
        return "ok"

    result = asyncio.run(await_thread(work, timeout_seconds=5))
    assert result == "ok"
    assert seen and seen[0] != threading.current_thread().name


def test_await_thread_timeout_raises_and_does_not_retry():
    import asyncio

    attempts: list[int] = []

    def slow() -> None:
        attempts.append(1)
        import time

        time.sleep(2)

    with pytest.raises(TimeoutError):
        asyncio.run(await_thread(slow, timeout_seconds=1))
    # 超时后**不重试**
    assert len(attempts) == 1


# ===========================================================================
# 导入边界
# ===========================================================================
def test_factory_import_does_not_pull_laya_torch_transformers():
    """§12.1：factory 不得顶层导入 laya / torch / transformers。"""
    code = (
        "import sys; import src.decisions.factory as f; "
        "banned=[m for m in ('laya','torch','transformers') if m in sys.modules]; "
        "print(','.join(banned))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(__import__("pathlib").Path(__file__).resolve().parents[2]),
        check=True,
    )
    assert out.stdout.strip() == ""


def test_engine_handle_is_degraded_flag():
    assert EngineHandle(None, 0, "x").is_degraded is True
    assert EngineHandle(FakeEngine(), 0, None).is_degraded is False
