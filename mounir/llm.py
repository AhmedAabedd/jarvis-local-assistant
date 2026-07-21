from __future__ import annotations
import re
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
                temperature=0.2, max_tokens=8192, base_url=None,
                api_key=None) -> dict:
    """One non-streaming NVIDIA chat-completion (OpenAI-compatible).

    Returns the assistant message dict ({content, tool_calls}). Used by fixed
    NVIDIA-backed specialists with their own models.
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
        "Authorization": f"Bearer {config.NVIDIA_API_KEY if api_key is None else api_key}",
        "Accept": "application/json",
    }
    resp = requests.post(
        f"{(base_url or config.NVIDIA_BASE_URL).rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]

def gemini_chat(messages, tools=None, model=None, *, temperature=0.2,
                max_tokens=8192, base_url=None, api_key=None) -> dict:
    """One non-streaming Gemini chat-completion via Google's OpenAI-compatible
    endpoint — same message/tool format as nvidia_chat, no extra SDK needed.

    Returns the assistant message dict ({content, tool_calls}). Used by the
    knowledge agent specialist.
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
        "Authorization": f"Bearer {config.GEMINI_API_KEY if api_key is None else api_key}",
        "Accept": "application/json",
    }
    # Gemini flash returns transient 429/5xx (overload) fairly often — retry a
    # couple of times with a short backoff before giving up.
    for attempt in range(3):
        resp = requests.post(
            f"{(base_url or config.GEMINI_BASE_URL).rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt < 2:
                # Gemini's 429 body says exactly how long the per-minute quota
                # needs ("Please retry in 7.08s") — honor it when present,
                # otherwise fall back to a generous wait.
                hinted = re.search(r"retry in ([\d.]+)\s*s", resp.text or "")
                if resp.status_code == 429:
                    wait = min(float(hinted.group(1)) + 1, 45) if hinted else 15 * (attempt + 1)
                else:
                    wait = 2 * (attempt + 1)
                time.sleep(wait)
                continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]

def openai_chat(messages, tools=None, model=None, *, base_url, api_key,
                temperature=0.2, max_tokens=8192) -> dict:
    """One non-streaming chat-completion against ANY OpenAI-compatible endpoint
    — the caller picks the model, base URL, and key. Same message/tool shape as
    nvidia_chat. Used by dynamic MCP subagents, whose provider is per-agent
    config rather than one of the fixed providers above.
    """
    import requests

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    headers = {"Accept": "application/json"}
    # Local OpenAI-compatible endpoints often require no authentication.
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers=headers,
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    # Reasoning models may think inline — keep only the answer.
    msg["content"] = re.sub(r"(?s)<think>.*?</think>", "", msg.get("content") or "").strip()
    return msg


def ollama_cloud_chat(messages, tools=None, model=None, *, temperature=0.2,
                      max_tokens=8192) -> dict:
    """One non-streaming Ollama Cloud chat-completion (ollama.com, hosted —
    not the local daemon). OpenAI-compatible, same message/tool format as
    nvidia_chat.

    Returns the assistant message dict ({content, tool_calls}). Kept as a
    provider-specific helper; dynamic MCP agents use ``openai_chat``.
    """
    import requests

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    headers = {
        "Authorization": f"Bearer {config.OLLAMA_API_KEY}",
        "Accept": "application/json",
    }
    resp = requests.post(
        f"{config.OLLAMA_CLOUD_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    # Reasoning models may think inline — keep only the answer.
    msg["content"] = re.sub(r"(?s)<think>.*?</think>", "", msg.get("content") or "").strip()
    return msg


def groq_chat(messages, tools=None, model=None, *, temperature=0.2,
              max_tokens=4096, reasoning_effort=None) -> dict:
    """One non-streaming Groq chat-completion, OpenAI message/tool format.

    Returns the assistant message dict ({content, tool_calls}) shaped exactly
    like gemini_chat's, so specialists can swap providers with one line. Qwen3
    models think out loud in <think> tags — stripped here so callers only see
    the answer.
    """
    from groq import Groq

    client = Groq(api_key=config.GROQ_API_KEY)
    kwargs = dict(
        model=model or config.GROQ_MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if tools:
        kwargs["tools"] = tools
    if reasoning_effort:  # e.g. "none" — qwen3 answers ~10x faster without thinking
        kwargs["reasoning_effort"] = reasoning_effort
    msg = client.chat.completions.create(**kwargs).choices[0].message
    content = re.sub(r"(?s)<think>.*?</think>", "", msg.content or "").strip()
    tool_calls = [
        # "type" is required when this message is sent back in the history.
        {"id": tc.id, "type": "function",
         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
        for tc in (msg.tool_calls or [])
    ]
    return {"content": content, "tool_calls": tool_calls}


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
    provider: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
) -> Iterator[str]:
    selected_provider = str(provider or "").strip().lower()
    if not selected_provider:
        selected_provider = (
            "mistral" if config.USE_MISTRAL
            else "groq" if config.USE_GROQ
            else "ollama"
        )
    if "mistral" in selected_provider:
        yield from _mistral_stream(
            messages, tools=tools, tool_calls_out=tool_calls_out,
            model=model, base_url=base_url, api_key=api_key,
        )
    elif "groq" in selected_provider:
        yield from _groq_stream(
            messages, tools=tools, tool_calls_out=tool_calls_out,
            model=model, base_url=base_url, api_key=api_key,
        )
    elif "ollama" in selected_provider:
        yield from _ollama_stream(
            messages, model=model, think=think, tools=tools,
            tool_calls_out=tool_calls_out, base_url=base_url, api_key=api_key,
        )
    else:
        raise OllamaError(
            f"Unsupported supervisor model provider: {provider or 'unknown'}"
        )

def _mistral_stream(
    messages, *, tools, tool_calls_out, model=None, base_url=None, api_key=None
) -> Iterator[str]:
    try:
        from mistralai.client import Mistral

        # Hard timeout: without it a throttled/stalled Mistral request hangs
        # the whole turn forever with no error surfacing anywhere.
        client = Mistral(
            api_key=config.MISTRAL_API_KEY if api_key is None else api_key,
            server_url=base_url or None,
            timeout_ms=120_000,
        )

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
            model=model or config.MISTRAL_MODEL,
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

def _ollama_stream(
    messages, *, model, think, tools, tool_calls_out, base_url=None, api_key=None
) -> Iterator[str]:
    try:
        kwargs = dict(model=model, messages=messages, stream=True, options={"num_ctx": 32768},
                      think=config.THINK if think is None else think)
        if tools:
            kwargs["tools"] = tools
        client_options = {}
        if base_url:
            client_options["host"] = base_url.removesuffix("/v1")
        if api_key:
            client_options["headers"] = {"Authorization": f"Bearer {api_key}"}
        client = ollama.Client(**client_options) if client_options else ollama
        stream = client.chat(**kwargs)
        for chunk in stream:
            message = chunk.message
            if message.content:
                yield message.content
            if tool_calls_out is not None and message.tool_calls:
                tool_calls_out.extend(message.tool_calls)
    except Exception as exc:
        raise OllamaError(f"Ollama call failed (model {model}): {exc}") from exc

def _groq_stream(
    messages, *, tools, tool_calls_out, model=None, base_url=None, api_key=None
) -> Iterator[str]:
    try:
        from groq import Groq

        client = Groq(
            api_key=config.GROQ_API_KEY if api_key is None else api_key,
            base_url=base_url or None,
        )

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
            model=model or config.GROQ_MODEL,
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
