"""Structured outcomes shared by every Mounir tool-execution path.

LangChain's ``ToolMessage`` remains the provider-facing contract: the model sees
the tool result in ``content`` and the matching ``tool_call_id``.  Mounir keeps
the richer execution state in the message artifact so orchestration can
distinguish a completed call from an error, refusal, or intentional skip without
parsing human-readable result text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import ToolMessage

Status = Literal["success", "error", "declined", "skipped"]
ARTIFACT_KEY = "mounir_tool_outcome"
ORIGINAL_ARTIFACT_KEY = "tool_artifact"


@dataclass(frozen=True)
class ToolOutcome:
    """One tool result with machine-readable execution state."""

    content: Any
    status: Status = "success"
    artifact: Any = None

    @classmethod
    def success(cls, content: Any, artifact: Any = None) -> "ToolOutcome":
        return cls(content=content, status="success", artifact=artifact)

    @classmethod
    def error(cls, content: Any, artifact: Any = None) -> "ToolOutcome":
        return cls(content=content, status="error", artifact=artifact)

    @classmethod
    def declined(cls, content: Any, artifact: Any = None) -> "ToolOutcome":
        return cls(content=content, status="declined", artifact=artifact)

    @classmethod
    def skipped(cls, content: Any, artifact: Any = None) -> "ToolOutcome":
        return cls(content=content, status="skipped", artifact=artifact)

    def _artifact(self) -> dict:
        if isinstance(self.artifact, dict):
            payload = dict(self.artifact)
        elif self.artifact is None:
            payload = {}
        else:
            payload = {ORIGINAL_ARTIFACT_KEY: self.artifact}
        payload[ARTIFACT_KEY] = {"status": self.status}
        return payload

    def as_tool_response(self) -> tuple[Any, dict]:
        """Return LangChain's standard ``content_and_artifact`` shape."""

        return self.content, self._artifact()

    def as_tool_message(
        self,
        *,
        name: str,
        tool_call_id: str,
    ) -> ToolMessage:
        return ToolMessage(
            content=self.content,
            name=name,
            tool_call_id=tool_call_id,
            artifact=self._artifact(),
            status="error" if self.status == "error" else "success",
        )


def status(message: ToolMessage) -> Status:
    """Read a Mounir outcome status, falling back to ToolMessage's status."""

    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict):
        outcome = artifact.get(ARTIFACT_KEY)
        if isinstance(outcome, dict) and outcome.get("status") in {
            "success",
            "error",
            "declined",
            "skipped",
        }:
            return outcome["status"]
    return "error" if getattr(message, "status", "success") == "error" else "success"


def normalize(
    message: ToolMessage,
    *,
    outcome_status: Status | None = None,
) -> ToolMessage:
    """Attach the structured outcome contract to a ToolNode result."""

    selected = outcome_status or status(message)
    normalized = ToolOutcome(
        content=message.content,
        status=selected,
        artifact=message.artifact,
    ).as_tool_message(
        name=message.name or "tool",
        tool_call_id=message.tool_call_id,
    )
    return message.model_copy(
        update={
            "artifact": normalized.artifact,
            "status": normalized.status,
        }
    )
