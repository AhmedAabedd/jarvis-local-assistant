"""Tool layer — native function-calling tools the model can invoke.

The model is shown SCHEMAS and decides when to call a tool; the agent runs it
through dispatch() and feeds the result back. New tools (files, terminal, …)
just add a function + schema + registry entry here; the agent loop is generic.
"""

from __future__ import annotations

import datetime
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

# Specialist agents (lazy imports — only load the specialist when actually
# called). These are reached via handoff in the graph; the registry entries are
# fallbacks and aren't normally dispatched.
def delegate_to_coder(task: str, context: str = "") -> str:
    """Ask the coder agent to write or fix code for a given task."""
    from .specialists.coder import run
    return run(task)


def delegate_to_researcher(task: str) -> str:
    """Ask the researcher agent to look something up on the web."""
    from .specialists.researcher import run
    return run(task)


# Cap how much of a file we read back so a huge file can't blow up the context.
MAX_READ_CHARS = 20000
# run_command: kill a hung command, and cap its output so it can't flood context.
RUN_COMMAND_TIMEOUT = 30
RUN_COMMAND_MAX_OUTPUT = 4000


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


# Browser binaries to try, in order; first one on PATH wins.
_BROWSERS = [
    "google-chrome-stable",
    "google-chrome",
    "chromium-browser",
    "chromium",
    "firefox",
    "brave-browser",
]


def open_browser(url: str = "") -> str:
    """Open the web browser, optionally to a URL (a new tab if it's running).

    Launched detached so it never blocks. Pass nothing to just open the browser,
    or a URL/site (the model supplies "youtube.com" for "open youtube").
    """
    url = (url or "").strip()
    browser = next((shutil.which(b) for b in _BROWSERS if shutil.which(b)), None)
    if browser is None:
        return "No web browser found on this machine."

    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    argv = [browser, url] if url else [browser]
    try:
        subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach so it survives this turn
        )
    except Exception as exc:
        return f"Could not open the browser: {exc}"
    return f"Opening {url} in the browser." if url else "Opening the browser."


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


def run_command(command: str) -> str:
    """Run a shell command on the local machine, but only after the user confirms."""
    command = (command or "").strip()
    if not command:
        return "No command given."
    if not confirm_fn(command):
        return "Command cancelled by the user — not run."

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=RUN_COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"Command timed out after {RUN_COMMAND_TIMEOUT}s and was killed."
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
    if len(result) > RUN_COMMAND_MAX_OUTPUT:
        result = result[:RUN_COMMAND_MAX_OUTPUT] + "\n… [output truncated]"
    return result


def send_email(to: str, subject: str, body: str, attachments: list[str] | None = None) -> str:
    """Send an email from the user's account, but only after the user confirms.

    attachments: optional list of file paths to attach (PDF, image, …).
    """
    to = (to or "").strip()
    if not to:
        return "No recipient given."
    if not (config.SMTP_USER and config.SMTP_PASS):
        return (
            "Email isn't set up: the MOUNIR_SMTP_USER and MOUNIR_SMTP_PASS "
            "environment variables (a Gmail App Password) aren't configured."
        )

    # Resolve attachments up front so we fail before sending, not after.
    files = []
    for raw in attachments or []:
        p = _resolve(raw)
        if not p.is_file():
            return f"Attachment not found: {p}"
        files.append(p)

    preview = f"To: {to}\nSubject: {subject}\n\n{body}"
    if files:
        preview += "\n\n[Attachments: " + ", ".join(p.name for p in files) + "]"
    if not confirm_fn(f"send this email?\n{preview}"):
        return "Email cancelled by the user — not sent."

    import smtplib
    import mimetypes
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = config.SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for p in files:
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, subtype = ctype.split("/", 1) if ctype else ("application", "octet-stream")
        try:
            msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
        except Exception as exc:
            return f"Could not attach {p}: {exc}"
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)
    except Exception as exc:
        return f"Failed to send email: {exc}"
    if files:
        return f"Email sent to {to} with {len(files)} attachment(s)."
    return f"Email sent to {to}."


# What the model sees. Descriptions matter — they're how it decides to call.
SCHEMAS = [
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
    {
        "type": "function",
        "function": {
            "name": "open_browser",
            "description": (
                "Open the web browser. Pass a URL or site to open it in a tab "
                "Use this for anything web/browser."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Optional URL or site to open. Omit to just open the browser.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_path",
            "description": (
                "Open a file, folder, or URL with the system's DEFAULT app, the "
                "way double-clicking it would (PDF viewer, image viewer, file "
                "manager, browser…). Use this to open a document, picture, or "
                "directory the user names — e.g. 'open my CV', 'open the "
                "Downloads folder'. For plain web pages prefer open_browser."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "A file path, folder path (~ allowed), or URL to open.",
                    }
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command on the local machine and return its exit code "
                "and output. Use it to DO things — move or rename files, manage "
                "processes, check disk, run scripts. The user confirms before it "
                "runs. Does not open the browser; use open_browser for that."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send an email from the user's own account. The user confirms "
                "before it's sent, so fill in the exact recipient, subject, and "
                "body. Write the body yourself unless the user dictates it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email address.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "The subject line.",
                    },
                    "body": {
                        "type": "string",
                        "description": "The full message body.",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of file paths to attach (PDF, image, etc.). Omit if none.",
                    },
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
]

SCHEMAS += [
    {
        "type": "function",
        "function": {
            "name": "delegate_to_coder",
            "description": (
                "Delegate ANY coding task to the coder agent: writing new code, "
                "editing existing files, debugging, refactoring. "
                "The coder reads and writes files itself — you never see the code. "
                "You only get back a short summary of what was done. "
                "Include the full file path(s) in the task description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "What code to write, fix, or explain. Be specific about language, requirements, and any constraints.",
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_researcher",
            "description": (
                "Delegate ANY web lookup to the researcher agent: current events, "
                "facts that may have changed, prices, documentation, product or "
                "tech comparisons — anything you'd need the internet for. The "
                "researcher searches, reads pages, cross-checks, and returns a "
                "concise report WITH sources. You have no web tools yourself, so "
                "always delegate lookups here. State exactly what you need to know."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The research question or topic, with any specifics (timeframe, what to compare, what detail you need).",
                    },
                },
                "required": ["task"],
            },
        },
    },
]

_REGISTRY = {
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "open_browser": open_browser,
    "open_path": open_path,
    "run_command": run_command,
    "send_email": send_email,
    "delegate_to_coder": delegate_to_coder,
    "delegate_to_researcher": delegate_to_researcher,
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
