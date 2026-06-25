"""Researcher agent — self-contained web research specialist.

The orchestrator calls run(task) and gets back a structured research report
WITH sources. The researcher does the whole search → read → cross-check →
synthesize loop in its OWN context, so raw page text never bloats the
supervisor — only the final report crosses back.

Web tools are ISOLATED to this agent. The supervisor has no web access and
must delegate any lookup here.
"""

from __future__ import annotations

import json

from .. import config, llm
from .. import trace

MAX_TOOL_ROUNDS = 8

# How far back to restrict results, mapped to what ddgs/DuckDuckGo accepts.
_RECENCY = {"day": "d", "week": "w", "month": "m", "year": "y"}
WEB_SEARCH_MAX_RESULTS = 5
FETCH_TIMEOUT = 15
FETCH_URL_MAX_CHARS = 6000
_FETCH_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SYSTEM_PROMPT = """\
You are an elite research analyst working as Mounir's dedicated researcher.
You answer questions by searching the web, reading real pages, and cross-
checking sources — never from memory alone for anything that can change.

Your tools:
- web_search(query, recency): ranked results. Use recency ("day"/"week"/
  "month"/"year") for anything time-sensitive.
- search_news(query): recent / breaking news items with dates and outlets.
- fetch_url(url): download a page and read its full text.

METHOD
- Start broad with web_search (or search_news for current events), then narrow.
- Do NOT trust snippets for important claims — fetch_url the most promising
  results and read the actual content.
- Cross-check key facts across at least two independent sources.
- Refine the query and search again if the first results are weak.
- Stop as soon as you can answer confidently with sources. Don't over-search.

FINAL REPORT (MANDATORY)
Your last message is read by the SUPERVISOR, not the user. Return EXACTLY this
structure and nothing after it:

## Research Report
**Question:** <one-line restatement of what was asked>
**Answer:** <the direct, synthesized answer — a few sentences or tight bullets>

**Key findings:**
- <finding, tied to a source> [1]
- <finding> [2]

**Sources:**
[1] <page title> — <url>
[2] <page title> — <url>

**Confidence:** high | medium | low — <one line why>

Report rules:
- ALWAYS cite sources. Every non-obvious claim gets an inline [n] tied to the
  Sources list. If you have no source for a claim, don't state it as fact.
- List the REAL urls you actually searched or fetched — never invent links.
- If sources conflict, say so in Answer and note which you trust and why.
- If you couldn't verify something, say so plainly. Never fabricate.
- Keep it tight. No fluff, no "certainly!".

EXAMPLE REPORT
## Research Report
**Question:** What's the latest stable Python version and its headline feature?
**Answer:** Python 3.13 is the latest stable release (Oct 2024). Its headline
change is an experimental free-threaded build (PEP 703) that can run without
the GIL, alongside a new interactive REPL. [1][2]

**Key findings:**
- 3.13 ships an experimental no-GIL "free-threaded" mode behind a build flag. [1]
- The rewritten REPL adds colors, multiline editing, and history. [2]

**Sources:**
[1] What's New In Python 3.13 — https://docs.python.org/3/whatsnew/3.13.html
[2] Python 3.13.0 release — https://www.python.org/downloads/release/python-3130/

**Confidence:** high — official python.org documentation.
"""


# ---------------------------------------------------------------------------
# Isolated web tools
# ---------------------------------------------------------------------------

def web_search(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS, recency: str = "") -> str:
    """Search the web (DuckDuckGo) and return ranked title/snippet/URL results."""
    timelimit = _RECENCY.get(recency.strip().lower()) if recency else None
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, timelimit=timelimit))
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
    lines.append("\nRead any result in full with fetch_url before trusting it.")
    return "\n".join(lines)


def search_news(query: str, max_results: int = 5) -> str:
    """Search recent news. Returns dated headlines with their outlet and URL."""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
    except ImportError:
        return "News search unavailable: the 'ddgs' package isn't installed."
    except Exception as exc:
        return f"News search failed: {exc}"

    if not results:
        return f"No news found for '{query}'."

    lines = [f"News results for '{query}':"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        source = r.get("source", "").strip()
        date = r.get("date", "").strip()
        body = r.get("body", "").strip()
        url = r.get("url", "").strip()
        lines.append(f"{i}. {title} — {source} ({date})\n   {body}\n   ({url})")
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
        resp = requests.get(url, timeout=FETCH_TIMEOUT, headers={"User-Agent": _FETCH_UA})
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


# ---------------------------------------------------------------------------
# Tool schemas + dispatch
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current events, facts, prices, docs, or "
                "anything that may have changed. Returns ranked title/snippet/URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "A concise search-engine-style query."},
                    "recency": {
                        "type": "string",
                        "enum": ["day", "week", "month", "year"],
                        "description": "Optional. Limit to the last day/week/month/year for time-sensitive topics.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "Search recent news for a topic. Returns dated headlines with their outlet and URL. Use for current/breaking events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to find news about."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Download a web page and return its readable text. Use it to read a result in full instead of trusting the snippet.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The full URL of the page to read."},
                },
                "required": ["url"],
            },
        },
    },
]

_REGISTRY = {
    "web_search": web_search,
    "search_news": search_news,
    "fetch_url": fetch_url,
}


def _dispatch(name: str, arguments: dict) -> str:
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
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
    """Run the researcher on a task. Returns its structured report with sources."""
    if not config.NVIDIA_API_KEY:
        return "Researcher failed: NVIDIA_API_KEY is not set."

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            message = llm.nvidia_chat(messages, tools=TOOLS, model=config.RESEARCHER_MODEL)
        except Exception as exc:
            return f"Researcher failed: {exc}"

        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            trace.event(f"{round_num + 1} round(s)")
            return content.strip() or "No findings."

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        )

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            result = _dispatch(name, args)
            trace.tool(name, args, result)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", "call_0"),
                    "content": result,
                }
            )

    return "Researcher reached max tool rounds — partial findings only."
