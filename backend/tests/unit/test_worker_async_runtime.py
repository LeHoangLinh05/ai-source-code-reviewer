"""Tests for the persistent Celery worker asyncio runtime."""

import asyncio

from app.workers import async_runtime
from app.workers.async_runtime import (
    WorkerAsyncRuntime,
    close_worker_async_runtime,
    run_worker_coroutine,
)


async def _running_loop() -> asyncio.AbstractEventLoop:
    return asyncio.get_running_loop()


def test_worker_runtime_reuses_event_loop_between_tasks() -> None:
    close_worker_async_runtime()
    try:
        first_loop = run_worker_coroutine(_running_loop())
        second_loop = run_worker_coroutine(_running_loop())
    finally:
        close_worker_async_runtime()

    assert first_loop is second_loop
    assert first_loop.is_closed()


def test_worker_runtime_creates_new_loop_after_shutdown() -> None:
    close_worker_async_runtime()
    first_loop = run_worker_coroutine(_running_loop())
    close_worker_async_runtime()

    try:
        second_loop = run_worker_coroutine(_running_loop())
    finally:
        close_worker_async_runtime()

    assert first_loop is not second_loop
    assert first_loop.is_closed()
    assert second_loop.is_closed()


def test_worker_runtime_uses_selector_loop_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(async_runtime, "IS_WINDOWS", True)
    runtime = WorkerAsyncRuntime()

    try:
        loop = runtime.run(_running_loop())
    finally:
        runtime.close()

    assert isinstance(loop, asyncio.SelectorEventLoop)
    assert loop.is_closed()
