"""Catalog and restricted runner for Mounir's built-in specialists.

Heartbeat uses this catalog to expose the same real tool schemas as the normal
specialists while allowing only explicitly selected read-only tools at runtime.
"""

from __future__ import annotations

from importlib import import_module


_BUILTINS = {
    "media": {
        "name": "Media",
        "module": "mounir.specialists.media",
        "safe_tools": {"load_media", "sample_frames", "find_media"},
    },
    "knowledge": {
        "name": "Knowledge",
        "module": "mounir.specialists.knowledge",
        "safe_tools": {"list_knowledge", "read_knowledge", "search_knowledge"},
    },
    "system": {
        "name": "System",
        "module": "mounir.specialists.system",
        "safe_tools": {"system_status"},
    },
}


def capabilities() -> list[dict]:
    """Return built-in tool schemas with heartbeat safety metadata."""
    result: list[dict] = []
    for key, definition in _BUILTINS.items():
        module = import_module(definition["module"])
        safe = definition["safe_tools"]
        tools = []
        for schema in module.TOOLS:
            function = schema.get("function") or {}
            name = str(function.get("name") or "").strip()
            if not name:
                continue
            tools.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "requires_confirmation": name not in safe,
                }
            )
        result.append(
            {
                "id": f"builtin:{key}",
                "key": f"builtin:{key}",
                "builtin_key": key,
                "kind": "builtin",
                "name": definition["name"],
                "connection_status": "built_in",
                "tools": tools,
            }
        )
    return result


def run(key: str, task: str, allowed_tools: list[str]) -> str:
    """Run one built-in specialist with a code-enforced tool allowlist."""
    definition = _BUILTINS.get(str(key or "").strip())
    if definition is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    safe = definition["safe_tools"]
    selected = [name for name in allowed_tools if name in safe]
    if not selected:
        raise ValueError(f"no safe tools selected for {definition['name']}")
    module = import_module(definition["module"])
    return module.run(task, allowed_tools=selected)
