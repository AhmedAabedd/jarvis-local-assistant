"""Thin wrapper over the official `ollama` Python library.

Uses streaming chat so the rest of the app can speak / print tokens as they
arrive instead of waiting for the full reply — critical for hiding the
~8-18 tok/s latency on a CPU-only box.

Thinking control is belt-and-suspenders, because some Ollama versions ignore
the `think` flag and let Qwen3 emit raw <think>…</think> into the content:
  1. We append Qwen3's own /no_think (or /think) directive to the last user
     message — the model honours this directly, version-independent.
  2. We still strip any <think>…</think> that leaks into the content stream,
     even when the tags are split across chunks.
"""

from __future__ import annotations

from typing import Iterable, Iterator

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
    """Stream assistant text chunks, with thinking suppressed by default.

    `think` resolves to config.THINK at call time when left as None, so a
    runtime toggle (e.g. the CLI's /think) takes effect.
    """
    effective_think = config.THINK if think is None else think
    msgs = _with_think_directive(messages, effective_think)
    raw = _raw_stream(msgs, model, effective_think, options, keep_alive)
    yield from _strip_think(raw)


def _raw_stream(messages, model, think, options, keep_alive) -> Iterator[str]:
    try:
        stream = _get_client().chat(
            model=model,
            messages=messages,
            stream=True,
            think=think,
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


def _with_think_directive(messages: list[dict], think: bool) -> list[dict]:
    """Append Qwen3's /think or /no_think to the last user message (copy)."""
    directive = "/think" if think else "/no_think"
    msgs = [dict(m) for m in messages]
    for msg in reversed(msgs):
        if msg.get("role") == "user":
            msg["content"] = f"{msg['content']} {directive}"
            break
    return msgs


def _strip_think(chunks: Iterable[str]) -> Iterator[str]:
    """Yield text with <think>…</think> spans removed, across chunk boundaries."""
    open_tag, close_tag = "<think>", "</think>"
    buffer = ""
    in_think = False
    for chunk in chunks:
        buffer += chunk
        out = ""
        while buffer:
            if not in_think:
                idx = buffer.find(open_tag)
                if idx == -1:
                    keep = _safe_len(buffer, open_tag)
                    out += buffer[:keep]
                    buffer = buffer[keep:]
                    break
                out += buffer[:idx]
                buffer = buffer[idx + len(open_tag):]
                in_think = True
            else:
                idx = buffer.find(close_tag)
                if idx == -1:
                    buffer = buffer[_safe_len(buffer, close_tag):]
                    break
                buffer = buffer[idx + len(close_tag):]
                in_think = False
        if out:
            yield out
    if buffer and not in_think:
        yield buffer


def _safe_len(buffer: str, marker: str) -> int:
    """Length safe to emit/drop now, holding back a possible partial `marker`."""
    for k in range(min(len(marker) - 1, len(buffer)), 0, -1):
        if marker.startswith(buffer[-k:]):
            return len(buffer) - k
    return len(buffer)
