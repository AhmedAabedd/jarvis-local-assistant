from __future__ import annotations
import time
from typing import Iterator
import ollama
from . import config

class OllamaError(RuntimeError):
    pass

def active_model(default: str) -> str:
    """The model actually used, given the active provider (for display)."""
    if config.USE_MISTRAL:
        return config.MISTRAL_MODEL
    if config.USE_GROQ:
        return config.GROQ_MODEL
    return default

def nvidia_chat(messages, tools=None, model=None, *, disable_thinking=False,
                temperature=0.2, max_tokens=8192) -> dict:
    """One non-streaming NVIDIA chat-completion (OpenAI-compatible).

    Returns the assistant message dict ({content, tool_calls}). Used by the
    coder and researcher specialists, each with their own model.
    """
    import requests

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.95,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    if disable_thinking:  # only for reasoning models (e.g. minimax)
        payload["chat_template_kwargs"] = {"thinking_mode": "disabled"}
    headers = {
        "Authorization": f"Bearer {config.NVIDIA_API_KEY}",
        "Accept": "application/json",
    }
    resp = requests.post(
        f"{config.NVIDIA_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]

def gemini_chat(messages, tools=None, model=None, *, temperature=0.2,
                max_tokens=8192) -> dict:
    """One non-streaming Gemini chat-completion via Google's OpenAI-compatible
    endpoint — same message/tool format as nvidia_chat, no extra SDK needed.

    Returns the assistant message dict ({content, tool_calls}). Used by the
    librarian specialist.
    """
    import requests

    payload = {
        "model": model or config.GEMINI_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    headers = {
        "Authorization": f"Bearer {config.GEMINI_API_KEY}",
        "Accept": "application/json",
    }
    # Gemini flash returns transient 429/5xx (overload) fairly often — retry a
    # couple of times with a short backoff before giving up.
    for attempt in range(3):
        resp = requests.post(
            f"{config.GEMINI_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

def is_up() -> bool:
    if config.USE_MISTRAL:
        return bool(config.MISTRAL_API_KEY)
    if config.USE_GROQ:
        return bool(config.GROQ_API_KEY)
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
    if config.USE_MISTRAL:
        yield from _mistral_stream(messages, tools=tools, tool_calls_out=tool_calls_out)
    elif config.USE_GROQ:
        yield from _groq_stream(messages, tools=tools, tool_calls_out=tool_calls_out)
    else:
        yield from _ollama_stream(messages, model=model, think=think, tools=tools, tool_calls_out=tool_calls_out)

def _mistral_stream(messages, *, tools, tool_calls_out) -> Iterator[str]:
    try:
        from mistralai.client import Mistral

        client = Mistral(api_key=config.MISTRAL_API_KEY)

        # Clean up messages: Mistral expects tool messages with specific shape
        clean_messages = []
        for m in messages:
            if m["role"] == "tool":
                clean_messages.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", "call_0"),
                    "content": m.get("content") or "",
                })
            elif m.get("tool_calls"):
                clean_messages.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": [
                        {
                            "id": f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": _to_json_str(tc["function"]["arguments"]),
                            },
                        }
                        for i, tc in enumerate(m["tool_calls"])
                    ],
                })
            else:
                clean_messages.append({"role": m["role"], "content": m.get("content") or ""})

        kwargs = dict(
            model=config.MISTRAL_MODEL,
            messages=clean_messages,
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools

        stream = client.chat.stream(**kwargs)

        collected_calls = {}
        for event in stream:
            delta = event.data.choices[0].delta

            if delta.content:
                yield delta.content

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index if hasattr(tc, 'index') else 0
                    if idx not in collected_calls:
                        collected_calls[idx] = {"id": tc.id or f"call_{idx}", "name": "", "arguments": ""}
                    if tc.function.name:
                        collected_calls[idx]["name"] += tc.function.name
                    if tc.function.arguments:
                        collected_calls[idx]["arguments"] += tc.function.arguments

        if tool_calls_out is not None and collected_calls:
            import json
            for call in collected_calls.values():
                tool_calls_out.append(_wrap_tool_call(call["name"], json.loads(call["arguments"])))

    except Exception as exc:
        raise OllamaError(f"Mistral call failed: {exc}") from exc

def _wrap_tool_call(name: str, arguments: dict):
    class _Fn:
        pass
    fn = _Fn()
    fn.name = name
    fn.arguments = arguments
    class _TC:
        pass
    tc = _TC()
    tc.function = fn
    return tc

def _ollama_stream(messages, *, model, think, tools, tool_calls_out) -> Iterator[str]:
    try:
        kwargs = dict(model=model, messages=messages, stream=True, options={"num_ctx": 32768},
                      think=config.THINK if think is None else think)
        if tools:
            kwargs["tools"] = tools
        stream = ollama.chat(**kwargs)
        for chunk in stream:
            message = chunk.message
            if message.content:
                yield message.content
            if tool_calls_out is not None and message.tool_calls:
                tool_calls_out.extend(message.tool_calls)
    except Exception as exc:
        raise OllamaError(f"Ollama call failed (model {model}): {exc}") from exc

def _groq_stream(messages, *, tools, tool_calls_out) -> Iterator[str]:
    try:
        from groq import Groq

        client = Groq(api_key=config.GROQ_API_KEY)

        clean_messages = []
        last_tool_call_ids: list[str] = []  # ids generated for the most recent assistant tool_calls

        for m in messages:
            if m["role"] == "tool":
                # Pop ids in order — matches the order tools were dispatched in agent.py
                tool_call_id = last_tool_call_ids.pop(0) if last_tool_call_ids else "call_0"
                clean_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": m.get("tool_name", ""),
                    "content": m.get("content") or "",
                })
                continue

            entry = {"role": m["role"], "content": m.get("content") or ""}

            if m.get("tool_calls"):
                ids = [f"call_{i}" for i in range(len(m["tool_calls"]))]
                last_tool_call_ids = ids.copy()
                entry["tool_calls"] = [
                    {
                        "id": ids[i],
                        "type": "function",
                        "function": {
                            "name": tc["function"]["name"],
                            "arguments": _to_json_str(tc["function"]["arguments"]),
                        },
                    }
                    for i, tc in enumerate(m["tool_calls"])
                ]

            clean_messages.append(entry)

        kwargs = dict(
            model=config.GROQ_MODEL,
            messages=clean_messages,
            stream=True,
        )
        if tools:
            kwargs["tools"] = tools

        stream = client.chat.completions.create(**kwargs)

        collected_calls = {}
        for chunk in stream:
            delta = chunk.choices[0].delta

            if delta.content:
                yield delta.content

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in collected_calls:
                        collected_calls[idx] = {"name": "", "arguments": ""}
                    if tc.function.name:
                        collected_calls[idx]["name"] += tc.function.name
                    if tc.function.arguments:
                        collected_calls[idx]["arguments"] += tc.function.arguments

        if tool_calls_out is not None and collected_calls:
            import json
            for call in collected_calls.values():
                tool_calls_out.append(_wrap_groq_tool_call(call["name"], json.loads(call["arguments"])))

    except Exception as exc:
        raise OllamaError(f"Groq call failed: {exc}") from exc

def _to_json_str(args) -> str:
    import json
    if isinstance(args, str):
        return args
    return json.dumps(args)

def _wrap_groq_tool_call(name: str, arguments: dict):
    """Wrap a Groq tool call into the same shape agent.py expects."""
    class _Fn:
        pass
    fn = _Fn()
    fn.name = name
    fn.arguments = arguments
    class _TC:
        pass
    tc = _TC()
    tc.function = fn
    return tc