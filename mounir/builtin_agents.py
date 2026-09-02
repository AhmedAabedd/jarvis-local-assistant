"""Catalog and restricted runner for Mounir's built-in specialists.

Heartbeat reads metadata from the same typed LangChain tools used by each
specialist and applies an explicit read-only allowlist at runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from .context_history import ContextHistory


_BUILTINS = {
    "computer": {
        "name": "Computer",
        "module": "mounir.specialists.computer",
        "provider": "Configured model",
        "default_model": config.MODEL,
        "description": (
            "Observes and controls visible desktop applications through a "
            "restricted set of native desktop tools."
        ),
        "safe_tools": {
            "screenshot", "cursor_position", "get_display_size", "wait",
        },
    },
    "media": {
        "name": "Files and Media",
        "module": "mounir.specialists.media",
        "provider": "NVIDIA",
        "default_model": config.MEDIA_MODEL,
        "description": (
            "Owns every local file and media operation: finding, listing, reading, "
            "creating, editing, appending, converting, and generating artifacts."
        ),
        "safe_tools": {"read_file", "load_media", "find_files"},
    },
    "knowledge": {
        "name": "Knowledge",
        "module": "mounir.specialists.knowledge",
        "provider": "Gemini",
        "default_model": config.KNOWLEDGE_MODEL,
        "description": (
            "Reads and maintains durable memory through Mounir's built-in "
            "local GBrain MCP service."
        ),
        "safe_tools": {"recall", "search", "get_page", "list_pages"},
    },
    "system": {
        "name": "System",
        "module": "mounir.specialists.system",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Observes and controls computer hardware, connectivity, media, and power.",
        "safe_tools": {"system_status"},
    },
    "facebook": {
        "name": "Facebook",
        "module": "mounir.specialists.facebook",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Manages connected Facebook Pages and Meta ad accounts through the official Graph API.",
        "safe_tools": {"list_connected_accounts", "list_page_posts", "list_ad_campaigns"},
    },
    "messenger": {
        "name": "Messenger",
        "module": "mounir.specialists.messenger",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Reports connected Facebook Page messaging readiness without personal accounts or cold DMs.",
        "safe_tools": {"list_connected_accounts", "messaging_policy"},
    },
    "instagram": {
        "name": "Instagram",
        "module": "mounir.specialists.instagram",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Reads and publishes for connected Instagram professional accounts through official APIs.",
        "safe_tools": {"list_connected_accounts", "list_media"},
    },
    "threads": {
        "name": "Threads",
        "module": "mounir.specialists.threads",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Reads and publishes for connected Threads profiles through the official Threads API.",
        "safe_tools": {"list_connected_accounts", "list_posts"},
    },
    "whatsapp": {
        "name": "WhatsApp",
        "module": "mounir.specialists.whatsapp",
        "provider": "NVIDIA",
        "default_model": config.SYSTEM_MODEL,
        "description": "Reads and serves WhatsApp Business inbox conversations through the official Cloud API.",
        "safe_tools": {"list_business_connections", "list_conversations", "read_messages"},
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


def system_prompt(key: str) -> str:
    """Return the built-in's shipped prompt without importing all specialists."""
    normalized = str(key or "").removeprefix("builtin:").strip()
    item = _BUILTINS.get(normalized)
    if item is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    module = import_module(item["module"])
    return str(getattr(module, "SYSTEM_PROMPT", "")).strip()


def default_max_tool_rounds(key: str) -> int:
    """Return the tool-round default shipped by a built-in runtime."""
    normalized = str(key or "").removeprefix("builtin:").strip()
    item = _BUILTINS.get(normalized)
    if item is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    module = import_module(item["module"])
    default = getattr(module, "MAX_TOOL_ROUNDS", None)
    if default is None:
        default = getattr(getattr(module, "meta_agent", None), "MAX_TOOL_ROUNDS", 8)
    return min(100, max(1, int(default)))


def capabilities() -> list[dict]:
    """Return built-in tool schemas with heartbeat safety metadata."""
    result: list[dict] = []
    for key, definition in _BUILTINS.items():
        module = import_module(definition["module"])
        safe = definition["safe_tools"]
        tools = []
        for registered_tool in module.TOOLS:
            name = str(registered_tool.name or "").strip()
            if not name:
                continue
            tools.append(
                {
                    "name": name,
                    "description": str(registered_tool.description or ""),
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


def default_confirmation_tools(key: str) -> list[str]:
    """Return the shipped safety defaults for one built-in specialist."""
    normalized = str(key or "").removeprefix("builtin:").strip()
    if normalized == "computer":
        # Computer has one mandatory approval at session start. Asking again for
        # every pointer or keyboard action makes an approved GUI task unusable.
        return []
    item = next(
        (agent for agent in capabilities() if agent["builtin_key"] == normalized),
        None,
    )
    if item is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    return [
        tool["name"] for tool in item["tools"]
        if tool["requires_confirmation"]
    ]


def run(
    key: str,
    task: str,
    allowed_tools: list[str],
    *,
    context_history_store: ContextHistory | None = None,
) -> str:
    """Run one built-in specialist with an approval-free tool allowlist."""
    normalized = str(key or "").removeprefix("builtin:").strip()
    definition = _BUILTINS.get(normalized)
    if definition is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    # Lazy import avoids the db -> builtin_agents catalog import cycle.
    from . import db
    if not db.is_builtin_agent_enabled(normalized):
        raise ValueError(f"{definition['name']} agent is inactive")
    capability = next(
        agent for agent in capabilities() if agent["builtin_key"] == normalized
    )
    confirmation_rules = set(db.get_builtin_confirmation_tools(normalized))
    safe = {
        tool["name"]
        for tool in capability["tools"]
        if "*" not in confirmation_rules and tool["name"] not in confirmation_rules
    }
    selected = [name for name in allowed_tools if name in safe]
    if not selected:
        raise ValueError(f"no safe tools selected for {definition['name']}")
    module = import_module(definition["module"])
    if normalized in {"computer", "media", "knowledge", "system"}:
        return module.run(
            task,
            allowed_tools=selected,
            context_history_store=context_history_store,
        )
    return module.run(task, allowed_tools=selected)


def run_direct(key: str, task: str) -> str:
    """Run one enabled built-in with its normal confirmation policy."""
    normalized = str(key or "").removeprefix("builtin:").strip()
    definition = _BUILTINS.get(normalized)
    if definition is None:
        raise ValueError(f"unknown built-in specialist: {key}")
    from . import db

    if not db.is_builtin_agent_enabled(normalized):
        raise ValueError(f"{definition['name']} agent is inactive")
    module = import_module(definition["module"])
    return module.run(task)
