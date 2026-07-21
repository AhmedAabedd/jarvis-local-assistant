"""Catalog and restricted runner for Mounir's built-in specialists.

Heartbeat uses this catalog to expose the same real tool schemas as the normal
specialists while allowing only explicitly selected read-only tools at runtime.
"""

from __future__ import annotations

from importlib import import_module

from . import config


_BUILTINS = {
    "media": {
        "name": "Media",
        "module": "mounir.specialists.media",
        "provider": "NVIDIA",
        "default_model": config.MEDIA_MODEL,
        "description": "Understands images, PDFs, audio, and video files on this computer.",
        "safe_tools": {"load_media", "sample_frames", "find_media"},
    },
    "knowledge": {
        "name": "Knowledge",
        "module": "mounir.specialists.knowledge",
        "provider": "Gemini",
        "default_model": config.KNOWLEDGE_MODEL,
        "description": "Reads and maintains Mounir's structured long-term knowledge.",
        "safe_tools": {"list_knowledge", "read_knowledge", "search_knowledge"},
    },
    "system": {
        "name": "System",
        "module": "mounir.specialists.system",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Observes and controls computer hardware, connectivity, media, and power.",
        "safe_tools": {"system_status"},
    },
}


def definitions() -> list[dict]:
    """Return lightweight metadata without importing specialist modules."""
    return [
        {
            "key": key,
            "id": f"builtin:{key}",
            "name": definition["name"],
            "provider": definition["provider"],
            "default_model": definition["default_model"],
            "description": definition["description"],
        }
        for key, definition in _BUILTINS.items()
    ]


def definition(key: str) -> dict | None:
    normalized = str(key or "").removeprefix("builtin:").strip()
    item = _BUILTINS.get(normalized)
    if item is None:
        return None
    return {
        "key": normalized,
        "id": f"builtin:{normalized}",
        "name": item["name"],
        "provider": item["provider"],
        "default_model": item["default_model"],
        "description": item["description"],
    }


def provider_matches(key: str, provider: str) -> bool:
    """Match the organizational provider label to the built-in adapter."""
    item = definition(key)
    if item is None:
        return False
    candidate = str(provider or "").strip().lower()
    if item["provider"] == "NVIDIA":
        return "nvidia" in candidate
    return "gemini" in candidate or "google" in candidate


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
                "provider": definition["provider"],
                "default_model": definition["default_model"],
                "description": definition["description"],
                "connection_status": "built_in",
                "tools": tools,
            }
        )
    return result


def run(key: str, task: str, allowed_tools: list[str]) -> str:
    """Run one built-in specialist with a code-enforced tool allowlist."""
    normalized = str(key or "").removeprefix("builtin:").strip()
    definition = _BUILTINS.get(normalized)
    if definition is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    # Lazy import avoids the db -> builtin_agents catalog import cycle.
    from . import db
    if not db.is_builtin_agent_enabled(normalized):
        raise ValueError(f"{definition['name']} agent is inactive")
    safe = definition["safe_tools"]
    selected = [name for name in allowed_tools if name in safe]
    if not selected:
        raise ValueError(f"no safe tools selected for {definition['name']}")
    module = import_module(definition["module"])
    return module.run(task, allowed_tools=selected)
