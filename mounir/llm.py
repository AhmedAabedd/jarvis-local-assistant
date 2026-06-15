"""Streaming chat over the official `ollama` library.

Calls ollama.chat() directly (same as the standalone test scripts). Streaming
lets the app print / speak tokens as they arrive. Thinking is controlled by the
`think` flag — the 8B model's stock template honours it.
"""

from __future__ import annotations

from typing import Iterator

import ollama

from . import config


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or the chat call fails."""


def is_up() -> bool:
    """Quick health check so the CLI can fail with a friendly message."""
    try:
        ollama.list()
        return True
    except Exception:
        return False


def chat_stream(
    messages: list[dict],
    *,
    model: str = config.MODEL,
    think: bool | None = None,
) -> Iterator[str]:
    """Stream assistant text chunks. `think` defaults to config.THINK."""
    try:
        stream = ollama.chat(
            model=model,
            messages=messages,
            think=config.THINK if think is None else think,
            stream=True,
        )
        for chunk in stream:
            content = chunk.message.content
            if content:
                yield content
    except Exception as exc:
        raise OllamaError(f"Ollama call failed (model {model}): {exc}") from exc
