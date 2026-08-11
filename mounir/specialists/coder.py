"""Coder agent — self-contained coding specialist with its own file tools.

The orchestrator calls run(task) and gets back a short status summary.
The coder handles everything internally: reads files, creates them, modifies
them surgically, searches patterns — the orchestrator never sees the code itself.

File tools are ISOLATED to this agent. The orchestrator has no access to them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from .. import config, graph_runtime, llm

MAX_TOOL_ROUNDS = 10
# The coder runs on a capable cloud model (large context) doing real code work,
# so it can read meatier chunks than the supervisor. It still pages with
# start_line and navigates with search_file instead of reading whole files.
MAX_READ_LINES = 1200   # lines per read when no range is given
MAX_READ_CHARS = 24000  # hard char ceiling per read

# Files seen this task (via read_file / search_file). modify_file refuses to edit
# a file that wasn't looked at first. Cleared at the start of every run().
_files_read: set[str] = set()

SYSTEM_PROMPT = """\
You are an expert software engineer working as the dedicated coder specialist.
You write clean, correct, production-quality code. You have tools to read,
create, modify, delete, and search files directly — use them.

WORKING RULES
- Never output code in your reply. Write it to files using your tools.
- create_file for new files (it fails if the file exists). modify_file for surgical
  edits to an existing file (a line, a word, a block).
- To edit: locate the spot with search_file first — it returns the matching line
  WITH surrounding context and line numbers. Copy enough of that context into old_str
  so it matches EXACTLY ONCE, then modify_file. Never guess at the text.
- read_file shows line numbers and accepts start_line/end_line — read a NARROW range,
  never the whole file just to look around. Use search_file to navigate.
- TRUST your tools: create_file and modify_file report success in their result. Do NOT
  re-read a file you just wrote to "double-check" it. Do not search for the same thing
  twice. Do not read a file you just created.
- The moment the file is written and correct, STOP and write the report. No extra
  verification passes.
- If the task is ambiguous, make a reasonable assumption and proceed; record it in the report.
- No fluff, no "certainly!", get straight to work.

FINAL REPORT (MANDATORY)
Your last message is read by the SUPERVISOR, not the user. It must let the
supervisor understand exactly what happened without ever seeing the code.
Always end with EXACTLY this structure and nothing after it:

## Coder Report
**Status:** done | partial | failed
**Summary:** <one sentence on what was accomplished>

**Files:**
- `<absolute/path>` (created|modified|deleted) — <what it contains or what changed>
- ... one line per file, list EVERY file you touched ...

**Notes:** <assumptions made, anything left undone, how to run/verify — or "none">

Report rules:
- List every file you created, modified, or deleted. Never omit one. Always use absolute paths.
- Each file line = full path, the action in parentheses, then a short plain-English description of the contents/change.
- If you created and changed nothing, write "- none" under Files.
- Keep it tight. No code blocks, no fluff.

EXAMPLE REPORT
## Coder Report
**Status:** done
**Summary:** Added a JSON benchmark script and a make target to run it.

**Files:**
- `/home/ahmed/bench.py` (created) — benchmarks orjson vs ujson vs json; run_bench() loops N times and prints an ops/sec table; runnable under __main__.
- `/home/ahmed/mounir_assistant/jarvis-local-assistant/Makefile` (modified) — added a `bench` target that runs `python bench.py`.

**Notes:** Assumed orjson is already installed. Run with `python /home/ahmed/bench.py` or `make bench`.
"""

# ---------------------------------------------------------------------------
# Isolated file tools
# ---------------------------------------------------------------------------

def _resolve(path: str) -> Path:
    return Path(path).expanduser().resolve()


def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a file (optionally a line range) with line numbers, like `cat -n`.

    Reads up to MAX_READ_LINES lines from start_line by default; page further or
    read a narrow slice with start_line/end_line.
    """
    p = _resolve(path)
    if not p.is_file():
        return f"No such file: {p}"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"Could not read {p}: {exc}"
    if not lines:
        _files_read.add(str(p))
        return "(empty file)"

    total = len(lines)
    start = max(1, start_line)
    if start > total:
        return f"{p} has {total} lines; start_line {start} is past the end."
    end = min(total, start + MAX_READ_LINES - 1) if end_line is None else min(total, end_line)

    body = "\n".join(f"{start + i:>5}\t{line}" for i, line in enumerate(lines[start - 1:end]))
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n… [truncated — read a narrower line range]"
    more = f"\n… {total - end} more line(s) below — read again with start_line={end + 1}." if end < total else ""
    _files_read.add(str(p))
    return f"{p} (lines {start}-{end} of {total}):\n{body}{more}"


def create_file(path: str, content: str) -> str:
    p = _resolve(path)
    if p.exists():
        return f"File already exists: {p}. Use modify_file to edit it."
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Could not create {p}: {exc}"
    _files_read.add(str(p))  # just created it — counts as "seen" for modify_file
    return f"Created {p} ({len(content)} chars)."


def modify_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Surgical find-and-replace. old_str must match exactly once, unless replace_all."""
    p = _resolve(path)
    if not p.is_file():
        return f"No such file: {p}"
    if str(p) not in _files_read:
        return f"Look at {p} first with search_file or read_file before modifying it."
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {p}: {exc}"

    count = text.count(old_str)
    if count == 0:
        return f"String not found in {p}. Use search_file to find the exact text."
    if count > 1 and not replace_all:
        return f"Found {count} occurrences of that string in {p} — too ambiguous. Make old_str more specific, or set replace_all to change all."

    new_text = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"Could not write {p}: {exc}"
    return f"Modified {p} — replaced {count if replace_all else 1} occurrence(s)."


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


def search_file(path: str, pattern: str, context: int = 2) -> str:
    """Find a regex in a file. Returns each match with `context` lines around it,
    numbered, so you can copy an exact, unique block straight into modify_file."""
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

    hits = [i for i, line in enumerate(lines) if regex.search(line)]
    if not hits:
        return f"No matches for '{pattern}' in {p}."
    _files_read.add(str(p))  # saw the file's content — counts as "seen" for modify_file

    blocks: list[str] = []
    for idx in hits:
        lo = max(0, idx - context)
        hi = min(len(lines), idx + context + 1)
        block = "\n".join(
            f"{'>' if j == idx else ' '}{j + 1:>5}\t{lines[j]}" for j in range(lo, hi)
        )
        blocks.append(block)
    out = f"Matches for '{pattern}' in {p} ({len(hits)} hit(s), '>' marks the match):\n"
    out += "\n  --\n".join(blocks)
    if len(out) > MAX_READ_CHARS:
        out = out[:MAX_READ_CHARS] + "\n… [truncated]"
    return out


# Typed LangChain tools are the source of truth.  Their annotations generate the
# provider schema and ToolNode handles validation/execution.
@tool("read_file")
def read_file_tool(
    path: Annotated[str, "File path; ~ is allowed."],
    start_line: Annotated[int, "First line to read, starting at 1."] = 1,
    end_line: Annotated[int | None, "Optional inclusive final line."] = None,
) -> str:
    """Read a narrow file range with line numbers; use search_file to navigate."""

    return read_file(path, start_line, end_line)


@tool("create_file")
def create_file_tool(
    path: Annotated[str, "New file path; ~ is allowed."],
    content: Annotated[str, "Complete text to write."],
) -> str:
    """Create a new file; fail rather than overwrite an existing file."""

    return create_file(path, content)


@tool("modify_file")
def modify_file_tool(
    path: Annotated[str, "Existing file path."],
    old_str: Annotated[str, "Exact text to replace."],
    new_str: Annotated[str, "Replacement text."],
    replace_all: Annotated[bool, "Replace every occurrence."] = False,
) -> str:
    """Surgically replace exact text after reading or searching the file."""

    return modify_file(path, old_str, new_str, replace_all)


@tool("delete_file")
def delete_file_tool(path: Annotated[str, "File path to delete."]) -> str:
    """Delete one file from disk."""

    return delete_file(path)


@tool("search_file")
def search_file_tool(
    path: Annotated[str, "File path to search."],
    pattern: Annotated[str, "Regular expression to find."],
    context: Annotated[int, "Surrounding lines for every match."] = 2,
) -> str:
    """Find a regex with numbered context for a precise subsequent edit."""

    return search_file(path, pattern, context)


TOOLS = [
    read_file_tool,
    create_file_tool,
    modify_file_tool,
    delete_file_tool,
    search_file_tool,
]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(task: str) -> str:
    """Run the coder agent on a task. Returns its structured status report."""
    _files_read.clear()  # fresh task — must look at a file before modifying it
    if not config.NVIDIA_API_KEY:
        return "Coder failed: NVIDIA_API_KEY is not set."

    messages = [
        {"role": "system", "content": config.specialist_system_prompt(SYSTEM_PROMPT)},
        {"role": "user", "content": task},
    ]
    return graph_runtime.run_tool_agent(
        messages,
        TOOLS,
        lambda history, schemas: llm.nvidia_chat(
            history,
            tools=schemas,
            model=config.CODER_MODEL,
            disable_thinking=True,
        ),
        max_rounds=MAX_TOOL_ROUNDS,
        empty_response="Done.",
        exhausted_response="Coder reached max tool rounds — task may be incomplete.",
        error_formatter=lambda _executed, error: f"Coder failed: {error}",
    )
