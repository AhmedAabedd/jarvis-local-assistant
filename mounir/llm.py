from __future__ import annotations
from typing import Iterator
import ollama
from . import config

class OllamaError(RuntimeError):
    pass

def is_up() -> bool:
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
    if config.USE_GROQ:
        yield from _groq_stream(messages, tools=tools, tool_calls_out=tool_calls_out)
    else:
        yield from _ollama_stream(messages, model=model, think=think, tools=tools, tool_calls_out=tool_calls_out)

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

        # Groq is OpenAI-compatible — messages format is basically identical
        # to what you already build, just strip non-standard keys.
        clean_messages = []
        for m in messages:
            entry = {"role": m["role"], "content": m.get("content") or ""}
            if m["role"] == "tool":
                entry["role"] = "tool"
                entry["name"] = m.get("tool_name", "")
            if m.get("tool_calls"):
                entry["tool_calls"] = [
                    {
                        "id": f"call_{i}",
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
            kwargs["tools"] = tools  # same JSON schema shape as Ollama — no conversion needed

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