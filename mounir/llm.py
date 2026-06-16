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
    tools: list | None = None,
    tool_calls_out: list | None = None,
) -> Iterator[str]:
    """Stream assistant text chunks. `think` defaults to config.THINK.

    If `tools` is given, the model may emit tool calls instead of text; any it
    emits are appended to `tool_calls_out` (raw ollama ToolCall objects) for the
    agent to execute. Text and tool calls are mutually exclusive per turn.
    """
    try:
        stream = ollama.chat(
            model=model,
            messages=messages,
            think=config.THINK if think is None else think,
            tools=tools,
            stream=True,
            options={"num_ctx": 32768}
        )
        for chunk in stream:
            message = chunk.message
            if message.content:
                yield message.content
            if tool_calls_out is not None and message.tool_calls:
                tool_calls_out.extend(message.tool_calls)
    except Exception as exc:
        raise OllamaError(f"Ollama call failed (model {model}): {exc}") from exc
