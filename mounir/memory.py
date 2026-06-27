"""Conversation memory: in-RAM history plus optional JSON persistence.

Stage 1 keeps this deliberately simple — a rolling window of recent turns.
Smarter long-term memory (summaries, retrieval) is a Stage 4 concern; the
interface here is built so that can drop in without touching the agent.
"""

from __future__ import annotations

import json
import time
import datetime
from pathlib import Path

from . import config


class Conversation:
    def __init__(
        self,
        system_prompt: str | None = None,
        max_messages: int = config.MAX_HISTORY_MESSAGES,
    ) -> None:
        self.system_prompt = system_prompt
        self.max_messages = max_messages
        self._messages: list[dict] = []

    # --- mutation -----------------------------------------------------------

    def add_user(self, content: str) -> None:
        now = datetime.datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")
        stamped = f"[{now}]\n{content}"
        self._messages.append({"role": "user", "content": stamped})

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_message(self, message: dict) -> None:
        """Append a raw message dict (used for tool calls and tool results)."""
        self._messages.append(message)

    def reset(self) -> None:
        self._messages.clear()

    # --- access -------------------------------------------------------------

    def to_messages(self) -> list[dict]:
        """Build the message list sent to Ollama: system prompt + OS context + window."""
        window = _ensure_tool_call_pairs(self._messages[-self.max_messages :])
        base = []
        if self.system_prompt:
            base.append({"role": "system", "content": self.system_prompt})
        # Machine facts as a system message: authoritative context the model
        # should rely on, not a fake assistant turn (which would also make the
        # `mounir` build — system_prompt=None — start on an assistant message).
        base.append({"role": "system", "content": config.CONTEXT_MESSAGE})
        return base + window

    def __len__(self) -> int:
        return len(self._messages)

    # --- persistence --------------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        path = path or _default_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "saved_at": time.time(),
            "system_prompt": self.system_prompt,
            "messages": self._messages,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return path

    def load(self, path: Path | None = None) -> bool:
        path = path or _default_path()
        if not path.exists():
            return False
        data = json.loads(path.read_text())
        self._messages = data.get("messages", [])
        if data.get("system_prompt"):
            self.system_prompt = data["system_prompt"]
        return True


def _ensure_tool_call_pairs(window: list[dict]) -> list[dict]:
    """Keep every tool result paired with the assistant turn that called it.

    The history now stores the full turn — assistant tool-call messages and their
    tool results. Trimming to the window can drop the assistant message while
    keeping its result, leaving an orphan the chat API rejects ("tool result with
    no preceding tool call"). For each orphan (most often a delegated specialist's
    report whose delegate call got trimmed), synthesize the missing assistant
    chat-completion that 'called' the tool, so the pair is whole again.
    """
    declared: set[str] = set()
    out: list[dict] = []
    for m in window:
        role = m.get("role")
        if role == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("id"):
                    declared.add(tc["id"])
            out.append(m)
        elif role == "tool":
            tid = m.get("tool_call_id", "call_0")
            if tid not in declared:
                out.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": tid,
                            "function": {
                                "name": m.get("tool_name", "tool"),
                                "arguments": {},
                            },
                        }
                    ],
                })
                declared.add(tid)
            out.append(m)
        else:
            out.append(m)
    return out


def _default_path() -> Path:
    return config.DATA_DIR / "last_conversation.json"
