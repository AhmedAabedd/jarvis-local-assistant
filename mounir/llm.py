"""Thin wrapper over the official `ollama` Python library.

Uses streaming chat so the rest of the app can speak / print tokens as they
arrive instead of waiting for the full reply — critical for hiding the
~8-18 tok/s latency on a CPU-only box. The library is the same path the
standalone test scripts use, where `think=False` is confirmed to actually
disable Qwen3's thinking.
"""

from __future__ import annotations

from typing import Iterator

from ollama import Client

from . import config

_client: Client | None = None


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


def _get_client() -> Client:
    global _client
    if _client is None:
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

    `messages` is the standard [{"role": ..., "content": ...}] list.
    Yields content fragments as they arrive. Raises OllamaError on failure.
    `think` resolves to config.THINK at call time when left as None, so a
    runtime toggle (e.g. the CLI's /think) actually takes effect.
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
            # Qwen3 thinking tokens come back under message.thinking; we only
            # surface message.content so thinking is never printed/spoken.
            content = chunk.message.content
            if content:
                yield content
    except Exception as exc:
        raise OllamaError(
            f"Ollama call failed (host {config.OLLAMA_HOST}, model {model}): {exc}"
        ) from exc
