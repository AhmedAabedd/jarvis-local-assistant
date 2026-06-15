"""Thin client over the Ollama HTTP API.

Uses /api/chat with streaming so the rest of the app can speak / print tokens
as they arrive instead of waiting for the full reply — critical for hiding the
~8-18 tok/s latency on a CPU-only box.
"""

from __future__ import annotations

from typing import Iterator

import requests

from . import config


class OllamaError(RuntimeError):
    """Raised when Ollama is unreachable or returns an error."""


def is_up(host: str = config.OLLAMA_HOST, timeout: float = 2.0) -> bool:
    """Quick health check so the CLI can fail with a friendly message."""
    try:
        requests.get(f"{host}/api/tags", timeout=timeout)
        return True
    except requests.RequestException:
        return False


def chat_stream(
    messages: list[dict],
    *,
    model: str = config.MODEL,
    host: str = config.OLLAMA_HOST,
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
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": config.THINK if think is None else think,
        "keep_alive": keep_alive,
        "options": options if options is not None else config.OPTIONS,
    }

    try:
        with requests.post(
            f"{host}/api/chat",
            json=payload,
            stream=True,
            timeout=(5, 300),
        ) as resp:
            if resp.status_code != 200:
                raise OllamaError(
                    f"Ollama returned {resp.status_code}: {resp.text[:200]}"
                )
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = _parse_line(line)
                if chunk is None:
                    continue
                # Qwen3 thinking tokens come back under "thinking"; we ignore
                # them for output but they still cost time when think=True.
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
                if chunk.get("done"):
                    break
    except requests.RequestException as exc:
        raise OllamaError(f"Could not reach Ollama at {host}: {exc}") from exc


def _parse_line(line: bytes) -> dict | None:
    import json

    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None
