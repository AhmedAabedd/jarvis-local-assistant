"""Shared lifecycle for Mounir's managed local GBrain MCP process.

GBrain's PGLite store must have a single process owner.  When automatic
knowledge is active, this module keeps one stdio session alive and lends a
small async proxy to every caller.  Calls are serialized inside the owning
event loop, so automatic previews, explicit Knowledge work, and connection
tests never open competing database processes.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import threading
from contextlib import asynccontextmanager
from typing import Any


def _spec_key(spec: dict) -> str:
    payload = {
        key: spec.get(key)
        for key in (
            "server_id",
            "transport",
            "connection",
            "headers",
            "env",
            "auth_scheme",
        )
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _failed_future(message: str) -> concurrent.futures.Future:
    future: concurrent.futures.Future = concurrent.futures.Future()
    future.set_exception(RuntimeError(message))
    return future


class _SessionProxy:
    """Expose the MCP methods used by Knowledge on the caller's event loop."""

    def __init__(self, runtime: "GBrainRuntime"):
        self._runtime = runtime

    async def call_tool(self, name: str, arguments: dict):
        return await _await_concurrent(
            self._runtime._submit("call_tool", name, arguments)
        )

    async def list_tools(self, *, cursor: str | None = None):
        return await _await_concurrent(
            self._runtime._submit("list_tools", cursor=cursor)
        )


async def _await_concurrent(future: concurrent.futures.Future):
    """Await a cross-loop future without blocking the caller's event loop."""
    try:
        while not future.done():
            await asyncio.sleep(0.005)
        return future.result()
    except asyncio.CancelledError:
        future.cancel()
        raise


class GBrainRuntime:
    """Own one restartable MCP session in a dedicated thread and event loop."""

    def __init__(self):
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._restart_event: asyncio.Event | None = None
        self._session: Any = None
        self._call_lock: asyncio.Lock | None = None
        self._spec: dict | None = None
        self._key = ""
        self._accepting = False
        self._reserved = False
        self._leases = 0
        self._last_error = ""
        self._first_attempt = threading.Event()
        self._stop_requested = threading.Event()

    @property
    def last_error(self) -> str:
        with self._condition:
            return self._last_error

    def is_active_for(self, spec: dict) -> bool:
        with self._condition:
            return bool(
                self._accepting
                and self._session is not None
                and self._key == _spec_key(spec)
            )

    def owns(self, spec: dict) -> bool:
        """Return whether this runtime owns the store, including restart gaps."""
        with self._condition:
            return bool(
                self._accepting
                and (
                    self._reserved
                    or (
                        self._thread is not None
                        and self._thread.is_alive()
                    )
                )
                and self._key == _spec_key(spec)
            )

    def reserve(self, spec: dict) -> None:
        """Prevent temporary owners while setup prepares the persistent store."""
        if self.owns(spec):
            return
        self.stop()
        with self._condition:
            self._spec = dict(spec)
            self._key = _spec_key(spec)
            self._accepting = True
            self._reserved = True

    def start(self, spec: dict, timeout: float = 65.0) -> bool:
        """Start or reuse the persistent process for this exact configuration."""
        key = _spec_key(spec)
        with self._condition:
            if (
                self._thread is not None
                and self._thread.is_alive()
                and self._key == key
            ):
                thread = self._thread
            else:
                thread = None
        if thread is not None:
            self._first_attempt.wait(timeout)
            return self.is_active_for(spec)

        with self._condition:
            reserved = self._reserved and self._key == key
        if not reserved:
            self.stop()
        with self._condition:
            self._spec = dict(spec)
            self._key = key
            self._accepting = True
            self._reserved = False
            self._last_error = ""
            self._first_attempt = threading.Event()
            self._stop_requested = threading.Event()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="mounir-gbrain-runtime",
                daemon=True,
            )
            self._thread.start()
        self._first_attempt.wait(timeout)
        return self.is_active_for(spec)

    def stop(self) -> None:
        """Stop accepting leases, finish current work, then close GBrain."""
        self._stop_requested.set()
        with self._condition:
            self._accepting = False
            while self._leases:
                self._condition.wait()
            loop = self._loop
            shutdown = self._shutdown_event
            thread = self._thread
        if loop is not None and shutdown is not None and loop.is_running():
            loop.call_soon_threadsafe(shutdown.set)
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        with self._condition:
            self._thread = None
            self._loop = None
            self._shutdown_event = None
            self._restart_event = None
            self._session = None
            self._call_lock = None
            self._spec = None
            self._key = ""
            self._reserved = False
            self._condition.notify_all()

    def acquire(self, spec: dict) -> _SessionProxy | None:
        """Reserve the persistent process for one complete logical operation."""
        with self._condition:
            if not (
                self._accepting
                and (
                    self._reserved
                    or (
                        self._thread is not None
                        and self._thread.is_alive()
                    )
                )
                and self._key == _spec_key(spec)
            ):
                return None
            self._leases += 1
        return _SessionProxy(self)

    def release(self) -> None:
        with self._condition:
            if self._leases:
                self._leases -= 1
            self._condition.notify_all()

    def _submit(self, method: str, *args, **kwargs) -> concurrent.futures.Future:
        with self._condition:
            loop = self._loop
            session = self._session
        if loop is None or session is None or not loop.is_running():
            return _failed_future("The persistent GBrain runtime is unavailable.")
        return asyncio.run_coroutine_threadsafe(
            self._invoke(method, *args, **kwargs), loop
        )

    async def _invoke(self, method: str, *args, **kwargs):
        lock = self._call_lock
        session = self._session
        if lock is None or session is None:
            raise RuntimeError("The persistent GBrain runtime is unavailable.")
        try:
            async with lock:
                return await getattr(session, method)(*args, **kwargs)
        except BaseException:
            restart = self._restart_event
            if restart is not None:
                restart.set()
            raise

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._worker())
        finally:
            with self._condition:
                self._accepting = False
                self._session = None
                self._loop = None
                self._call_lock = None
                self._condition.notify_all()

    async def _worker(self) -> None:
        from .specialists.mcp_agent import _mcp_session

        self._loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
        if self._stop_requested.is_set():
            self._shutdown_event.set()
        delay = 0.5
        while not self._shutdown_event.is_set():
            self._restart_event = asyncio.Event()
            try:
                async with _mcp_session(dict(self._spec or {})) as session:
                    self._call_lock = asyncio.Lock()
                    with self._condition:
                        self._session = session
                        self._last_error = ""
                        self._condition.notify_all()
                    self._first_attempt.set()
                    delay = 0.5
                    shutdown_task = asyncio.create_task(self._shutdown_event.wait())
                    restart_task = asyncio.create_task(self._restart_event.wait())
                    _, pending = await asyncio.wait(
                        {shutdown_task, restart_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    lock = self._call_lock
                    if lock is not None:
                        async with lock:
                            pass
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                with self._condition:
                    self._last_error = str(exc).strip() or type(exc).__name__
                self._first_attempt.set()
            finally:
                with self._condition:
                    self._session = None
                    self._call_lock = None
                    self._condition.notify_all()
            if self._shutdown_event.is_set():
                break
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
            except TimeoutError:
                pass
            delay = min(delay * 2, 10.0)


runtime = GBrainRuntime()


@asynccontextmanager
async def session(spec: dict, temporary_factory=None):
    """Use the persistent session when active, otherwise preserve on-demand use."""
    proxy = runtime.acquire(spec)
    if proxy is not None:
        try:
            yield proxy
        finally:
            runtime.release()
        return

    if temporary_factory is None:
        from .specialists.mcp_agent import _mcp_session

        temporary_factory = _mcp_session

    async with temporary_factory(spec) as temporary:
        yield temporary
