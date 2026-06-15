"""Tool layer — native function-calling tools the model can invoke.

The model is shown SCHEMAS and decides when to call a tool; the agent runs it
through dispatch() and feeds the result back. New tools (files, terminal, …)
just add a function + schema + registry entry here; the agent loop is generic.
"""

from __future__ import annotations

import sys

WEB_SEARCH_MAX_RESULTS = 5


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
    }
]

_REGISTRY = {"web_search": web_search}


def dispatch(name: str, arguments: dict) -> str:
    """Run a tool by name with the given arguments, returning a text result."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    # Printed (not yielded) so it shows on screen but is never spoken by TTS.
    print(f"  [🔍 {name}: {arguments}]", file=sys.stderr, flush=True)
    try:
        return fn(**arguments)
    except TypeError as exc:
        return f"Bad arguments for {name}: {exc}"
    except Exception as exc:
        return f"Tool {name} failed: {exc}"
