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

from . import browser_control, tool_outcome

# Specialist agents are reached through LangGraph handoffs. These functions
# remain defensive fallbacks for direct tool invocation.
def delegate_to_knowledge(task: str) -> str:
    """Hand a durable-memory lookup or change to the Knowledge agent."""
    from . import db
    if not db.is_builtin_agent_enabled("knowledge"):
        return "The Knowledge agent is inactive and cannot be used."
    from .specialists.knowledge import run
    return run(task)


def delegate_to_computer(task: str) -> str:
    """Hand visible desktop interaction to the Computer agent."""
    from . import db
    if not db.is_builtin_agent_enabled("computer"):
        return "The Computer agent is inactive and cannot be used."
    from .specialists.computer import run
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


def _delegate_builtin(key: str, task: str) -> str:
    from . import builtin_agents

    try:
        return builtin_agents.run_direct(key, task)
    except ValueError as exc:
        return str(exc)


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


def _open_browser_outcome(url: str = "") -> tool_outcome.ToolOutcome:
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        opened = browser_control.open_default(url)
    except Exception as exc:
        return tool_outcome.ToolOutcome.error(
            f"Could not open the default browser: {exc}"
        )
    if not opened:
        return tool_outcome.ToolOutcome.error(
            "The operating system could not open its default browser."
        )
    return tool_outcome.ToolOutcome.success(
        f"Opening {url} in the default browser."
        if url
        else "Opening the default browser."
    )


def open_browser(url: str = "") -> str:
    """Open a URL in the operating system's configured default browser."""

    return str(_open_browser_outcome(url).content)


def _close_browser_outcome() -> tool_outcome.ToolOutcome:
    app = browser_control.default_browser()
    if app is None:
        return tool_outcome.ToolOutcome.error(
            "Could not identify the operating system's default browser."
        )
    if not request_confirmation(f"Close {app.name} and all of its open windows?"):
        return tool_outcome.ToolOutcome.declined(USER_DECLINED)
    closed, message = browser_control.close_default(app)
    return (
        tool_outcome.ToolOutcome.success(message)
        if closed
        else tool_outcome.ToolOutcome.error(message)
    )


def close_browser() -> str:
    """Close the operating system's configured default browser after confirmation."""

    return str(_close_browser_outcome().content)


def _play_on_youtube_outcome(query: str) -> tool_outcome.ToolOutcome:
    """Find the top YouTube result for a search and open it in the browser.

    yt-dlp's ytsearch resolves the query to a video without an API key; flat
    extraction returns just id/title/url (no formats), so it's quick.
    """
    query = " ".join((query or "").split())
    if not query:
        return tool_outcome.ToolOutcome.error(
            "Nothing to play — give a song/video name."
        )

    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL(
            {"quiet": True, "no_warnings": True, "extract_flat": True}
        ) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entries = (info or {}).get("entries") or []
    except Exception as exc:
        return tool_outcome.ToolOutcome.error(f"YouTube search failed: {exc}")
    if not entries:
        return tool_outcome.ToolOutcome.error(
            f"No YouTube results for '{query}'."
        )

    top = entries[0]
    url = top.get("url") or f"https://www.youtube.com/watch?v={top.get('id', '')}"
    title = top.get("title") or query
    opened = _open_browser_outcome(url)
    if opened.status != "success":
        return tool_outcome.ToolOutcome.error(
            f"Found \"{title}\" ({url}) but couldn't open the browser: "
            f"{opened.content}"
        )
    return tool_outcome.ToolOutcome.success(f"Playing \"{title}\" — {url}")


def play_on_youtube(query: str) -> str:
    """Find the top YouTube result for a search and open it in the browser."""

    return str(_play_on_youtube_outcome(query).content)


def _open_path_outcome(target: str) -> tool_outcome.ToolOutcome:
    """Open a file, folder, or URL with the system default app (via xdg-open).

    Opens `target` the way double-clicking it would: a PDF in the PDF viewer, an
    image in the image viewer, a folder in the file manager, a URL in the
    browser. Launched detached so it never blocks.
    """
    target = (target or "").strip()
    if not target:
        return tool_outcome.ToolOutcome.error(
            "Nothing to open — give a file, folder, or URL."
        )

    opener = shutil.which("xdg-open")
    if opener is None:
        return tool_outcome.ToolOutcome.error(
            "Can't open it: xdg-open isn't available (install the xdg-utils package)."
        )

    # Expand ~ for local paths; a bare domain like "youtube.com" gets an
    # https:// scheme so xdg-open treats it as a URL, not a filename.
    if not target.startswith(("http://", "https://", "mailto:", "file://")):
        looks_like_path = target.startswith(("/", "~", ".")) or "/" in target
        p = _resolve(target)
        if p.exists():
            target = str(p)
        elif looks_like_path:
            return tool_outcome.ToolOutcome.error(
                f"No such file or directory: {p}"
            )
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
        return tool_outcome.ToolOutcome.success(f"Opening {target}.")
    except Exception as exc:
        return tool_outcome.ToolOutcome.error(f"Could not open {target}: {exc}")

    return (
        tool_outcome.ToolOutcome.success(f"Opening {target}.")
        if code == 0
        else tool_outcome.ToolOutcome.error(f"Could not open {target}.")
    )


def open_path(target: str) -> str:
    """Open a file, folder, or URL with the system default application."""

    return str(_open_path_outcome(target).content)


def _bash_outcome(
    command: str,
    timeout: int = BASH_DEFAULT_TIMEOUT,
    run_in_background: bool = False,
) -> tool_outcome.ToolOutcome:
    """Run a shell command on the local machine, but only after the user confirms.

    timeout: seconds before a foreground command is killed (clamped to BASH_MAX_TIMEOUT).
    run_in_background: launch the command detached and return immediately, without
    waiting for output — use it for long-running things (a server, a watcher).
    """
    command = (command or "").strip()
    if not command:
        return tool_outcome.ToolOutcome.error("No command given.")
    if not request_confirmation(command):
        return tool_outcome.ToolOutcome.declined(USER_DECLINED)

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
            return tool_outcome.ToolOutcome.error(
                f"Command failed to start: {exc}"
            )
        return tool_outcome.ToolOutcome.success(
            f"Started in the background (PID {proc.pid}): {command}"
        )

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
        return tool_outcome.ToolOutcome.error(
            f"Command timed out after {timeout}s and was killed."
        )
    except Exception as exc:
        return tool_outcome.ToolOutcome.error(f"Command failed to start: {exc}")

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
    return (
        tool_outcome.ToolOutcome.success(result)
        if proc.returncode == 0
        else tool_outcome.ToolOutcome.error(result)
    )


def bash(
    command: str,
    timeout: int = BASH_DEFAULT_TIMEOUT,
    run_in_background: bool = False,
) -> str:
    """Run a confirmed shell command and return its human-readable result."""

    return str(_bash_outcome(command, timeout, run_in_background).content)


@tool("open_browser", response_format="content_and_artifact")
def open_browser_tool(
    url: Annotated[str, "Optional URL or site."] = "",
):
    """Open the default browser, optionally at a URL."""

    return _open_browser_outcome(url).as_tool_response()


@tool("close_browser", response_format="content_and_artifact")
def close_browser_tool():
    """Close the detected default browser after user confirmation."""

    return _close_browser_outcome().as_tool_response()


@tool("play_on_youtube", response_format="content_and_artifact")
def play_on_youtube_tool(
    query: Annotated[str, "Song, artist, or video to search for."],
):
    """Find and open the top YouTube result for a requested song or video."""

    return _play_on_youtube_outcome(query).as_tool_response()


@tool("open_path", response_format="content_and_artifact")
def open_path_tool(
    target: Annotated[str, "File path, folder path, or URL."],
):
    """Open a local path or URL with the operating system's default app."""

    return _open_path_outcome(target).as_tool_response()


@tool("bash", response_format="content_and_artifact")
def bash_tool(
    command: Annotated[str, "Exact shell command."],
    timeout: Annotated[
        int, "Foreground timeout in seconds, at most 600."
    ] = BASH_DEFAULT_TIMEOUT,
    run_in_background: Annotated[
        bool, "Launch detached and return immediately."
    ] = False,
):
    """Run a shell command after confirmation and return its status and output."""

    return _bash_outcome(command, timeout, run_in_background).as_tool_response()


@tool("delegate_to_knowledge")
def delegate_to_knowledge_tool(
    task: Annotated[str, "Knowledge to find, remember, update, or forget."],
) -> str:
    """Recall or change durable memory through its sole owner."""

    return delegate_to_knowledge(task)


@tool("delegate_to_computer")
def delegate_to_computer_tool(
    task: Annotated[
        str,
        "Visible desktop task, including the target application and desired verified end state.",
    ],
) -> str:
    """Observe or control visible desktop applications with the Computer specialist."""

    return delegate_to_computer(task)


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


@tool("delegate_to_facebook")
def delegate_to_facebook_tool(
    task: Annotated[str, "Facebook Page or Meta Ads request, including known account names or IDs."],
) -> str:
    """Use the official Facebook Pages and Meta Ads specialist."""

    return _delegate_builtin("facebook", task)


@tool("delegate_to_messenger")
def delegate_to_messenger_tool(
    task: Annotated[str, "Facebook Page Messenger connection or policy request."],
) -> str:
    """Use the official Messenger Platform specialist; personal accounts and cold DMs are excluded."""

    return _delegate_builtin("messenger", task)


@tool("delegate_to_instagram")
def delegate_to_instagram_tool(
    task: Annotated[str, "Instagram professional-account request, including known account names or IDs."],
) -> str:
    """Use the official Instagram professional-account specialist."""

    return _delegate_builtin("instagram", task)


@tool("delegate_to_threads")
def delegate_to_threads_tool(
    task: Annotated[str, "Threads profile request, including known account names or IDs."],
) -> str:
    """Use the official Threads API specialist."""

    return _delegate_builtin("threads", task)


@tool("delegate_to_whatsapp")
def delegate_to_whatsapp_tool(
    task: Annotated[str, "WhatsApp Business inbox request, including known connection, contact, or message IDs."],
) -> str:
    """Use the official WhatsApp Business inbox specialist; this is separate from the private channel."""

    return _delegate_builtin("whatsapp", task)


GENERAL_TOOLS = [
    open_browser_tool,
    close_browser_tool,
    play_on_youtube_tool,
    open_path_tool,
    bash_tool,
]

DELEGATE_TOOLS = [
    delegate_to_computer_tool,
    delegate_to_knowledge_tool,
    delegate_to_system_tool,
    delegate_to_media_tool,
    delegate_to_facebook_tool,
    delegate_to_messenger_tool,
    delegate_to_instagram_tool,
    delegate_to_threads_tool,
    delegate_to_whatsapp_tool,
]

TOOLS = [*GENERAL_TOOLS, *DELEGATE_TOOLS]
