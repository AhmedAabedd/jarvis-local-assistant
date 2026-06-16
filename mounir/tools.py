"""Tool layer — native function-calling tools the model can invoke.

The model is shown SCHEMAS and decides when to call a tool; the agent runs it
through dispatch() and feeds the result back. New tools (files, terminal, …)
just add a function + schema + registry entry here; the agent loop is generic.
"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

WEB_SEARCH_MAX_RESULTS = 5
# Cap how much of a file we read back so a huge file can't blow up the context.
MAX_READ_CHARS = 20000


def web_search(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> str:
    """Search the web (DuckDuckGo) and return ranked title/snippet/URL results."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
    except ImportError:
        return "Web search unavailable: the 'ddgs' package isn't installed."
    except Exception as exc:
        return f"Web search failed: {exc}"

    if not results:
        return f"No results found for '{query}'."

    lines = [f"Search results for '{query}':"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        body = r.get("body", "").strip()
        href = r.get("href", "").strip()
        lines.append(f"{i}. {title}\n   {body}\n   ({href})")
    return "\n".join(lines)


def get_datetime() -> str:
    """Return the current local date and time — the model has no clock of its own."""
    return datetime.datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")


def _resolve(path: str) -> Path:
    """Expand ~ and make paths predictable before touching the filesystem."""
    return Path(path).expanduser()


def read_file(path: str) -> str:
    """Read a UTF-8 text file and return its contents (truncated if very large)."""
    p = _resolve(path)
    try:
        if not p.is_file():
            return f"No such file: {p}"
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {p}: {exc}"
    if len(text) > MAX_READ_CHARS:
        return text[:MAX_READ_CHARS] + f"\n… [truncated, {len(text)} chars total]"
    return text or "(file is empty)"


def write_file(path: str, content: str) -> str:
    """Write text to a file, creating parent folders. Overwrites if it exists."""
    p = _resolve(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Could not write {p}: {exc}"
    return f"Wrote {len(content)} characters to {p}."


def list_directory(path: str = ".") -> str:
    """List a directory's entries (folders shown with a trailing slash)."""
    p = _resolve(path)
    try:
        if not p.is_dir():
            return f"Not a directory: {p}"
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except Exception as exc:
        return f"Could not list {p}: {exc}"
    if not entries:
        return f"{p} is empty."
    lines = [f"Contents of {p}:"]
    lines += [f"  {e.name}/" if e.is_dir() else f"  {e.name}" for e in entries]
    return "\n".join(lines)


# What the model sees. Descriptions matter — they're how it decides to call.
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current events, recent facts, prices, "
                "documentation, or anything that may have changed since training "
                "or that you're unsure about. Returns top results with titles, "
                "snippets, and URLs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A concise search-engine-style query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a text file on the local machine. Use "
                "this to look at a file the user mentions before answering."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (~ for the home folder is fine).",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write text to a file on the local machine, creating it (and any "
                "parent folders) or overwriting it if it already exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to write to (~ for the home folder is fine).",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full text to write into the file.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List the files and folders inside a directory on the local "
                "machine. Use this to see what's available before reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory to list. Defaults to the current folder.",
                    }
                },
                "required": [],
            },
        },
    },
]

_REGISTRY = {
    "web_search": web_search,
    "get_datetime": get_datetime,
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
}


def dispatch(name: str, arguments: dict) -> str:
    """Run a tool by name with the given arguments, returning a text result."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    # Printed (not yielded) so it shows on screen but is never spoken by TTS.
    print(f"  [🔧 {name}: {arguments}]", file=sys.stderr, flush=True)
    try:
        return fn(**arguments)
    except TypeError as exc:
        return f"Bad arguments for {name}: {exc}"
    except Exception as exc:
        return f"Tool {name} failed: {exc}"
