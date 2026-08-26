"""Contract and managed MCP defaults for Mounir's local knowledge service.

GBrain-specific behavior stays isolated here. User-created MCP servers remain
fully dynamic and are never interpreted as Knowledge backends.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config

PROTOCOL_NAME = "GBrain local MCP"
PROTOCOL_VERSION = 1

BUILTIN_SETUP_TYPE = "builtin_gbrain"
BUILTIN_SERVER_NAME = "GBrain"
BUILTIN_SERVER_DESCRIPTION = (
    "Built-in local knowledge service used exclusively by the Knowledge subagent."
)
BUILTIN_SERVER_COMMAND = "gbrain serve"
BUILTIN_SETUP_COMMAND = "python -m mounir.setup_gbrain"

# GBrain's official per-turn, zero-LLM context operation. It is an internal
# supervisor read rather than a Knowledge subagent capability, so it is not
# included in TOOL_NAMES or exposed in the Knowledge tool picker.
AUTOMATIC_CONTEXT_TOOL = "volunteer_context"

TOOL_NAMES = (
    "recall",
    "search",
    "get_page",
    "list_pages",
    "put_page",
    "delete_page",
    "restore_page",
)

REQUIRED_TOOL_NAMES = ("recall", "search", "get_page", "put_page", "delete_page")
READ_TOOLS = frozenset({"recall", "search", "get_page", "list_pages"})
WRITE_TOOLS = frozenset({"put_page", "delete_page", "restore_page"})


def local_home_parent() -> Path:
    """Return GBrain's installation-local parent directory.

    GBrain appends its own ``.gbrain`` directory to this path.
    """
    configured = os.environ.get("MOUNIR_GBRAIN_HOME", "").strip()
    path = Path(configured).expanduser() if configured else config.DATA_DIR / "gbrain"
    return path.resolve()


def missing_tools(names) -> list[str]:
    """Return the core native GBrain tools missing from an advertised catalog."""
    available = {str(name or "").strip() for name in names}
    return [name for name in REQUIRED_TOOL_NAMES if name not in available]
