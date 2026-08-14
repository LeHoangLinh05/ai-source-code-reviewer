"""Persistent asyncio runtime for synchronous Celery worker tasks."""

from __future__ import annotations

import asyncio
import atexit
import sys
from collections.abc import Coroutine
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any, TypeVar

ResultT = TypeVar("ResultT")
WINDOWS_PLATFORM = "win32"
IS_WINDOWS = sys.platform == WINDOWS_PLATFORM


def create_worker_event_loop() -> asyncio.AbstractEventLoop:
    """Create an event loop compatible with async Psycopg on Windows."""

    if IS_WINDOWS:
        return asyncio.SelectorEventLoop()

    return asyncio.new_event_loop()


@dataclass(slots=True)
class WorkerAsyncRuntime:
    """Own the event loop reused by sequential Celery tasks."""

    runner: asyncio.Runner | None = None

    def run(self, coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
        """Run a coroutine with a fresh context on the persistent loop."""

        if self.runner is None:
            self.runner = asyncio.Runner(loop_factory=create_worker_event_loop)

        return self.runner.run(coroutine, context=copy_context())

    def close(self) -> None:
        """Close the persistent event loop if it was initialized."""

        if self.runner is None:
            return

        self.runner.close()
        self.runner = None


_runtime = WorkerAsyncRuntime()


def run_worker_coroutine(coroutine: Coroutine[Any, Any, ResultT]) -> ResultT:
    """Run a task without replacing the worker process event loop."""

    return _runtime.run(coroutine)


def close_worker_async_runtime() -> None:
    """Close the worker event loop during process shutdown."""

    _runtime.close()


atexit.register(close_worker_async_runtime)
