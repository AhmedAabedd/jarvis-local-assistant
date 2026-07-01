"""Modern, readable trace output for the agent graph — Claude-Code-ish, purple.

Everything goes to stderr so it never mixes with the streamed reply on stdout.
Colors auto-disable when stderr isn't a TTY (e.g. piped to a file).

Vocabulary:
    node(name, detail)        a graph node taking over   ●
    event(text)               a small sub-step           ⎿
    tool(name, params, res)   a tool call + its params   ⏺
    block(title, body)        a larger payload in/out    ▌ │
"""

from __future__ import annotations

import shutil
import sys
import textwrap
import threading

_TTY = sys.stderr.isatty()

# Serializes every stderr write and lets a live status line (e.g. the CLI's
# thinking spinner) wipe itself right before any trace line prints, so trace
# output never lands on top of the spinner and leaves frozen leftovers.
_lock = threading.RLock()
_pre_output = None


def output_lock() -> threading.RLock:
    """Shared lock so an external writer (the spinner) can serialize with trace."""
    return _lock


def set_pre_output(fn) -> None:
    """Register a callback run (under the lock) just before each trace line."""
    global _pre_output
    _pre_output = fn


def gap() -> None:
    """Emit a blank line into the trace stream (spacing between sections)."""
    _out("")


def _c(code: str) -> str:
    return code if _TTY else ""


# --- purple theme -----------------------------------------------------------
PURPLE = _c("\033[38;2;168;85;247m")   # #a855f7 accent
LAV = _c("\033[38;2;196;181;253m")     # #c4b5fd light
DIM = _c("\033[38;2;128;128;138m")     # muted grey
BOLD = _c("\033[1m")
RESET = _c("\033[0m")


def _out(text: str) -> None:
    with _lock:
        if _pre_output is not None:
            _pre_output()
        print(text, file=sys.stderr, flush=True)


def _width() -> int:
    return max(50, shutil.get_terminal_size((100, 24)).columns)


def _preview(text: str, limit: int = 150) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _fmt_params(params: dict) -> str:
    parts = []
    for key, value in (params or {}).items():
        if isinstance(value, str) and len(value) > 80:
            shown = f"{LAV}<{len(value)} chars>{RESET}"
        else:
            shown = f"{LAV}{value!r}{RESET}"
        parts.append(f"{DIM}{key}={RESET}{shown}")
    return f"{DIM}, {RESET}".join(parts)


# --- public API -------------------------------------------------------------

def node(name: str, detail: str = "") -> None:
    tail = f"{DIM}  ·  {detail}{RESET}" if detail else ""
    _out(f"\n\n{PURPLE}●{RESET} {BOLD}{PURPLE}{name}{RESET}{tail}")


def event(text: str) -> None:
    _out(f"  {DIM}⎿ {text}{RESET}")


def tool(name: str, params: dict | None = None, result: str | None = None) -> None:
    _out(f"  {LAV}⏺{RESET} {BOLD}{name}{RESET}{DIM}({RESET}{_fmt_params(params or {})}{DIM}){RESET}")
    if result is not None:
        _out(f"    {DIM}⎿ {_preview(result)}{RESET}")


def block(title: str, body: str, max_lines: int = 60) -> None:
    _out(f"  {PURPLE}▌{RESET} {BOLD}{LAV}{title}{RESET}")
    width = _width() - 6
    shown = 0
    for line in (str(body).splitlines() or ["(empty)"]):
        for wrapped in (textwrap.wrap(line, width) or [""]):
            _out(f"  {PURPLE}│{RESET} {wrapped}")
            shown += 1
            if shown >= max_lines:
                _out(f"  {PURPLE}│{RESET} {DIM}… (truncated){RESET}")
                return


def log(name: str, message: str = "") -> None:
    """Generic fallback line for anything that doesn't fit the above."""
    _out(f"  {DIM}{name}: {message}{RESET}")


_BANNER = [
    "███╗   ███╗ ██████╗ ██╗   ██╗███╗   ██╗██╗██████╗ ",
    "████╗ ████║██╔═══██╗██║   ██║████╗  ██║██║██╔══██╗",
    "██╔████╔██║██║   ██║██║   ██║██╔██╗ ██║██║██████╔╝",
    "██║╚██╔╝██║██║   ██║██║   ██║██║╚██╗██║██║██╔══██╗",
    "██║ ╚═╝ ██║╚██████╔╝╚██████╔╝██║ ╚████║██║██║  ██║",
    "╚═╝     ╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝╚═╝  ╚═╝",
]


# Left gutter for the wordmark so it doesn't hug the terminal edge; matches
# the 2-space margin the rest of the UI (kv rows, blocks) already uses.
_BANNER_PAD = "   "


def banner(subtitle: str = "") -> None:
    """Print the big purple MOUNIR wordmark to stdout (startup branding)."""
    print()
    for line in _BANNER:
        print(f"{_BANNER_PAD}{PURPLE}{BOLD}{line}{RESET}")
    if subtitle:
        print(f"{_BANNER_PAD}{DIM}{subtitle}{RESET}")
    print()


def rule(width: int | None = None) -> None:
    """A horizontal divider line (stdout)."""
    w = width or min(_width(), 52)
    print(f"{PURPLE}{'─' * w}{RESET}")


def kv(label: str, value: str) -> None:
    """An aligned key/value row for the startup header (stdout)."""
    print(f"  {DIM}{label:<14}{RESET}{LAV}{value}{RESET}")


def agent_row(label: str, value: str) -> None:
    """The highlighted head of the startup tree — the supervisor agent itself.
    Bright + bold so the eye lands here first; the specialist rows below read
    as its children."""
    print(f"  {BOLD}{LAV}{label:<14}{RESET}{BOLD}{value}{RESET}")


def sub_row(label: str, value: str, last: bool = False) -> None:
    """A specialist nested under the agent, drawn dim as a tree child so it
    visually recedes beneath the highlighted agent row."""
    branch = "└─" if last else "├─"
    print(f"  {PURPLE}{branch}{RESET} {DIM}{label:<11}{value}{RESET}")
