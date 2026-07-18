"""Generic MCP specialist — one dynamic subagent instance per registry entry.

Same shape as the email agent (Mounir's hand-tuned MCP specialist): spawn the
server over stdio for the task, adopt whatever tools it advertises as OpenAI
schemas, loop with the LLM until it reports, and return ONLY the short report.
The server's tool schemas and raw results never leave this module — the parent
just sees its delegate tool and the report.

What the email agent hardcodes, this module takes from the registry spec:
the server command, the system prompt, the LLM endpoint, the extra env, and
which tools need the user's confirmation.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import traceback
from contextlib import asynccontextmanager

from .. import llm
from .. import trace

MAX_TOOL_ROUNDS = 8
# One tool result can be a whole page or listing — cap it so a huge result
# can't flood the loop's context.
MAX_RESULT_CHARS = 8000


def _openai_tools(mcp_tools) -> list[dict]:
    """Convert the server's MCP tool list to the OpenAI schema the LLM eats."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.inputSchema or {"type": "object", "properties": {}},
            },
        }
        for t in mcp_tools
    ]


def _result_text(result) -> str:
    """Flatten an MCP call result to plain text for the tool message."""
    parts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    text = "\n".join(parts).strip() or "(empty result)"
    if getattr(result, "isError", False):
        text = f"Tool failed: {text}"
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n[... truncated]"
    return text


def _exc_detail(exc: BaseException) -> str:
    """Pull a human-readable message out of an ExceptionGroup / BaseExceptionGroup."""
    if isinstance(exc, BaseExceptionGroup):
        if len(exc.exceptions) == 1:
            return _exc_detail(exc.exceptions[0])
        return "; ".join(_exc_detail(e) for e in exc.exceptions)
    msg = str(exc).strip()
    if not msg:
        return type(exc).__name__
    return msg


async def _call(session, name: str, args: dict, confirm_tools: set[str]) -> str:
    """One tool call via the MCP server, confirmation gate included."""
    if name in confirm_tools:
        from .. import tools as _tools

        summary = f"{name} {json.dumps(args, ensure_ascii=False)[:400]}"
        # confirm_fn blocks (terminal prompt / Telegram reply) — off the loop.
        allowed = await asyncio.to_thread(_tools.confirm_fn, summary)
        if not allowed:
            return "User declined — action cancelled. Do not retry."
    try:
        return _result_text(await session.call_tool(name, args))
    except Exception as exc:
        return f"Tool {name} failed: {exc}"


@asynccontextmanager
async def _mcp_session(spec: dict):
    """Yield an initialized MCP ClientSession, using stdio or SSE depending on
    whether the connection string looks like a URL."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import get_default_environment, stdio_client
    from mcp.client.sse import sse_client

    connection = (spec.get("command") or "").strip()
    if connection.startswith(("http://", "https://")):
        async with sse_client(connection) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        argv = shlex.split(connection)
        # None = the SDK's default minimal env. When the spec asks for extra
        # vars, merge them on top, expanding "$VAR" references.
        env = None
        if spec.get("env"):
            env = get_default_environment() | {
                k: os.path.expandvars(str(v)) for k, v in spec["env"].items()
            }
        params = StdioServerParameters(command=argv[0], args=argv[1:], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


async def _run_async(task: str, spec: dict, api_key: str) -> str:
    confirm_tools = set(spec.get("confirm_tools") or [])
    executed: list[str] = []  # tool results so far — actions that REALLY happened
    retried_empty = False

    try:
        async with _mcp_session(spec) as session:
            tools = _openai_tools((await session.list_tools()).tools)

            messages = [
                {"role": "system", "content": spec["prompt"]},
                {"role": "user", "content": task},
            ]

            for round_num in range(MAX_TOOL_ROUNDS):
                try:
                    # the LLM call blocks on HTTP — keep the MCP loop breathing.
                    message = await asyncio.to_thread(
                        llm.openai_chat,
                        messages,
                        tools=tools or None,
                        model=spec["model"],
                        base_url=spec["base_url"],
                        api_key=api_key,
                    )
                except Exception as exc:
                    if executed:
                        # The LLM died AFTER tools ran (e.g. rate limit on the
                        # report call). Saying "failed" would make the parent
                        # redo actions that already happened.
                        return (
                            f"{spec['name']} agent was cut off by an LLM error while "
                            "reporting, but these actions DID run: "
                            + "; ".join(executed) + ". Do NOT redo them."
                        )
                    return f"{spec['name']} agent failed: {_exc_detail(exc)}"

                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or []

                if not content.strip() and not tool_calls:
                    if retried_empty:
                        return f"{spec['name']} agent failed: the LLM returned an empty response twice."
                    retried_empty = True
                    continue

                if not tool_calls:
                    trace.event(f"{round_num + 1} round(s)")
                    return re.sub(r"(?i)^\s*final report:?\s*", "", content.strip())

                messages.append(
                    {"role": "assistant", "content": content, "tool_calls": tool_calls}
                )

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"].get("arguments") or "{}")
                    except Exception:
                        args = {}
                    result = await _call(session, name, args, confirm_tools)
                    trace.tool(name, args, result)
                    executed.append(f"{name} -> {result[:200]}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", "call_0"),
                            "content": result,
                        }
                    )
    except Exception as exc:
        return (
            f"{spec['name']} agent failed to connect to its MCP server: "
            f"{_exc_detail(exc)}"
        )

    return f"{spec['name']} agent reached max tool rounds — partial outcome only."


def run(task: str, spec: dict) -> str:
    """Run one dynamic MCP subagent on a task. Returns its plain-text report."""
    name = spec.get("name", "MCP")
    api_key = spec.get("api_key") or ""
    if not api_key:
        return (
            f"{name} agent failed: no API key configured for its model. "
            "Set one in the model preset (Admin page or `python -m mounir.mcp_agents`)."
        )
    if not (spec.get("command") or "").strip():
        return (
            f"{name} agent failed: no MCP server command configured. "
            "Set one in the server preset (Admin page or `python -m mounir.mcp_agents`)."
        )
    try:
        return asyncio.run(_run_async(task, spec, api_key)).strip()
    except Exception as exc:
        detail = _exc_detail(exc)
        # Log the full traceback so the real cause is inspectable, even if the
        # summary shown to the user is short.
        traceback.print_exc()
        return f"{name} agent failed: {detail}"
