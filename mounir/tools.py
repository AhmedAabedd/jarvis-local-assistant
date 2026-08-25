"""Supervisor tool implementations and typed LangChain declarations."""

from __future__ import annotations

import shutil
import subprocess
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Callable, Iterator

from langchain_core.tools import tool

from . import browser_control

# Specialist agents are reached through LangGraph handoffs. These functions
# remain defensive fallbacks for direct tool invocation.
def delegate_to_knowledge(task: str) -> str:
    """Hand a durable-memory lookup or change to the Knowledge agent."""
    from . import db
    if not db.is_builtin_agent_enabled("knowledge"):
        return "The Knowledge agent is inactive and cannot be used."
    from .specialists.knowledge import run
    return run(task)

def delegate_to_media(task: str) -> str:
    """Delegate any local file or media operation except inspecting directly attached chat images."""
    from . import db
    if not db.is_builtin_agent_enabled("media"):
        return "Files and Media is inactive and cannot be used."
    from .specialists.media import run
    return run(task)


def delegate_to_system(task: str) -> str:
    """Hand a hardware/system control task to the system agent; returns its report."""
    from . import db
    if not db.is_builtin_agent_enabled("system"):
        return "The System agent is inactive and cannot be used."
    from .specialists.system import run
    return run(task)


# bash: default timeout (s) to kill a hung command, a hard ceiling the model
# can't exceed, and an output cap so a chatty command can't flood the context.
BASH_DEFAULT_TIMEOUT = 30
BASH_MAX_TIMEOUT = 600
BASH_MAX_OUTPUT = 4000


def _resolve(path: str) -> Path:
    """Expand ~ and make paths predictable before touching the filesystem."""
    return Path(path).expanduser()


def _default_confirm(action: str) -> bool:
    """Ask the user, in the terminal, to confirm an action. y = yes."""
    try:
        answer = input(f"  [⚠ confirm?]\n{action}\n  y/N > ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


# The confirmation gate for anything outward-facing or irreversible (running a
# command, sending an email). The CLI uses the terminal prompt above; the voice
# loop can swap in a spoken "yes/no" by reassigning tools.confirm_fn. Whatever it
# is, those tools never act unless this returns True.
confirm_fn = _default_confirm

# A running server can accept turns from several interfaces at once (web,
# Telegram, and later others).  The fallback above preserves the simple CLI
# contract, while this context-local override routes a turn's confirmations
# back to the interface that started that turn.
_turn_confirm_fn: ContextVar[Callable[[str], bool] | None] = ContextVar(
    "mounir_turn_confirm_fn", default=None
)
_confirmation_lock = threading.Lock()


@contextmanager
def use_confirmation_handler(handler: Callable[[str], bool]) -> Iterator[None]:
    """Route confirmations in the current turn to ``handler`` only."""
    token = _turn_confirm_fn.set(handler)
    try:
        yield
    finally:
        _turn_confirm_fn.reset(token)


def request_confirmation(action: str) -> bool:
    """Ask through the current interface, falling back to ``confirm_fn``."""
    handler = _turn_confirm_fn.get() or confirm_fn
    # ToolNode can execute independent calls concurrently, while Telegram and
    # WhatsApp expose one confirmation prompt at a time.
    with _confirmation_lock:
        return handler(action)

# Returned VERBATIM when the user declines a command. The supervisor loop
# checks for this exact string and ends the turn with a fixed reply, instead
# of handing the decline back to the model (which tends to retry or argue).
USER_DECLINED = (
    "USER DECLINED the command — it was NOT run. The turn was stopped; "
    "do not retry it unless the user asks again."
)


def open_browser(url: str = "") -> str:
    """Open a URL in the operating system's configured default browser."""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        opened = browser_control.open_default(url)
    except Exception as exc:
        return f"Could not open the default browser: {exc}"
    if not opened:
        return "The operating system could not open its default browser."
    return f"Opening {url} in the default browser." if url else "Opening the default browser."


def close_browser() -> str:
    """Close the operating system's configured default browser after confirmation."""
    app = browser_control.default_browser()
    if app is None:
        return "Could not identify the operating system's default browser."
    if not request_confirmation(f"Close {app.name} and all of its open windows?"):
        return USER_DECLINED
    _, message = browser_control.close_default(app)
    return message


def play_on_youtube(query: str) -> str:
    """Find the top YouTube result for a search and open it in the browser.

    yt-dlp's ytsearch resolves the query to a video without an API key; flat
    extraction returns just id/title/url (no formats), so it's quick.
    """
    query = " ".join((query or "").split())
    if not query:
        return "Nothing to play — give a song/video name."

    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True}) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entries = (info or {}).get("entries") or []
    except Exception as exc:
        return f"YouTube search failed: {exc}"
    if not entries:
        return f"No YouTube results for '{query}'."

    top = entries[0]
    url = top.get("url") or f"https://www.youtube.com/watch?v={top.get('id', '')}"
    title = top.get("title") or query
    opened = open_browser(url)
    if not opened.startswith("Opening"):
        return f"Found \"{title}\" ({url}) but couldn't open the browser: {opened}"
    return f"Playing \"{title}\" — {url}"


def open_path(target: str) -> str:
    """Open a file, folder, or URL with the system default app (via xdg-open).

    Opens `target` the way double-clicking it would: a PDF in the PDF viewer, an
    image in the image viewer, a folder in the file manager, a URL in the
    browser. Launched detached so it never blocks.
    """
    target = (target or "").strip()
    if not target:
        return "Nothing to open — give a file, folder, or URL."

    opener = shutil.which("xdg-open")
    if opener is None:
        return "Can't open it: xdg-open isn't available (install the xdg-utils package)."

    # Expand ~ for local paths; a bare domain like "youtube.com" gets an
    # https:// scheme so xdg-open treats it as a URL, not a filename.
    if not target.startswith(("http://", "https://", "mailto:", "file://")):
        looks_like_path = target.startswith(("/", "~", ".")) or "/" in target
        p = _resolve(target)
        if p.exists():
            target = str(p)
        elif looks_like_path:
            return f"No such file or directory: {p}"
        elif "." in target and " " not in target:
            target = "https://" + target  # a bare domain like "youtube.com"

    try:
        code = subprocess.Popen(
            [opener, target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach the opened app so it survives this turn
        ).wait(timeout=5)
    except subprocess.TimeoutExpired:
        return f"Opening {target}."
    except Exception as exc:
        return f"Could not open {target}: {exc}"

    return f"Opening {target}." if code == 0 else f"Could not open {target}."


def bash(command: str, timeout: int = BASH_DEFAULT_TIMEOUT, run_in_background: bool = False) -> str:
    """Run a shell command on the local machine, but only after the user confirms.

    timeout: seconds before a foreground command is killed (clamped to BASH_MAX_TIMEOUT).
    run_in_background: launch the command detached and return immediately, without
    waiting for output — use it for long-running things (a server, a watcher).
    """
    command = (command or "").strip()
    if not command:
        return "No command given."
    if not request_confirmation(command):
        return USER_DECLINED

    if run_in_background:
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # detach so it survives this turn
            )
        except Exception as exc:
            return f"Command failed to start: {exc}"
        return f"Started in the background (PID {proc.pid}): {command}"

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = BASH_DEFAULT_TIMEOUT
    timeout = max(1, min(timeout, BASH_MAX_TIMEOUT))

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s and was killed."
    except Exception as exc:
        return f"Command failed to start: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parts = [f"Exit code: {proc.returncode}"]
    if out:
        parts.append(f"stdout:\n{out}")
    if err:
        parts.append(f"stderr:\n{err}")
    result = "\n".join(parts)
    if len(result) > BASH_MAX_OUTPUT:
        result = result[:BASH_MAX_OUTPUT] + "\n… [output truncated]"
    return result


@tool("open_browser")
def open_browser_tool(
    url: Annotated[str, "Optional URL or site."] = "",
) -> str:
    """Open the default browser, optionally at a URL."""

    return open_browser(url)


@tool("close_browser")
def close_browser_tool() -> str:
    """Close the detected default browser after user confirmation."""

    return close_browser()


@tool("play_on_youtube")
def play_on_youtube_tool(
    query: Annotated[str, "Song, artist, or video to search for."],
) -> str:
    """Find and open the top YouTube result for a requested song or video."""

    return play_on_youtube(query)


@tool("open_path")
def open_path_tool(
    target: Annotated[str, "File path, folder path, or URL."],
) -> str:
    """Open a local path or URL with the operating system's default app."""

    return open_path(target)


@tool("bash")
def bash_tool(
    command: Annotated[str, "Exact shell command."],
    timeout: Annotated[int, "Foreground timeout in seconds, at most 600."] = BASH_DEFAULT_TIMEOUT,
    run_in_background: Annotated[bool, "Launch detached and return immediately."] = False,
) -> str:
    """Run a shell command after confirmation and return its status and output."""

    return bash(command, timeout, run_in_background)


@tool("delegate_to_knowledge")
def delegate_to_knowledge_tool(
    task: Annotated[str, "Knowledge to find, remember, update, or forget."],
) -> str:
    """Recall or change durable memory through its sole owner."""

    return delegate_to_knowledge(task)


@tool("delegate_to_system")
def delegate_to_system_tool(
    task: Annotated[str, "Desired hardware or system end state."],
) -> str:
    """Control or inspect laptop audio, display, connectivity, media playback, battery, or power."""

    return delegate_to_system(task)


@tool("delegate_to_media")
def delegate_to_media_tool(
    task: Annotated[
        str,
        "Complete file/media request with known names, location hints, and exact paths if available.",
    ],
) -> str:
    """Find, list, read, create, edit, append, convert, or generate any local file or media."""

    return delegate_to_media(task)


GENERAL_TOOLS = [
    open_browser_tool,
    close_browser_tool,
    play_on_youtube_tool,
    open_path_tool,
    bash_tool,
]

DELEGATE_TOOLS = [
    delegate_to_knowledge_tool,
    delegate_to_system_tool,
    delegate_to_media_tool,
]

TOOLS = [*GENERAL_TOOLS, *DELEGATE_TOOLS]
