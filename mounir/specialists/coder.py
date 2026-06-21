"""Coder agent — self-contained coding specialist with its own file tools.

The orchestrator calls run(task) and gets back a short status summary.
The coder handles everything internally: reads files, creates them, modifies
them surgically, searches patterns — the orchestrator never sees the code itself.

File tools are ISOLATED to this agent. The orchestrator has no access to them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import ollama

# Swap to "qwen2.5-coder:7b" once the download finishes
CODER_MODEL: str = "qwen2.5-coder:7b"

MAX_TOOL_ROUNDS = 10
MAX_READ_CHARS = 20000

SYSTEM_PROMPT = """\
You are an expert software engineer. You write clean, correct, production-quality code.
You have tools to read, create, modify, delete, and search files directly — use them.

Rules:
- Never output code in your final reply. Write it to files using your tools.
- Use search_file to find the exact line before modifying — never guess.
- Use modify_file for surgical edits (change a line, a word, a block).
- Use create_file for new files — it fails if the file already exists.
- When done, reply with a SHORT summary: what files you created/modified and what they do.
- No fluff, no "certainly!", get straight to work.
- If the task is ambiguous, make reasonable assumptions and proceed.
"""

# ---------------------------------------------------------------------------
# Isolated file tools
# ---------------------------------------------------------------------------

def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def read_file(path: str) -> str:
    p = _resolve(path)
    if not p.is_file():
        return f"No such file: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {p}: {exc}"
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n… [truncated, {len(text)} chars total]"
    return text or "(empty file)"


def create_file(path: str, content: str) -> str:
    p = _resolve(path)
    if p.exists():
        return f"File already exists: {p}. Use modify_file to edit it."
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Could not create {p}: {exc}"
    return f"Created {p} ({len(content)} chars)."


def modify_file(path: str, old_str: str, new_str: str) -> str:
    """Surgical find-and-replace. old_str must match exactly once."""
    p = _resolve(path)
    if not p.is_file():
        return f"No such file: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {p}: {exc}"

    count = text.count(old_str)
    if count == 0:
        return f"String not found in {p}. Use search_file to find the exact text."
    if count > 1:
        return f"Found {count} occurrences of that string in {p} — too ambiguous. Make old_str more specific."

    new_text = text.replace(old_str, new_str, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"Could not write {p}: {exc}"
    return f"Modified {p} — replaced 1 occurrence."


def delete_file(path: str) -> str:
    p = _resolve(path)
    if not p.exists():
        return f"No such file: {p}"
    try:
        if p.is_dir():
            return f"{p} is a directory, not a file."
        p.unlink()
    except Exception as exc:
        return f"Could not delete {p}: {exc}"
    return f"Deleted {p}."


def search_file(path: str, pattern: str) -> str:
    """Search for a regex pattern in a file. Returns matching lines with numbers."""
    p = _resolve(path)
    if not p.is_file():
        return f"No such file: {p}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        regex = re.compile(pattern)
    except re.error as exc:
        return f"Invalid regex pattern: {exc}"
    except Exception as exc:
        return f"Could not read {p}: {exc}"

    matches = [
        f"  line {i+1}: {line}"
        for i, line in enumerate(lines)
        if regex.search(line)
    ]
    if not matches:
        return f"No matches for '{pattern}' in {p}."
    return f"Matches for '{pattern}' in {p}:\n" + "\n".join(matches)


# ---------------------------------------------------------------------------
# Tool schemas (for ollama function calling)
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use before modifying to understand the current state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file (~ allowed)."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the given content. Fails if the file already exists.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to create (~ allowed)."},
                    "content": {"type": "string", "description": "Full content to write into the file."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "modify_file",
            "description": (
                "Surgically edit a file by replacing an exact string with a new one. "
                "old_str must match exactly once in the file — use search_file first if unsure. "
                "Use this for changing a line, a word, or a block without rewriting the whole file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "old_str": {"type": "string", "description": "The exact string to find and replace (must be unique in the file)."},
                    "new_str": {"type": "string", "description": "The string to replace it with."},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to delete."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_file",
            "description": (
                "Search for a regex pattern in a file. Returns matching lines with their line numbers. "
                "Use this before modify_file to find the exact text to replace."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to search."},
                    "pattern": {"type": "string", "description": "Regex pattern to search for."},
                },
                "required": ["path", "pattern"],
            },
        },
    },
]

_REGISTRY = {
    "read_file": read_file,
    "create_file": create_file,
    "modify_file": modify_file,
    "delete_file": delete_file,
    "search_file": search_file,
}


def _dispatch(name: str, arguments: dict) -> str:
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    print(f"\n  [💻🔧 {name}: {arguments}]", file=sys.stderr, flush=True)
    try:
        return fn(**arguments)
    except TypeError as exc:
        return f"Bad arguments for {name}: {exc}"
    except Exception as exc:
        return f"Tool {name} failed: {exc}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(task: str) -> str:
    """Run the coder agent on a task. Returns a short status summary."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    print(f"\n  [💻 coder starting...]\n", file=sys.stderr, flush=True)

    for round_num in range(MAX_TOOL_ROUNDS):
        print(f"\n  [💻 coder thinking... round {round_num + 1}]", file=sys.stderr, flush=True)
        # ollama tool calling (non-streaming for tool rounds — streaming +
        # tool_calls don't mix cleanly in ollama's API yet)
        try:
            response = ollama.chat(
                model=CODER_MODEL,
                messages=messages,
                tools=TOOLS,
                options={"num_ctx": 16384},
                think=False,
            )
        except Exception as exc:
            return f"Coder failed: {exc}"

        message = response.message
        content = message.content or ""
        tool_calls = message.tool_calls or []

        # Stream the content to stderr so user sees progress
        if content:
            print(content, file=sys.stderr, flush=True)

        if not tool_calls:
            # No more tool calls — this is the final summary
            print(f"\n  [💻 coder done after {round_num+1} round(s)]\n",
                  file=sys.stderr, flush=True)
            return content.strip() or "Done."

        # Append assistant turn
        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls})

        # Execute tools and append results
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = dict(tc.function.arguments) if tc.function.arguments else {}
            except Exception:
                args = {}
            result = _dispatch(name, args)
            messages.append({
                "role": "tool",
                "content": result,
            })

    return "Coder reached max tool rounds — task may be incomplete."