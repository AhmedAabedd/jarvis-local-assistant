"""Internal user-decline signal shared by dynamic agent graph layers.

The encoded value is transport for Mounir's own graph only.  Dynamic agent
models must never receive it as an ordinary tool result after a refusal.
"""

from __future__ import annotations

import json
from typing import Any

PREFIX = "__MOUNIR_USER_DECLINED_V1__:"
MESSAGE = "User declined — action cancelled. Do not retry."
MAX_RESULT_CHARS = 240
MAX_VISIBLE_ACTIONS = 8


class Signal(str):
    """Trusted in-process cancellation value; ordinary model text is never one."""


def _short(value: Any, limit: int = MAX_RESULT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def create(action: str) -> Signal:
    """Create a private signal for one action refused before execution."""
    return Signal(
        encode(
            {
                "type": "user_declined",
                "message": MESSAGE,
                "declined_action": {"agent": "", "name": _short(action, 120)},
                "completed_actions": [],
                "agent_path": [],
                "remaining_actions": "cancelled",
            }
        )
    )


def encode(outcome: dict) -> str:
    return PREFIX + json.dumps(outcome, ensure_ascii=False, separators=(",", ":"))


def parse(value: Any) -> dict | None:
    text = str(value or "")
    if not text.startswith(PREFIX):
        return None
    try:
        outcome = json.loads(text[len(PREFIX) :])
    except (TypeError, ValueError):
        return None
    if not isinstance(outcome, dict) or outcome.get("type") != "user_declined":
        return None
    return outcome


def add_agent_context(
    value: str,
    *,
    agent: str,
    completed_actions: list[dict] | None = None,
) -> Signal:
    """Prepend one dynamic-agent layer and its completed work to a signal."""
    outcome = parse(value)
    if outcome is None:
        return Signal(value)

    agent_name = _short(agent, 120)
    declined = dict(outcome.get("declined_action") or {})
    if not declined.get("agent"):
        declined["agent"] = agent_name
    outcome["declined_action"] = declined

    path = [str(item) for item in outcome.get("agent_path") or [] if str(item)]
    if not path or path[0] != agent_name:
        path.insert(0, agent_name)
    outcome["agent_path"] = path

    earlier = []
    for item in completed_actions or []:
        if not isinstance(item, dict):
            continue
        earlier.append(
            {
                "agent": _short(item.get("agent") or agent_name, 120),
                "name": _short(item.get("name"), 120),
                "result": _short(item.get("result")),
            }
        )
    existing = [
        item
        for item in outcome.get("completed_actions") or []
        if isinstance(item, dict)
    ]
    outcome["completed_actions"] = [*earlier, *existing]
    outcome["message"] = MESSAGE
    outcome["remaining_actions"] = "cancelled"
    return Signal(encode(outcome))


def from_artifact(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    outcome = value.get("mounir_user_declined")
    return outcome if isinstance(outcome, dict) else None


def artifact(outcome: dict) -> dict:
    return {"mounir_user_declined": outcome}


def user_notice(outcome: dict | None) -> str:
    """Render a deterministic response without asking another model."""
    if not outcome:
        return (
            "Okay, I didn't run it — you declined the action. "
            "Tell me how you'd like to proceed."
        )

    declined = outcome.get("declined_action") or {}
    action = _short(declined.get("name"), 120) or "the action"
    agent = _short(declined.get("agent"), 120)
    target = f"{action} in {agent}" if agent else action
    completed = [
        item
        for item in outcome.get("completed_actions") or []
        if isinstance(item, dict)
    ]
    if not completed:
        return (
            f"Okay, I didn't run {target} — you declined the action. "
            "No further actions were performed. Tell me how you'd like to proceed."
        )

    rendered = []
    for item in completed[:MAX_VISIBLE_ACTIONS]:
        name = _short(item.get("name"), 120) or "action"
        owner = _short(item.get("agent"), 120)
        result = _short(item.get("result"), 160)
        label = f"{name} in {owner}" if owner else name
        rendered.append(f"{label}: {result}" if result else label)
    if len(completed) > MAX_VISIBLE_ACTIONS:
        rendered.append(f"{len(completed) - MAX_VISIBLE_ACTIONS} more approved actions")
    return (
        f"Stopped after you declined {target}. Completed before cancellation: "
        + "; ".join(rendered)
        + ". No further actions were performed."
    )
