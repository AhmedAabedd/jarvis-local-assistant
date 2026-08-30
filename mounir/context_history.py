"""Per-conversation memory for subagents and selected built-in specialists."""

from __future__ import annotations

from typing import Any

from . import action_decline
from .memory import Conversation


HISTORY_INSTRUCTION = """\
The conversation below contains tasks delegated to you earlier in this same
Mounir conversation and the reports you returned. Use it to understand
follow-up requests and avoid needlessly repeating completed work. Past reports
are not guaranteed to describe current external state; re-check when needed.
"""

_BUILTIN_OWNERS = {"media", "knowledge", "system"}


def _owner(
    *, subagent_node_id: int | None = None, builtin_key: str | None = None
) -> tuple[str, int | str] | None:
    if subagent_node_id is not None:
        return "subagent", int(subagent_node_id)
    normalized = str(builtin_key or "").strip().lower()
    if normalized in _BUILTIN_OWNERS:
        return "builtin", normalized
    return None


class ContextHistory:
    """Own the specialist conversations belonging to one Mounir conversation."""

    def __init__(self) -> None:
        self._conversations: dict[tuple[str, int | str], Conversation] = {}

    def messages(
        self,
        *,
        subagent_node_id: int | None = None,
        builtin_key: str | None = None,
    ) -> list[dict]:
        owner = _owner(
            subagent_node_id=subagent_node_id,
            builtin_key=builtin_key,
        )
        conversation = self._conversations.get(owner) if owner is not None else None
        return conversation.to_messages() if conversation is not None else []

    def remember(
        self,
        task: str,
        report: Any,
        *,
        subagent_node_id: int | None = None,
        builtin_key: str | None = None,
    ) -> None:
        owner = _owner(
            subagent_node_id=subagent_node_id,
            builtin_key=builtin_key,
        )
        if owner is None:
            return
        conversation = self._conversations.get(owner)
        if conversation is None:
            conversation = Conversation(system_prompt=HISTORY_INSTRUCTION.strip())
            self._conversations[owner] = conversation
        outcome = action_decline.parse(report)
        rendered = action_decline.user_notice(outcome) if outcome else str(report or "")
        conversation.add_user(str(task or ""))
        conversation.add_assistant(rendered)

    def reset(self) -> None:
        """Clear every specialist conversation owned by this Mounir conversation."""
        self._conversations.clear()


def messages(
    history: ContextHistory | None,
    *,
    subagent_node_id: int | None = None,
    builtin_key: str | None = None,
) -> list[dict]:
    if history is None:
        return []
    return history.messages(
        subagent_node_id=subagent_node_id,
        builtin_key=builtin_key,
    )


def remember(
    history: ContextHistory | None,
    task: str,
    report: Any,
    *,
    subagent_node_id: int | None = None,
    builtin_key: str | None = None,
) -> None:
    if history is not None:
        history.remember(
            task,
            report,
            subagent_node_id=subagent_node_id,
            builtin_key=builtin_key,
        )
