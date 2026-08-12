"""Knowledge agent — curator of the knowledge folder (long-term memory).

The orchestrator calls run(task) and gets back a short plain-text report of
what was saved, updated, or deleted. The knowledge agent does the whole judge →
check-for-duplicates → write → report loop in its OWN context; the supervisor
never edits knowledge files itself.

The index (index.md) is maintained BY CODE, not by the model: save_knowledge
and delete_knowledge update it automatically, so a file can never exist
without its index line or vice versa. The knowledge agent cannot touch index.md
directly.

Writes are ISOLATED to this agent. The supervisor still READS knowledge
directly (the index rides in its context; read_file is its fast path) — only
changes go through here.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from .. import config, graph_runtime, llm

MAX_TOOL_ROUNDS = 8

SEARCH_MAX_HITS = 30          # cap grep output so a broad query can't flood context
READ_MAX_CHARS = 8000         # cap a single file read
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # one path segment

# Files read this task (via read_knowledge). save_knowledge refuses to REWRITE
# an existing file and append_knowledge refuses to touch one that hasn't been
# read yet — the same safeguard used by the supervisor, so the model
# can't skip the read-before-write rule.
_files_read: set[str] = set()

SYSTEM_PROMPT = """\
You are the knowledge specialist — the sole curator of the knowledge folder, the
assistant's long-term memory. Every file in that folder is loaded into the
assistant's head on demand, so the folder's quality IS the assistant's memory
quality. You keep it small, current, and true.

WHAT DESERVES SAVING
- Durable facts the user will reuse: contacts, preferences, templates, routines,
  project facts, how-to knowledge for recurring tasks.
- NOT: one-off task results, whole conversations, anything trivially looked up
  again, secrets (passwords, API keys — refuse these outright and say why).
- When a task doesn't deserve saving, refuse politely in your report and say
  why. An empty folder beats a junk drawer.

METHOD — before any write
1. Check the folder tree and index you were given (or list_knowledge if stale).
2. search_knowledge for the key term. If the fact belongs in an EXISTING file,
   append_knowledge or rewrite that file — never create a near-duplicate.
3. Only create a new file for a genuinely new topic.
4. If the fact contradicts what's stored (e.g. a changed email), read the file,
   then save_knowledge the corrected full version — don't append a duplicate.

HOW TO WRITE A FILE
- One topic per file. Short, factual, plain Markdown. No fluff.
- Address the assistant as "you" ("READ this before emailing…"). Never use a
  hard-coded assistant name in third person; use the configured profile.
- Refer to the user by the name in the configured profile.
- Names: short-kebab-case.md (e.g. gym-schedule.md).
- description = ONE line saying WHEN to read the file, starting with what it
  is, e.g. "The user's gym plan. READ when scheduling around workouts." It goes
  in the index, which is all the assistant sees by default — make it earn the
  open.

DELETING
- Delete only when asked, or when a file is clearly obsolete AND you were asked
  to clean up. When merging two files: read both, save the merged one, delete
  the leftover. Never delete on a hunch — say your doubt in the report instead.

HARD RULES
- index.md is machine-managed. You cannot write, append to, or delete it; the
  save/delete tools keep it in sync for you.
- Never claim a write happened unless you called the tool and saw its result.
- Stay inside the knowledge folder. You have no other filesystem access.

FINAL REPORT (MANDATORY)
Your last message is read by the SUPERVISOR, not the user. One short paragraph,
no headers, stating: the action taken (saved/appended/updated/deleted/refused),
the exact filename(s), and a one-line summary of the content change — e.g.
"Appended 'Sami: sami@x.com' to contacts.md." If you refused or found a
conflict, say exactly why and what you did instead. The supervisor relays this
to the user, so make it self-contained. No fluff, no "certainly!".
"""


# ---------------------------------------------------------------------------
# Path + index helpers (pure code — the guarantees live here, not in the prompt)
# ---------------------------------------------------------------------------

def _resolve_name(name: str) -> Path | str:
    """Turn a file name into a safe path inside KNOWLEDGE_DIR, or an error string."""
    name = (name or "").strip()
    if name.startswith("./"):
        name = name[2:]
    if not name:
        return "No file name given."
    if not name.endswith(".md"):
        name += ".md"
    # One path segment only — no subfolders, no ../ escapes.
    if not _NAME_RE.match(name) or ".." in name:
        return f"Bad file name: {name!r}. Use short-kebab-case, e.g. gym-schedule.md."
    if name.lower() == "index.md":
        return "index.md is machine-managed — the save/delete tools sync it for you."
    return config.KNOWLEDGE_DIR / name


def _index_lines() -> list[str]:
    try:
        return config.INDEX_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["# Knowledge index — the menu of what you know", ""]


def _entry_prefix(filename: str) -> str:
    return f"- {filename} "


def _index_upsert(filename: str, description: str) -> None:
    """Add or replace this file's line in index.md. Called by save_knowledge."""
    entry = f"- {filename} — {description.strip()}"
    lines = _index_lines()
    for i, line in enumerate(lines):
        if line.startswith(_entry_prefix(filename)):
            lines[i] = entry
            break
    else:
        # Insert after the last existing entry so the trailing comment (if any)
        # stays at the bottom; append at the end when there are no entries yet.
        last = max((i for i, l in enumerate(lines) if l.startswith("- ")), default=None)
        if last is None:
            lines.append(entry)
        else:
            lines.insert(last + 1, entry)
    config.INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _index_remove(filename: str) -> None:
    """Drop this file's line from index.md. Called by delete_knowledge."""
    lines = [l for l in _index_lines() if not l.startswith(_entry_prefix(filename))]
    config.INDEX_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tree() -> str:
    """A tree view of the knowledge folder with per-file line counts."""
    folder = config.KNOWLEDGE_DIR
    if not folder.is_dir():
        return f"(knowledge folder missing: {folder})"
    files = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith("."))
    lines = [f"{folder.name}/"]
    for i, p in enumerate(files):
        branch = "└──" if i == len(files) - 1 else "├──"
        try:
            count = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            meta = f"({count} lines)"
        except OSError:
            meta = "(unreadable)"
        lines.append(f"{branch} {p.name}  {meta}")
    return "\n".join(lines)


def _context() -> str:
    """Everything the knowledge agent should know before its first tool call."""
    try:
        index = config.INDEX_FILE.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        index = "(index.md is missing — it will be created on the first save)"
    return (
        f"KNOWLEDGE FOLDER: {config.KNOWLEDGE_DIR}\n"
        f"Today: {date.today().isoformat()}\n\n"
        f"Folder tree:\n{_tree()}\n\n"
        f"Current index.md (files NOT listed here are invisible to the assistant):\n{index}"
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def list_knowledge() -> str:
    """Fresh tree of the knowledge folder + the current index."""
    return _context()


def read_knowledge(name: str) -> str:
    """Read one knowledge file in full."""
    path = _resolve_name(name)
    if isinstance(path, str):
        return path
    if not path.exists():
        return f"No such file: {path.name}. Check list_knowledge for what exists."
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > READ_MAX_CHARS:
        text = text[:READ_MAX_CHARS] + " … [truncated]"
    _files_read.add(path.name)
    return f"{path.name} ({len(text.splitlines())} lines):\n\n{text}"


def search_knowledge(query: str) -> str:
    """Grep the knowledge folder: every line containing `query`, as file:line: text."""
    query = (query or "").strip()
    if not query:
        return "No search query given."
    folder = config.KNOWLEDGE_DIR
    if not folder.is_dir():
        return f"Knowledge folder missing: {folder}"
    hits: list[str] = []
    needle = query.lower()
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            file_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(file_lines, 1):
            if needle in line.lower():
                hits.append(f"{path.name}:{n}: {line.strip()}")
                if len(hits) >= SEARCH_MAX_HITS:
                    hits.append("… [more hits truncated — narrow the query]")
                    return f"Matches for '{query}':\n" + "\n".join(hits)
    if not hits:
        return f"No matches for '{query}' anywhere in the knowledge folder."
    return f"Matches for '{query}':\n" + "\n".join(hits)


def save_knowledge(name: str, description: str, content: str) -> str:
    """Create or fully rewrite a knowledge file; its index line is synced by code."""
    path = _resolve_name(name)
    if isinstance(path, str):
        return path
    description = " ".join((description or "").split())
    if not description:
        return "A one-line description is required — it becomes the index entry."
    content = (content or "").strip()
    if not content:
        return "Refusing to save an empty file."
    existed = path.exists()
    if existed and path.name not in _files_read:
        return f"Read {path.name} first with read_knowledge before rewriting it."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content + "\n", encoding="utf-8")
    _index_upsert(path.name, description)
    _files_read.add(path.name)  # just wrote it — counts as "seen" for later edits
    verb = "Rewrote" if existed else "Created"
    return f"{verb} {path.name} ({len(content.splitlines())} lines) and synced its index line."


def append_knowledge(name: str, content: str) -> str:
    """Append lines to the END of an existing knowledge file."""
    path = _resolve_name(name)
    if isinstance(path, str):
        return path
    if not path.exists():
        return f"No such file: {path.name}. Use save_knowledge to create a new file."
    if path.name not in _files_read:
        return f"Read {path.name} first with read_knowledge before appending to it."
    content = (content or "").strip()
    if not content:
        return "Nothing to append."
    old = path.read_text(encoding="utf-8", errors="replace")
    sep = "" if old.endswith("\n") or not old else "\n"
    path.write_text(old + sep + content + "\n", encoding="utf-8")
    return f"Appended {len(content.splitlines())} line(s) to {path.name}."


def delete_knowledge(name: str) -> str:
    """Delete a knowledge file; its index line is removed by code."""
    path = _resolve_name(name)
    if isinstance(path, str):
        return path
    if not path.exists():
        return f"No such file: {path.name} — nothing to delete."
    path.unlink()
    _index_remove(path.name)
    return f"Deleted {path.name} and removed its index line."


@tool("list_knowledge")
def list_knowledge_tool() -> str:
    """Show the current knowledge tree and index."""

    return list_knowledge()


@tool("read_knowledge")
def read_knowledge_tool(
    name: Annotated[str, "File name inside the knowledge folder."]
) -> str:
    """Read one knowledge file before changing or appending to it."""

    return read_knowledge(name)


@tool("search_knowledge")
def search_knowledge_tool(
    query: Annotated[str, "Case-insensitive text to find."]
) -> str:
    """Search all knowledge before writing so facts are not duplicated."""

    return search_knowledge(query)


@tool("save_knowledge")
def save_knowledge_tool(
    name: Annotated[str, "Short kebab-case Markdown file name."],
    description: Annotated[str, "One-line index description and when to read it."],
    content: Annotated[str, "Complete Markdown content."],
) -> str:
    """Create or rewrite a knowledge file and update its index entry."""

    return save_knowledge(name, description, content)


@tool("append_knowledge")
def append_knowledge_tool(
    name: Annotated[str, "Existing knowledge file name."],
    content: Annotated[str, "Lines to append."],
) -> str:
    """Append a small addition to an existing knowledge file."""

    return append_knowledge(name, content)


@tool("delete_knowledge")
def delete_knowledge_tool(
    name: Annotated[str, "Knowledge file name to delete."]
) -> str:
    """Delete a knowledge file and remove its index entry."""

    return delete_knowledge(name)


TOOLS = [
    list_knowledge_tool,
    read_knowledge_tool,
    search_knowledge_tool,
    save_knowledge_tool,
    append_knowledge_tool,
    delete_knowledge_tool,
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(task: str, allowed_tools: list[str] | None = None) -> str:
    """Run the knowledge agent on a task. Returns a short plain-text report."""
    from .. import db
    runtime = db.get_builtin_agent_runtime(
        "knowledge",
        fallback_model=config.KNOWLEDGE_MODEL,
        fallback_base_url=config.GEMINI_BASE_URL,
        fallback_api_key=config.GEMINI_API_KEY,
        fallback_provider="Gemini",
    )

    selected_tools = graph_runtime.select_tools(TOOLS, allowed_tools)

    _files_read.clear()  # fresh task — must read a file before modifying it

    messages = [
        {"role": "system", "content": config.specialist_system_prompt(SYSTEM_PROMPT)},
        {"role": "user", "content": f"{_context()}\n\nTASK FROM SUPERVISOR:\n{task}"},
    ]

    def error_report(executed: list[str], error: str) -> str:
        if executed:
            return (
                "Knowledge agent was cut off by an LLM error while reporting, but "
                f"these tool calls DID run: {'; '.join(executed)}. Do NOT redo them."
            )
        return f"Knowledge agent failed: {error}"

    return graph_runtime.run_tool_agent(
        messages,
        selected_tools,
        lambda history, schemas: llm.openai_chat(
            history,
            tools=schemas,
            model=runtime["model"],
            provider=runtime["provider"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
        ),
        max_rounds=MAX_TOOL_ROUNDS,
        empty_response=(
            "Knowledge agent failed: the model returned an empty response twice — "
            "NOTHING was saved or changed. Try again."
        ),
        exhausted_response=(
            "Knowledge agent reached max tool rounds — the task may be only partly done."
        ),
        error_formatter=error_report,
    )
