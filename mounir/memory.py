"""Conversation memory: in-RAM history plus optional JSON persistence.

Stage 1 keeps this deliberately simple — a rolling window of recent turns.
Smarter long-term memory (summaries, retrieval) is a Stage 4 concern; the
interface here is built so that can drop in without touching the agent.
"""

from __future__ import annotations

import json
import time
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
        self._messages.append({"role": "user", "content": content})

    def add_assistant(self, content: str) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_message(self, message: dict) -> None:
        """Append a raw message dict (used for tool calls and tool results)."""
        self._messages.append(message)

    def reset(self) -> None:
        self._messages.clear()

    # --- access -------------------------------------------------------------

    def to_messages(self) -> list[dict]:
        """Build the message list sent to Ollama: system prompt + window."""
        window = self._messages[-self.max_messages :]
        if self.system_prompt:
            return [{"role": "system", "content": self.system_prompt}, *window]
        return list(window)

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


def _default_path() -> Path:
    return config.DATA_DIR / "last_conversation.json"
