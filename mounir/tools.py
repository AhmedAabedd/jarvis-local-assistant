"""Tool layer — native function-calling tools the model can invoke.

The model is shown SCHEMAS and decides when to call a tool; the agent runs it
through dispatch() and feeds the result back. New tools (files, terminal, …)
just add a function + schema + registry entry here; the agent loop is generic.
"""

from __future__ import annotations

import datetime
import shutil
import subprocess
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


# The supervisor runs on a small, local model with a modest context window, and
# reads files only incidentally (a note, a config, a path the user mentions) —
# heavy code reading is the coder's job. So keep a read to a quick glance the
# model can actually digest; it pages for more with start_line.
MAX_READ_LINES = 300    # lines per read when no range is given
MAX_READ_CHARS = 12000  # hard char ceiling so one read can't flood the context

# Files the model has read this process. edit_file refuses to touch a file that
# wasn't read first, so it never blind-edits text it hasn't actually seen.
_files_read: set[str] = set()
# bash: default timeout (s) to kill a hung command, a hard ceiling the model
# can't exceed, and an output cap so a chatty command can't flood the context.
BASH_DEFAULT_TIMEOUT = 30
BASH_MAX_TIMEOUT = 600
BASH_MAX_OUTPUT = 4000


def _resolve(path: str) -> Path:
    """Expand ~ and make paths predictable before touching the filesystem."""
    return Path(path).expanduser()


def read_file(path: str, start_line: int = 1, end_line: int | None = None) -> str:
    """Read a text file with line numbers (like `cat -n`), optionally a line range.

    Line numbers let you copy an exact block straight into edit_file. Reads up to
    MAX_READ_LINES lines from start_line by default; pass start_line/end_line to
    page through or read a narrow slice of a large file.
    """
    p = _resolve(path)
    try:
        if not p.is_file():
            return f"No such file: {p}"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"Could not read {p}: {exc}"
    if not lines:
        _files_read.add(str(p))
        return "(file is empty)"

    total = len(lines)
    start = max(1, start_line)
    if start > total:
        return f"{p} has {total} lines; start_line {start} is past the end."
    # Default to a page of MAX_READ_LINES rather than the whole file.
    end = min(total, start + MAX_READ_LINES - 1) if end_line is None else min(total, end_line)

    body = "\n".join(f"{start + i:>5}\t{line}" for i, line in enumerate(lines[start - 1:end]))
    if len(body) > MAX_READ_CHARS:
        body = body[:MAX_READ_CHARS] + "\n… [truncated — read a narrower line range]"
    more = f"\n… {total - end} more line(s) below — read again with start_line={end + 1}." if end < total else ""
    _files_read.add(str(p))
    return f"{p} (lines {start}-{end} of {total}):\n{body}{more}"


def write_file(path: str, content: str) -> str:
    """Write text to a file, creating parent folders. Overwrites if it exists."""
    p = _resolve(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except Exception as exc:
        return f"Could not write {p}: {exc}"
    _files_read.add(str(p))  # we just wrote it, so it counts as "seen" for edit_file
    return f"Wrote {len(content)} characters to {p}."


def edit_file(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Surgically replace exact text in a file, without rewriting the whole thing.

    old_str must match EXACTLY (whitespace included). It must be unique unless
    replace_all is set. Read the file first to copy the exact text.
    """
    p = _resolve(path)
    if not p.is_file():
        return f"No such file: {p}"
    if str(p) not in _files_read:
        return f"Read {p} with read_file before editing it, so you edit its real current text."
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {p}: {exc}"

    count = text.count(old_str)
    if count == 0:
        return f"Text not found in {p}. Read the file to copy the exact text."
    if count > 1 and not replace_all:
        return (
            f"Found {count} matches in {p} — too ambiguous. Make old_str more "
            f"specific (add surrounding lines), or set replace_all to change all."
        )

    new_text = text.replace(old_str, new_str) if replace_all else text.replace(old_str, new_str, 1)
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as exc:
        return f"Could not write {p}: {exc}"
    return f"Edited {p} — replaced {count if replace_all else 1} occurrence(s)."


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


def bash(command: str, timeout: int = BASH_DEFAULT_TIMEOUT, run_in_background: bool = False) -> str:
    """Run a shell command on the local machine, but only after the user confirms.

    timeout: seconds before a foreground command is killed (clamped to BASH_MAX_TIMEOUT).
    run_in_background: launch the command detached and return immediately, without
    waiting for output — use it for long-running things (a server, a watcher).
    """
    command = (command or "").strip()
    if not command:
        return "No command given."
    if not confirm_fn(command):
        return "Command cancelled by the user — not run."

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


def _plain_text(msg) -> str:
    """Pull readable plain text out of an email message.

    Prefer a text/plain part; fall back to stripping tags off the HTML.
    """
    import re
    from html import unescape

    body = ""
    if msg.is_multipart():
        # Grab the first text/plain part; remember HTML as a fallback.
        html = ""
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            try:
                text = part.get_payload(decode=True).decode(
                    part.get_content_charset() or "utf-8", "replace"
                )
            except Exception:
                continue
            if ctype == "text/plain" and not body:
                body = text
            elif ctype == "text/html" and not html:
                html = text
        if not body:
            body = html
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", "replace"
            )
        except Exception:
            body = msg.get_payload() or ""

    # If we ended up with HTML, strip it down to text.
    if "<" in body and ">" in body:
        body = re.sub(r"(?is)<(script|style).*?</\1>", " ", body)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = unescape(body)

    # Collapse the blank-line/whitespace storm HTML leaves behind.
    lines = [ln.strip() for ln in body.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def read_emails(count: int = 5, unread_only: bool = False, sender: str = "") -> str:
    """Read recent emails from the user's inbox, as plain text.

    count: how many of the most recent matching emails to fetch.
    unread_only: only return unread emails.
    sender: only emails from this address/name (substring match).
    """
    if not (config.SMTP_USER and config.SMTP_PASS):
        return (
            "Email isn't set up: the MOUNIR_SMTP_USER and MOUNIR_SMTP_PASS "
            "environment variables (a Gmail App Password) aren't configured."
        )

    import imaplib
    import email as emaillib
    from email.header import decode_header, make_header

    count = max(1, min(int(count), 20))
    # Split a fixed total budget across the emails: ask for 1 and get the full
    # body, ask for 10 and get short snippets. Keeps total output bounded for
    # the small local model either way.
    per_email_cap = max(1500, 12000 // count)

    criteria = []
    if unread_only:
        criteria.append("UNSEEN")
    if sender.strip():
        criteria += ["FROM", f'"{sender.strip()}"']
    if not criteria:
        criteria = ["ALL"]

    try:
        with imaplib.IMAP4_SSL(config.IMAP_HOST) as imap:
            imap.login(config.SMTP_USER, config.SMTP_PASS)
            imap.select("INBOX", readonly=True)  # readonly: don't mark as read
            status, data = imap.search(None, *criteria)
            if status != "OK":
                return "Could not search the inbox."
            ids = data[0].split()
            if not ids:
                return "No matching emails found."

            out = []
            for num in reversed(ids[-count:]):  # newest first
                status, raw = imap.fetch(num, "(RFC822)")
                if status != "OK" or not raw or not raw[0]:
                    continue
                msg = emaillib.message_from_bytes(raw[0][1])
                frm = str(make_header(decode_header(msg.get("From", "?"))))
                subj = str(make_header(decode_header(msg.get("Subject", "(no subject)"))))
                date = msg.get("Date", "?")
                text = _plain_text(msg)
                if len(text) > per_email_cap:
                    text = text[:per_email_cap] + "\n… (read this email alone for the rest)"
                out.append(f"From: {frm}\nSubject: {subj}\nDate: {date}\n\n{text}")
    except Exception as exc:
        return f"Failed to read email: {exc}"

    return ("\n\n" + "—" * 40 + "\n\n").join(out)


# What the model sees. Descriptions matter — they're how it decides to call.
SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a text file on the local machine, shown with line numbers. "
                "Use it to look at a file before answering or before editing it. "
                "Pass start_line/end_line to read just a range of a large file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (~ for the home folder is fine).",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "First line to read (1-based). Optional; defaults to the start.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Last line to read (inclusive). Optional; defaults to the end of the file.",
                    },
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
            "name": "edit_file",
            "description": (
                "Surgically change part of an EXISTING text file — a line, a word, "
                "a block — without rewriting the whole file. Read the file first to "
                "copy the exact text into old_str (it must match exactly and be "
                "unique unless replace_all is set)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to edit.",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "The exact text to find and replace (must be unique in the file unless replace_all is true).",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "The text to replace it with.",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence instead of requiring a unique match. Defaults to false.",
                    },
                },
                "required": ["path", "old_str", "new_str"],
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
            "name": "bash",
            "description": (
                "Run a shell command on the local machine and return its exit code and output."
                "Set run_in_background for long-running commands (a server, a "
                "watcher): they launch detached and return immediately without output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The exact shell command to run.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds before a foreground command is killed (default 30, max 600).",
                    },
                    "run_in_background": {
                        "type": "boolean",
                        "description": "Launch detached and return immediately without waiting for output. Use for long-running commands.",
                    },
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
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": (
                "Read recent emails from the user's own inbox, returned as plain "
                "text (From, Subject, Date, and the message). Use it to check the "
                "inbox, summarize new mail, or find a message. Filter with "
                "unread_only or sender when the user is specific. To read ONE "
                "email's full body, call again with count=1 and a sender filter — "
                "fewer emails means each is returned in full instead of clipped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "How many of the most recent matching emails to read (default 5, max 20).",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Only return unread emails. Default false.",
                    },
                    "sender": {
                        "type": "string",
                        "description": "Only emails from this address or name (substring match). Omit for all senders.",
                    },
                },
                "required": [],
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
    "edit_file": edit_file,
    "list_directory": list_directory,
    "open_browser": open_browser,
    "open_path": open_path,
    "bash": bash,
    "send_email": send_email,
    "read_emails": read_emails,
    "delegate_to_coder": delegate_to_coder,
    "delegate_to_researcher": delegate_to_researcher,
}


def dispatch(name: str, arguments: dict) -> str:
    """Run a tool by name with the given arguments, returning a text result."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        return fn(**arguments)
    except TypeError as exc:
        return f"Bad arguments for {name}: {exc}"
    except Exception as exc:
        return f"Tool {name} failed: {exc}"
