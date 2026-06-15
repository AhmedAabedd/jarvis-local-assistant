"""Thin wrapper over the official `ollama` Python library.

Streaming chat so the app can print / speak tokens as they arrive — key to
hiding CPU latency. Thinking is disabled in the Modelfile template, so there's
nothing special to do here.
"""

from __future__ import annotations

from typing import Iterator

from . import config

_client = None  # cached ollama.Client


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


def _get_client():
    global _client
    if _client is None:
        from ollama import Client

        _client = Client(host=config.OLLAMA_HOST)
    return _client


def is_up() -> bool:
    """Quick health check so the CLI can fail with a friendly message."""
    try:
        _get_client().list()
        return True
    except Exception:
        return False


def chat_stream(
    messages: list[dict],
    *,
    model: str = config.MODEL,
    think: bool | None = None,
    options: dict | None = None,
    keep_alive: str = config.KEEP_ALIVE,
) -> Iterator[str]:
    """Stream assistant text chunks for a list of chat messages.

    `think` resolves to config.THINK at call time when left as None.
    """
    try:
        stream = _get_client().chat(
            model=model,
            messages=messages,
            stream=True,
            think=config.THINK if think is None else think,
            options=options if options is not None else config.OPTIONS,
            keep_alive=keep_alive,
        )
        for chunk in stream:
            content = chunk.message.content
            if content:
                yield content
    except Exception as exc:
        raise OllamaError(
            f"Ollama call failed (host {config.OLLAMA_HOST}, model {model}): {exc}"
        ) from exc
