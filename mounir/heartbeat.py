"""Safe, lifecycle-managed scheduled tasks for the web application.

Mounir receives each saved task through an isolated supervisor graph containing
only its selected agents. Every specialist receives only the explicitly chosen,
non-confirmation tools. Quiet checks are persisted but not delivered; alerts are
handed to the application through a callback.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from . import db, trace
from .langgraph_agent import Agent
from .memory import Conversation

QUIET_TOKEN = "HEARTBEAT_OK"
HEARTBEAT_AGENT_PROMPT = """\
AUTOMATED HEARTBEAT MODE
Report complete findings from this scheduled check to your parent agent. Never
return HEARTBEAT_OK; only the supervisor decides whether the result is quiet.
"""
HEARTBEAT_SUPERVISOR_PROMPT = """\
You supervise a scheduled heartbeat task running without the user present.

Use only the delegation tools available for this run. Delegate each check to
the appropriate specialist and treat its report as evidence. Do not claim an
outcome that a specialist did not confirm.

Compare the findings with the previous delivered result. Return one complete
notification only when something new or meaningfully changed needs the user's
attention. Otherwise, return exactly HEARTBEAT_OK.

Only you may return HEARTBEAT_OK. Specialists must report their findings.
"""


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_quiet(report: str) -> bool:
    return report.strip().rstrip(".!").upper() == QUIET_TOKEN


def run_task(task_id: int) -> tuple[str, str]:
    """Let Mounir orchestrate one saved task through its scoped safe agents."""
    task = db.get_heartbeat_task(task_id)
    if task is None:
        raise ValueError("heartbeat task not found")
    targets = db.get_heartbeat_targets(task_id)
    if not targets:
        return "skipped", "No approved agent tools are available for this task."
    for target in targets:
        target["task_prompt"] = HEARTBEAT_AGENT_PROMPT.strip()
        if target.get("kind") == "mcp":
            target["prompt"] = "\n\n".join(
                part
                for part in (
                    str(target.get("prompt") or "").strip(),
                    HEARTBEAT_AGENT_PROMPT.strip(),
                )
                if part
            )

    previous = db.get_heartbeat_task_agent_report(task_id, "supervisor")
    prompt = f"""SCHEDULED TASK
Name: {task['name']}
Instructions: {task['instructions']}

PREVIOUS DELIVERED RESULT
{previous.strip() or '(none)'}"""
    conversation = Conversation(
        system_prompt=HEARTBEAT_SUPERVISOR_PROMPT.strip()
    )
    mounir = Agent(conversation=conversation, scoped_targets=targets)
    # Exhaust the stream so the complete supervisor/delegation graph runs.
    list(mounir.respond(prompt))
    visible = conversation.display_messages()
    report = next(
        (
            item["content"].strip()
            for item in reversed(visible)
            if item["role"] == "assistant" and item["content"].strip()
        ),
        "",
    )
    if not report or _is_quiet(report):
        db.set_heartbeat_task_agent_report(task_id, "supervisor", "")
        return "quiet", ""
    if report == previous:
        return "quiet", ""
    db.set_heartbeat_task_agent_report(task_id, "supervisor", report)
    return "alert", report


class HeartbeatService:
    """One cooperative scheduler owned by the FastAPI application lifespan."""

    def __init__(
        self,
        notify: Callable[..., Awaitable[None]],
        *,
        runner: Callable[[int], tuple[str, str]] | None = None,
    ) -> None:
        self._notify = notify
        self._runner = runner
        try:
            self._notify_accepts_task = len(inspect.signature(notify).parameters) >= 2
        except (TypeError, ValueError):
            self._notify_accepts_task = False
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._run_lock: asyncio.Lock | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        db.recover_interrupted_heartbeat_runs()
        self._wake = asyncio.Event()
        self._run_lock = asyncio.Lock()
        self._task = asyncio.create_task(self._loop(), name="mounir-heartbeat")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def run_now(self, task_id: int, trigger: str = "manual") -> dict:
        if self._run_lock is None:
            self._run_lock = asyncio.Lock()
        if self._run_lock.locked():
            raise RuntimeError("A heartbeat check is already running.")
        async with self._run_lock:
            task = db.get_heartbeat_task(task_id)
            if task is None:
                raise ValueError("heartbeat task not found")
            if trigger == "scheduled" and not task["enabled"]:
                return task
            run_id = db.begin_heartbeat_task_run(task_id, trigger)
            runner = (
                (lambda: self._runner(task_id))
                if self._runner is not None
                else (lambda: run_task(task_id))
            )
            try:
                status, message = await asyncio.to_thread(runner)
            except Exception as exc:
                trace.event(f"heartbeat failed: {exc}")
                return db.finish_heartbeat_task_run(
                    task_id, run_id, status="error", error=str(exc)
                )
            state = db.finish_heartbeat_task_run(
                task_id, run_id, status=status, message=message
            )
            if status == "alert" and message:
                if self._notify_accepts_task:
                    await self._notify(message, state)
                else:
                    await self._notify(message)
            return state

    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            tasks = db.list_heartbeat_tasks()
            now = datetime.now(timezone.utc)
            due_tasks = [
                task
                for task in tasks
                if task["enabled"]
                and (
                    _parse_time(task.get("next_run_at")) is None
                    or _parse_time(task.get("next_run_at")) <= now
                )
            ]
            if due_tasks:
                for task in due_tasks:
                    try:
                        await self.run_now(task["id"], "scheduled")
                    except (RuntimeError, ValueError):
                        pass
                continue

            due_times = [
                parsed
                for task in tasks
                if task["enabled"]
                for parsed in [_parse_time(task.get("next_run_at"))]
                if parsed is not None
            ]
            timeout = 60.0
            if due_times:
                timeout = max(
                    0.25, min(60.0, (min(due_times) - now).total_seconds())
                )
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=timeout)
            except TimeoutError:
                pass
