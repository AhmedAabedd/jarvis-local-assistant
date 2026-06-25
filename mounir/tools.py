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

WEB_SEARCH_MAX_RESULTS = 5

# Specialist agents (lazy imports — only load ollama when actually called)
def delegate_to_coder(task: str, context: str = "") -> str:
    """Ask the coder agent to write or fix code for a given task."""
    from .specialists.coder import run
    print(f"  [💻 coder delegated]", file=sys.stderr, flush=True)
    return run(task)


# How far back to restrict results, mapped to what ddgs/DuckDuckGo accepts.
_RECENCY = {"day": "d", "week": "w", "month": "m", "year": "y"}
# fetch_url: network timeout, how much page text to keep, and a real browser
# User-Agent (many sites reject the default python one).
FETCH_TIMEOUT = 15
FETCH_URL_MAX_CHARS = 6000
_FETCH_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
# Cap how much of a file we read back so a huge file can't blow up the context.
MAX_READ_CHARS = 20000
# run_command: kill a hung command, and cap its output so it can't flood context.
RUN_COMMAND_TIMEOUT = 30
RUN_COMMAND_MAX_OUTPUT = 4000


def web_search(
    query: str, max_results: int = WEB_SEARCH_MAX_RESULTS, recency: str = ""
) -> str:
    """Search the web (DuckDuckGo) and return ranked title/snippet/URL results.

    `recency` limits how old results may be: "day", "week", "month", or "year"
    (empty = no limit). Use it for current events so stale pages don't win.
    """
    timelimit = _RECENCY.get(recency.strip().lower()) if recency else None
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(
                ddgs.text(query, max_results=max_results, timelimit=timelimit)
            )
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
    lines.append("\nTo read any result in full, call fetch_url with its URL.")
    return "\n".join(lines)


def fetch_url(url: str) -> str:
    """Download a web page (or any URL) and return its readable text, no HTML."""
    url = (url or "").strip()
    if not url:
        return "No URL given."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return "Page fetch unavailable: install 'requests' and 'beautifulsoup4'."
    try:
        resp = requests.get(
            url, timeout=FETCH_TIMEOUT, headers={"User-Agent": _FETCH_UA}
        )
        resp.raise_for_status()
    except Exception as exc:
        return f"Could not fetch {url}: {exc}"

    soup = BeautifulSoup(resp.text, "html.parser")
    # Drop boilerplate so the model reads content, not menus and scripts.
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
        tag.decompose()
    text = " ".join(soup.get_text(separator=" ").split())
    if not text:
        return f"Fetched {url} but found no readable text."

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    head = f"{title}\n{url}\n\n" if title else f"{url}\n\n"
    body = text[:FETCH_URL_MAX_CHARS]
    if len(text) > FETCH_URL_MAX_CHARS:
        body += " … [truncated]"
    return head + body


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


def send_email(to: str, subject: str, body: str) -> str:
    """Send an email from the user's account, but only after the user confirms."""
    to = (to or "").strip()
    if not to:
        return "No recipient given."
    if not (config.SMTP_USER and config.SMTP_PASS):
        return (
            "Email isn't set up: the MOUNIR_SMTP_USER and MOUNIR_SMTP_PASS "
            "environment variables (a Gmail App Password) aren't configured."
        )

    preview = f"To: {to}\nSubject: {subject}\n\n{body}"
    if not confirm_fn(f"send this email?\n{preview}"):
        return "Email cancelled by the user — not sent."

    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = config.SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(config.SMTP_USER, config.SMTP_PASS)
            server.send_message(msg)
    except Exception as exc:
        return f"Failed to send email: {exc}"
    return f"Email sent to {to}."


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
                    },
                    "recency": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "description": (
                            "Optional. Limit results to the last day/week/month/"
                            "year. Use it for current events; omit otherwise."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Download a web page and return its readable text. Use it to read "
                "a search result in full instead of relying on the short snippet, "
                "or to read any URL the user gives you."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL of the page to read.",
                    }
                },
                "required": ["url"],
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
]

_REGISTRY = {
    "web_search": web_search,
    "fetch_url": fetch_url,
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "open_browser": open_browser,
    "run_command": run_command,
    "send_email": send_email,
    "delegate_to_coder": delegate_to_coder,
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
