from __future__ import annotations
from typing import Iterator
import ollama
from . import config

class OllamaError(RuntimeError):
    pass

def is_up() -> bool:
    if config.USE_GEMINI:
        return bool(config.GEMINI_API_KEY)
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
    if config.USE_GEMINI:
        yield from _gemini_stream(messages, tools=tools, tool_calls_out=tool_calls_out)
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

def _gemini_stream(messages, *, tools, tool_calls_out) -> Iterator[str]:
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.GEMINI_API_KEY)

        # Convert messages to Gemini format
        system_prompt = None
        history = []
        for m in messages:
            if m["role"] == "system":
                system_prompt = (system_prompt or "") + "\n" + m["content"]
            elif m["role"] == "user":
                history.append(types.Content(role="user", parts=[types.Part(text=m["content"])]))
            elif m["role"] == "assistant":
                history.append(types.Content(role="model", parts=[types.Part(text=m.get("content") or "")]))

        # Last message is the user turn
        last = history.pop() if history else None
        if last is None:
            return

        config_kwargs = {}
        if system_prompt:
            config_kwargs["system_instruction"] = system_prompt.strip()
        if tools:
            config_kwargs["tools"] = _convert_tools(tools)

        response = client.models.generate_content_stream(
            model=config.GEMINI_MODEL,
            contents=history + [last],
            config=types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
        )

        for chunk in response:
            # Handle tool calls — guard against chunks with no candidates,
            # or a candidate whose content/parts is None (happens on some
            # streaming chunks, e.g. the final one).
            if tool_calls_out is not None and chunk.candidates:
                content = chunk.candidates[0].content
                parts = content.parts if content is not None else None
                if parts:
                    for part in parts:
                        if hasattr(part, "function_call") and part.function_call:
                            tool_calls_out.append(_wrap_gemini_tool_call(part.function_call))
            # Handle text
            if chunk.text:
                yield chunk.text

    except Exception as exc:
        raise OllamaError(f"Gemini call failed: {exc}") from exc

def _convert_tools(schemas: list) -> list:
    """Convert Ollama-style tool schemas to Gemini format."""
    from google.genai import types
    gemini_tools = []
    for s in schemas:
        fn = s["function"]
        gemini_tools.append(types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name=fn["name"],
                description=fn["description"],
                parameters=fn.get("parameters"),
            )
        ]))
    return gemini_tools

def _wrap_gemini_tool_call(fc):
    """Wrap a Gemini function_call into the same shape agent.py expects."""
    class _Fn:
        name = fc.name
        arguments = dict(fc.args)
    class _TC:
        function = _Fn()
    return _TC()