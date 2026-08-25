"""Dynamic MCP subagents — registry and management CLI.

Persistence is now SQLite (see ``mounir/db.py``), not JSON. Three concepts:

- **Models**        reusable LLM presets (name, provider, base_url, api_key)
- **MCP servers**   reusable MCP connections (name, connection string)
- **Subagents**     the actual delegate targets (name, system_prompt, chosen
                    model, and zero or more selected MCP capabilities)

The graph reads ``mcp_agents.load()`` once per turn, so a new subagent is live
from the next message.

The management CLI is::

    python -m mounir.mcp_agents models list|add|update|remove
    python -m mounir.mcp_agents servers list|add|update|remove
    python -m mounir.mcp_agents agents list|show|add|update|remove
"""

from __future__ import annotations

import re
from typing import Annotated

from langchain_core.tools import StructuredTool

from . import config as cfg, db
from .db import add_model, add_server, add_subagent  # exposed for callers


# --- name handling ------------------------------------------------------------

def _slug(name: str) -> str:
    """Turn a display name into a tool/node-safe slug ("Web Search" -> "web_search")."""
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.strip().lower())).strip("_")


def delegate_tool_name(name: str) -> str:
    """The supervisor-facing tool that hands a task to this agent."""
    return f"delegate_to_{_slug(name)}"


def node_name(name: str) -> str:
    """The graph node id. Prefixed so it can never shadow a built-in node."""
    return f"mcp_{_slug(name)}"


def _tree_children(specs: list[dict], parent: dict | None) -> list[dict]:
    """Return direct children using placement IDs when the graph provides them."""
    placement_graph = any(
        "node_id" in spec or "parent_node_id" in spec for spec in specs
    )
    if parent is None:
        if placement_graph:
            children = [spec for spec in specs if spec.get("parent_node_id") is None]
        else:
            children = [
                spec
                for spec in specs
                if spec.get("connected_to_supervisor")
                or spec.get("parent_agent_id") is None
            ]
    elif placement_graph and parent.get("node_id") is not None:
        parent_node_id = int(parent["node_id"])
        children = [
            spec
            for spec in specs
            if spec.get("parent_node_id") is not None
            and int(spec["parent_node_id"]) == parent_node_id
        ]
    else:
        parent_agent_id = parent.get("id")
        children = [
            spec
            for spec in specs
            if spec.get("parent_agent_id") == parent_agent_id
        ]
    return sorted(
        children,
        key=lambda spec: (
            str(spec.get("name") or "").casefold(),
            int(spec.get("node_id") or spec.get("id") or 0),
        ),
    )


def subagent_tree_prompt(
    specs: list[dict], *, parent: dict | None = None
) -> str:
    """Describe the reachable delegation tree relative to one running agent.

    Direct children use names only because their delegation schemas already
    include descriptions. A deeper agent's description appears beside its
    first occurrence only, keyed internally by agent ID. Placement IDs keep
    repeated nodes independent without duplicating capability text.
    """
    direct_children = _tree_children(specs, parent)
    if not direct_children:
        return ""
    if not any(_tree_children(specs, child) for child in direct_children):
        return ""

    direct_agent_ids = {
        int(spec["id"])
        for spec in direct_children
        if spec.get("id") is not None
    }
    tree_lines = [
        "SUBAGENT TREE",
        "Call direct children only; route deeper agents through their parent.",
    ]
    described_agent_ids = set(direct_agent_ids)

    def identity(spec: dict) -> tuple[str, int]:
        if spec.get("node_id") is not None:
            return "node", int(spec["node_id"])
        return "agent", int(spec.get("id") or 0)

    def add_branch(spec: dict, depth: int, ancestors: set[tuple[str, int]]) -> None:
        label = str(spec.get("name") or "Unnamed subagent").strip()
        agent_id = spec.get("id")
        rendered = label
        if (
            depth > 1
            and agent_id is not None
            and int(agent_id) not in described_agent_ids
        ):
            description = " ".join(str(spec.get("description") or "").split())
            if description:
                rendered = f"{label} — {description}"
            described_agent_ids.add(int(agent_id))
        tree_lines.append(f"{'  ' * (depth - 1)}- {rendered}")

        branch_id = identity(spec)
        if branch_id in ancestors:
            return
        next_ancestors = {*ancestors, branch_id}
        for child in _tree_children(specs, spec):
            add_branch(child, depth + 1, next_ancestors)

    for child in direct_children:
        add_branch(child, 1, set())
    return "\n".join(tree_lines)


# Names a dynamic agent may not take: the built-in graph nodes and their
# delegate tools are already wired by hand.
_RESERVED = {"supervisor", "media", "knowledge", "system"}


def _validate_agent_name(name: str, *, exclude_id: int | None = None) -> None:
    if not isinstance(name, str):
        raise ValueError("name is required.")
    slug = _slug(name)
    if not slug:
        raise ValueError("name must contain at least one letter or digit.")
    if slug in _RESERVED:
        raise ValueError(f"'{name}' collides with the built-in '{slug}' agent — pick another name.")
    for agent in db.list_subagents():
        if agent["id"] != exclude_id and _slug(agent["name"]) == slug:
            raise ValueError(
                f"'{name}' produces the same delegate name as '{agent['name']}'."
            )


# --- runtime-facing API (used by the graph) ------------------------------------

def load() -> list[dict]:
    """All active subagents with their parent relationships resolved."""
    db.init()
    return db.build_specs()


def delegate_tool(spec: dict) -> StructuredTool:
    """Build the typed routing tool advertised for one dynamic subagent.

    The graph intercepts this call and routes to the subagent node, so the
    function is only a defensive fallback for direct invocation.
    """

    def route(
        task: Annotated[str, "Task with every detail the subagent needs."]
    ) -> str:
        return f"Delegation for {spec['name']} must run inside the agent graph: {task}"

    return StructuredTool.from_function(
        func=route,
        name=delegate_tool_name(spec["name"]),
        description=(
            f"Delegate to the {spec['name']} agent. {spec['description']} "
            "It completes the task with its available tools and returns the result."
        ),
    )


# --- management CLI -----------------------------------------------------------

def _print_table(rows: list[dict], columns: list[str]) -> None:
    if not rows:
        print("(none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    header = " | ".join(c.upper().ljust(widths[c]) for c in columns)
    print(header)
    print("-" * len(header))
    for r in rows:
        print(" | ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def _models_cmd(args):
    if args.action == "list":
        _print_table(db.list_models(), ["id", "name", "model", "provider", "base_url"])
    elif args.action == "add":
        if not all((args.name, args.model, args.base_url)):
            raise ValueError("--name, --model, and --base-url are required when adding a model.")
        m = db.add_model(args.name, args.model, args.provider, args.base_url, args.api_key or "")
        print(f"Added model {m['id']}: {m['name']}")
    elif args.action == "update":
        if args.id is None:
            raise ValueError("--id is required when updating a model.")
        m = db.update_model(args.id, name=args.name, model=args.model, provider=args.provider, base_url=args.base_url, api_key=args.api_key)
        print(f"Updated model {m['id']}: {m['name']}" if m else "Model not found.")
    elif args.action == "remove":
        if args.id is None:
            raise ValueError("--id is required when removing a model.")
        if db.delete_model(args.id):
            print("Removed model.")
        else:
            print("Model not found or in use by a subagent.")


def _servers_cmd(args):
    if args.action == "list":
        _print_table(db.list_servers(), ["id", "name", "transport", "connection"])
    elif args.action == "add":
        if not all((args.name, args.connection)):
            raise ValueError("--name and --connection are required when adding a server.")
        s = db.add_server(
            args.name,
            args.connection,
            transport=args.transport,
            headers=args.headers or "{}",
            env=args.env or "{}",
        )
        print(f"Added server {s['id']}: {s['name']}")
    elif args.action == "update":
        if args.id is None:
            raise ValueError("--id is required when updating a server.")
        s = db.update_server(
            args.id,
            name=args.name,
            connection=args.connection,
            transport=args.transport,
            headers=args.headers,
            env=args.env,
        )
        print(f"Updated server {s['id']}: {s['name']}" if s else "Server not found.")
    elif args.action == "remove":
        if args.id is None:
            raise ValueError("--id is required when removing a server.")
        if db.delete_server(args.id):
            print("Removed server.")
        else:
            print("Server not found or in use by a subagent.")


def _agents_cmd(args):
    if args.action == "list":
        rows = db.list_subagents()
        _print_table(
            rows,
            ["id", "name", "enabled", "model_name", "server_name", "parent_name"],
        )
    elif args.action == "show":
        if not args.name:
            raise ValueError("--name is required when showing an agent.")
        a = db.get_subagent_by_name(args.name)
        if not a:
            print("Agent not found.")
            return
        import json
        print(json.dumps(a, indent=2, default=str))
    elif args.action == "add":
        _validate_agent_name(args.name)
        if not (args.description or "").strip():
            raise ValueError("description is required — it's how the supervisor decides to delegate here.")
        if db.get_model(args.model_id) is None:
            raise ValueError(f"model_id {args.model_id} does not exist.")
        if args.server_id is not None and db.get_server(args.server_id) is None:
            raise ValueError(f"server_id {args.server_id} does not exist.")
        a = db.add_subagent(
            args.name,
            args.description,
            args.prompt or "",
            args.model_id,
            args.server_id,
            confirm_tool_calls=args.confirm_tool_calls is not False,
            parent_agent_id=args.parent_id,
            confirm_tools=(
                [name.strip() for name in args.confirm_tools.split(",") if name.strip()]
                if args.confirm_tools is not None
                else None
            ),
            enabled=args.enabled is not False,
        )
        print(f"Added agent {a['id']}: {a['name']} ({delegate_tool_name(a['name'])})")
    elif args.action == "update":
        if args.id is None:
            raise ValueError("--id is required when updating an agent.")
        fields = {}
        if args.name is not None:
            _validate_agent_name(args.name, exclude_id=args.id)
            fields["name"] = args.name
        if args.description is not None:
            fields["description"] = args.description
        if args.prompt is not None:
            fields["system_prompt"] = args.prompt
        if args.model_id is not None:
            fields["model_id"] = args.model_id
        if args.server_id is not None:
            fields["mcp_server_id"] = args.server_id
        if args.parent_id is not None:
            fields["parent_agent_id"] = args.parent_id or None
        if args.confirm_tool_calls is not None:
            fields["confirm_tool_calls"] = args.confirm_tool_calls
        if args.confirm_tools is not None:
            fields["confirm_tools"] = [
                name.strip() for name in args.confirm_tools.split(",") if name.strip()
            ]
        if args.enabled is not None:
            fields["enabled"] = args.enabled
        a = db.update_subagent(args.id, **fields)
        print(f"Updated agent {a['id']}: {a['name']}" if a else "Agent not found.")
    elif args.action == "remove":
        if args.id is None:
            raise ValueError("--id is required when removing an agent.")
        if db.delete_subagent(args.id):
            print("Removed agent.")
        else:
            print("Agent not found.")


def _main() -> int:
    import argparse

    db.init()

    parser = argparse.ArgumentParser(
        prog="python -m mounir.mcp_agents",
        description="Manage Mounir's dynamic MCP subagents, models, and servers.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # models
    p_mod = sub.add_parser("models", help="Manage model presets.")
    p_mod.add_argument("action", choices=["list", "add", "update", "remove"])
    p_mod.add_argument("--id", type=int)
    p_mod.add_argument("--name")
    p_mod.add_argument("--model")
    p_mod.add_argument("--provider")
    p_mod.add_argument("--base-url")
    p_mod.add_argument("--api-key")
    p_mod.set_defaults(func=_models_cmd)

    # servers
    p_srv = sub.add_parser("servers", help="Manage MCP server connections.")
    p_srv.add_argument("action", choices=["list", "add", "update", "remove"])
    p_srv.add_argument("--id", type=int)
    p_srv.add_argument("--name")
    p_srv.add_argument("--connection")
    p_srv.add_argument(
        "--transport",
        choices=[
            "stdio",
            "sse",
            "streamable_http"
        ],
        default=None,
    )
    p_srv.add_argument("--headers")
    p_srv.add_argument("--env")
    p_srv.set_defaults(func=_servers_cmd)

    # agents
    p_ag = sub.add_parser("agents", help="Manage subagents.")
    p_ag.add_argument("action", choices=["list", "show", "add", "update", "remove"])
    p_ag.add_argument("--id", type=int)
    p_ag.add_argument("--name")
    p_ag.add_argument("--description")
    p_ag.add_argument("--prompt")
    p_ag.add_argument("--model-id", type=int)
    p_ag.add_argument(
        "--server-id",
        type=int,
        help="Optional MCP server ID; omit it to create a prompt-only subagent.",
    )
    p_ag.add_argument(
        "--parent-id",
        type=int,
        help="Parent subagent ID; use 0 to attach directly to Mounir.",
    )
    p_ag.add_argument(
        "--confirm-tool-calls",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Require confirmation before every MCP tool call (default on add).",
    )
    p_ag.add_argument(
        "--confirm-tools",
        help="Comma-separated MCP tool names that require confirmation; overrides --confirm-tool-calls.",
    )
    p_ag.add_argument(
        "--enabled",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Make the subagent available or unavailable to the orchestrator.",
    )
    p_ag.set_defaults(func=_agents_cmd)

    args = parser.parse_args()
    try:
        args.func(args)
    except ValueError as exc:
        print(f"Refused: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
