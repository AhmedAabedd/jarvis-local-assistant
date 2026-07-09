"""Email agent — Gmail specialist backed by an MCP server.

Unlike the other specialists, its tools are NOT hand-written here: each task
spawns the Gmail MCP server (@gongrzhe/server-gmail-autoauth-mcp) as a stdio
subprocess, asks it for its tool list, and hands those schemas straight to the
LLM. Tool calls are executed by the server (real Gmail API over OAuth — no
IMAP/SMTP). When the server adds tools, this agent gains them for free.

The big Gmail schemas live only in THIS agent's context; the supervisor just
sees delegate_to_email and gets back a short report.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
from pathlib import Path

from .. import config, llm
from .. import trace

MAX_TOOL_ROUNDS = 8
# One tool result can be a whole inbox page or email body — cap it so a huge
# thread can't flood the loop's context.
MAX_RESULT_CHARS = 8000

# Written by the server's one-time `auth` flow. Checked before spawning so a
# missing login fails fast with instructions instead of hanging on a browser
# consent screen mid-conversation.
_AUTH_FILE = Path.home() / ".gmail-mcp" / "credentials.json"

# Destructive / outward-facing tools: the user confirms via the active channel
# (terminal y/N or Telegram reply) before they run.
_CONFIRM_TOOLS = {"send_email", "delete_email", "batch_delete_emails"}

SYSTEM_PROMPT = """\
You are Mounir's email specialist. You operate Ahmed's own Gmail account
through the tools provided (they come from a Gmail MCP server and act on the
real mailbox).

RULES
- Do exactly what the task asks, then STOP. Don't label, archive, or delete
  anything the task didn't ask for.
- Search with Gmail query syntax (from:, subject:, is:unread, newer_than:2d,
  has:attachment ...) — one good query beats many vague ones.
- Report what the tools actually returned. Quote real subjects, senders, and
  content — never invent or embellish email text.
- When reading email, report the substance (who, what, what's needed), not
  raw headers or HTML.
- Sending and deleting ask the user to confirm by themselves; if the result
  says the user declined, relay that and STOP — don't retry.
- If a tool errors, say what failed plainly. Don't pretend it worked.

FINAL REPORT (MANDATORY)
Your last message is read by the SUPERVISOR, not the user. A few short
sentences with the concrete outcome, e.g. "Sent the report to Sami." or
"3 unread from LinkedIn; the only important one is a recruiter message from X
about Y." No fluff, no headers — never write the words "FINAL REPORT".
"""


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


async def _call(session, name: str, args: dict) -> str:
    """One tool call via the MCP server, confirmation gate included."""
    if name in _CONFIRM_TOOLS:
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


async def _run_async(task: str) -> str:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    argv = shlex.split(config.GMAIL_MCP_COMMAND)
    params = StdioServerParameters(command=argv[0], args=argv[1:])

    executed: list[str] = []  # tool results so far — actions that REALLY happened
    retried_empty = False

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = _openai_tools((await session.list_tools()).tools)

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task},
            ]

            for round_num in range(MAX_TOOL_ROUNDS):
                try:
                    # the LLM call blocks on HTTP — keep the MCP loop breathing.
                    message = await asyncio.to_thread(
                        llm.ollama_cloud_chat, messages, tools=tools, model=config.EMAIL_MODEL
                    )
                except Exception as exc:
                    if executed:
                        # The LLM died AFTER tools ran (e.g. rate limit on the
                        # report call). Saying "failed" would make the
                        # supervisor redo actions that already happened.
                        return (
                            "Email agent was cut off by an LLM error while "
                            "reporting, but these actions DID run: "
                            + "; ".join(executed) + ". Do NOT redo them."
                        )
                    return f"Email agent failed: {exc}"

                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or []

                if not content.strip() and not tool_calls:
                    if retried_empty:
                        return "Email agent failed: the LLM returned an empty response twice."
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
                    result = await _call(session, name, args)
                    trace.tool(name, args, result)
                    executed.append(f"{name} -> {result[:200]}")
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", "call_0"),
                            "content": result,
                        }
                    )

    return "Email agent reached max tool rounds — partial outcome only."


def run(task: str) -> str:
    """Run the email agent on a task. Returns a short plain-text report."""
    if not config.OLLAMA_API_KEY:
        return "Email agent failed: OLLAMA_API_KEY is not set."
    if not _AUTH_FILE.exists():
        return (
            "Email agent failed: Gmail MCP is not authenticated on this machine. "
            "One-time setup: put the Google OAuth keys at "
            "~/.gmail-mcp/gcp-oauth.keys.json, then run "
            "`npx @gongrzhe/server-gmail-autoauth-mcp auth` and approve in the browser."
        )
    try:
        return asyncio.run(_run_async(task)).strip()
    except Exception as exc:
        return f"Email agent failed: {exc}"
