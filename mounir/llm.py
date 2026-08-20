from __future__ import annotations

import json
import re
import time
from typing import Iterator

import ollama
import requests

from . import config


class OllamaError(RuntimeError):
    """Backward-compatible name for an error raised by the LLM transport."""


def active_model(default: str) -> str:
    """Return the legacy environment-selected model name."""
    if config.USE_MISTRAL:
        return config.MISTRAL_MODEL
    if config.USE_GROQ:
        return config.GROQ_MODEL
    return default


def is_up() -> bool:
    """Check the legacy local-provider configuration."""
    if config.USE_MISTRAL:
        return bool(config.MISTRAL_API_KEY)
    if config.USE_GROQ:
        return bool(config.GROQ_API_KEY)
    try:
        ollama.list()
        return True
    except Exception:
        return False


def _chat_completions_url(base_url: str | None) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        raise OllamaError(
            "This model needs an OpenAI-compatible base URL, for example "
            "https://provider.example/v1."
        )
    if not value.startswith(("http://", "https://")):
        raise OllamaError("The model base URL must start with http:// or https://.")
    if value.endswith("/chat/completions"):
        return value
    return f"{value}/chat/completions"


def _json_arguments(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {})


def _compatible_content(content, provider: str | None):
    """Adapt common multimodal blocks only where a provider's wire shape differs."""
    if not isinstance(content, list) or str(provider or "").casefold() != "mistral":
        return content
    normalized = []
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "image_url":
            normalized.append(part)
            continue
        image_url = part.get("image_url")
        if isinstance(image_url, dict) and image_url.get("url"):
            normalized.append({**part, "image_url": image_url["url"]})
        else:
            normalized.append(part)
    return normalized


def _portable_tool_content(content) -> tuple[object, list[dict]]:
    """Split visual output from a tool's portable textual response.

    The common chat-completions contract represents tool results as text, while
    image inputs belong to a multimodal user message.  LangChain can preserve
    image blocks inside ``ToolMessage`` objects, so move those blocks at the
    transport boundary instead of making individual tools provider-aware.
    """
    if not isinstance(content, list):
        return content, []

    text_parts: list[str] = []
    images: list[dict] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "image_url":
            images.append(part)
            continue
        if isinstance(part, dict) and part.get("type") == "text":
            text = str(part.get("text") or "").strip()
            if text:
                text_parts.append(text)
            continue
        if isinstance(part, str) and part.strip():
            text_parts.append(part.strip())

    if not images:
        return content, []
    return "\n".join(text_parts) or "Tool completed and returned visual media.", images


def _compatible_messages(
    messages: list[dict], provider: str | None = None
) -> list[dict]:
    """Keep history within the common OpenAI chat-completions contract."""
    normalized: list[dict] = []
    pending_visuals: list[dict] = []

    def flush_visuals() -> None:
        if not pending_visuals:
            return
        normalized.append(
            {
                "role": "user",
                "content": _compatible_content(
                    [
                        {
                            "type": "text",
                            "text": (
                                "Visual media returned by the preceding tool "
                                "results. Use it together with those results."
                            ),
                        },
                        *pending_visuals,
                    ],
                    provider,
                ),
            }
        )
        pending_visuals.clear()

    for message in messages:
        role = str(message.get("role") or "user")
        if role != "tool":
            flush_visuals()
        content = message.get("content") or ""
        if role == "tool":
            content, images = _portable_tool_content(content)
            pending_visuals.extend(images)
        entry: dict = {
            "role": role,
            "content": _compatible_content(content, provider),
        }
        if message.get("name"):
            entry["name"] = message["name"]
        if role == "tool" and message.get("tool_call_id"):
            entry["tool_call_id"] = message["tool_call_id"]
        if message.get("tool_calls"):
            entry["tool_calls"] = [
                {
                    "id": call.get("id") or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": call.get("function", {}).get("name") or "",
                        "arguments": _json_arguments(
                            call.get("function", {}).get("arguments")
                        ),
                    },
                }
                for index, call in enumerate(message["tool_calls"])
            ]
        normalized.append(entry)
    flush_visuals()
    return normalized


def _payload(
    messages: list[dict],
    *,
    model: str,
    tools: list | None,
    stream: bool,
    temperature: float | None,
    max_tokens: int,
    provider: str | None = None,
) -> dict:
    selected_model = str(model or "").strip()
    if not selected_model:
        raise OllamaError("A model ID is required.")
    body = {
        "model": selected_model,
        "messages": _compatible_messages(messages, provider),
        "stream": stream,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = tools
    return body


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _error_detail(response: requests.Response) -> str:
    try:
        body = response.json()
        error = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(error, dict):
            return str(error.get("message") or error.get("detail") or error)[:800]
        return str(error)[:800]
    except (ValueError, TypeError):
        return (response.text or response.reason or "request failed").strip()[:800]


def _is_retryable_response(response: requests.Response, detail: str = "") -> bool:
    """Recognize both standard and provider-specific transient failures.

    NVIDIA's hosted NIM gateway reports an unhealthy model deployment as HTTP
    400 with ``DEGRADED function cannot be invoked``.  Although the status code
    normally means a bad request, that particular response is infrastructure
    health state and is safe to retry just like a 5xx response.
    """
    if response.status_code == 429 or response.status_code >= 500:
        return True
    return (
        response.status_code == 400
        and "degraded function cannot be invoked" in detail.lower()
    )


def _endpoint_error(status_code: int, detail: str) -> OllamaError:
    if "degraded function cannot be invoked" in detail.lower():
        return OllamaError(
            "The provider's model deployment is temporarily degraded and could "
            "not be invoked after retries. Try again shortly or select another "
            f"model for this agent (HTTP {status_code}: {detail})."
        )
    return OllamaError(
        f"OpenAI-compatible endpoint returned HTTP {status_code}: {detail}"
    )


def _post(
    *,
    base_url: str | None,
    api_key: str | None,
    payload: dict,
    stream: bool,
    timeout: int,
) -> requests.Response:
    """POST with small, bounded retries for provider throttling/outages."""
    url = _chat_completions_url(base_url)
    response: requests.Response | None = None
    detail = ""
    for attempt in range(3):
        response = requests.post(
            url,
            headers=_headers(api_key),
            json=payload,
            stream=stream,
            timeout=timeout,
        )
        detail = _error_detail(response) if response.status_code >= 400 else ""
        if not _is_retryable_response(response, detail):
            break
        if attempt < 2:
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(max(float(retry_after), 0.25), 15.0)
            except ValueError:
                delay = float(2 ** attempt)
            response.close()
            time.sleep(delay)
    assert response is not None
    if response.status_code >= 400:
        response.close()
        raise _endpoint_error(response.status_code, detail)
    return response


def openai_chat(
    messages,
    tools=None,
    model=None,
    *,
    base_url,
    api_key="",
    provider=None,
    temperature: float | None = None,
    max_tokens=8192,
) -> dict:
    """Call any OpenAI-compatible chat-completions endpoint."""
    try:
        response = _post(
            base_url=base_url,
            api_key=api_key,
            payload=_payload(
                messages,
                model=model,
                tools=tools,
                stream=False,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            ),
            stream=False,
            timeout=180,
        )
        try:
            raw = response.json()
        finally:
            response.close()
        message = dict(raw["choices"][0]["message"])
        message["content"] = re.sub(
            r"(?s)<think>.*?</think>", "", message.get("content") or ""
        ).strip()
        return message
    except OllamaError:
        raise
    except Exception as exc:
        label = f"{provider}/" if provider else ""
        raise OllamaError(f"Model call failed ({label}{model}): {exc}") from exc


def _canonical_tool_call(
    name: str, arguments: dict, call_id: str | None = None
) -> dict:
    return {
        "id": call_id or "call_0",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


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
    """Stream any model exposing OpenAI-compatible chat completions."""
    del think  # Kept in the public signature for existing callers.
    response: requests.Response | None = None
    try:
        response = _post(
            base_url=base_url,
            api_key=api_key,
            payload=_payload(
                messages,
                model=model,
                tools=tools,
                stream=True,
                temperature=None,
                max_tokens=8192,
                provider=provider,
            ),
            stream=True,
            timeout=180,
        )
        collected_calls: dict[int, dict[str, str]] = {}
        for raw_line in response.iter_lines(decode_unicode=True):
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace").strip()
            else:
                line = str(raw_line or "").strip()
            if not line or line.startswith(("event:", ":")):
                continue
            data = line[5:].strip() if line.startswith("data:") else line
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content") or ""
            if isinstance(content, str) and content:
                yield content
            for position, tool_call in enumerate(delta.get("tool_calls") or []):
                index = int(tool_call.get("index", position) or 0)
                current = collected_calls.setdefault(
                    index,
                    {"id": f"call_{index}", "name": "", "arguments": ""},
                )
                if tool_call.get("id"):
                    current["id"] = str(tool_call["id"])
                function = tool_call.get("function") or {}
                if function.get("name"):
                    current["name"] += str(function["name"])
                if function.get("arguments"):
                    current["arguments"] += _json_arguments(function["arguments"])
        if tool_calls_out is not None:
            for call in collected_calls.values():
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except (TypeError, ValueError):
                    arguments = {}
                tool_calls_out.append(
                    _canonical_tool_call(call["name"], arguments, call["id"])
                )
    except OllamaError:
        raise
    except Exception as exc:
        label = f"{provider}/" if provider else ""
        raise OllamaError(f"Model stream failed ({label}{model}): {exc}") from exc
    finally:
        if response is not None:
            response.close()
