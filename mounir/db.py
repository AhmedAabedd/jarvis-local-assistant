"""SQLite persistence for Mounir's dynamic agent layer.

Three tables:

- ``models``       reusable LLM presets (name, provider, base_url, api_key)
- ``mcp_servers``  reusable MCP server connections (name, connection)
- ``subagents``    the actual agents the supervisor can delegate to
                   (name, system_prompt, model_id, mcp_server_id, parent)

On first run, if ``~/.mounir/mcp_agents.json`` exists it is migrated into the
DB; after that the JSON file is ignored.

API keys are stored as you provide them. To avoid keeping raw secrets in the
DB, put an env var reference like ``$OPENAI_API_KEY`` or ``${OPENAI_API_KEY}``
and Mounir expands it at runtime.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg

DB_PATH: Path = cfg.DATA_DIR / "mounir.db"
LEGACY_REGISTRY: Path = cfg.DATA_DIR / "mcp_agents.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            model TEXT,
            provider TEXT,
            base_url TEXT NOT NULL,
            api_key TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS mcp_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            connection TEXT NOT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS subagents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
            mcp_server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE RESTRICT,
            parent TEXT DEFAULT 'supervisor',
            created_at TEXT
        );
        """
    )
    # Older DBs from earlier iterations may be missing the description column.
    try:
        conn.execute("ALTER TABLE subagents ADD COLUMN description TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Newer columns added after the initial release.
    try:
        conn.execute("ALTER TABLE models ADD COLUMN model TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """One-time import from the old JSON registry, if it exists and the DB is empty."""
    if not LEGACY_REGISTRY.exists():
        return
    cur = conn.execute("SELECT COUNT(*) FROM subagents")
    if cur.fetchone()[0] > 0:
        return
    try:
        agents = json.loads(LEGACY_REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(agents, list):
        return

    for a in agents:
        model_name = a.get("model") or "Imported model"
        model = _get_model_by_name(conn, model_name)
        if model is None:
            api_key = ""
            if a.get("api_key_env"):
                api_key = f"${{{a['api_key_env']}}}"
            model_id = _add_model(conn, model_name, model_name, "Imported", a.get("base_url", ""), api_key)
        else:
            model_id = model["id"]

        cmd = (a.get("command") or "").strip()
        server_name = f"Imported: {cmd[:50]}" if cmd else "Imported server"
        server_id = _add_server(conn, server_name, cmd)

        _add_subagent(
            conn,
            name=a.get("name", "Imported agent"),
            description=a.get("description", ""),
            system_prompt=a.get("prompt", ""),
            model_id=model_id,
            mcp_server_id=server_id,
            parent=a.get("parent", "supervisor"),
        )
    conn.commit()


def init() -> None:
    """Create tables and migrate legacy JSON once."""
    with _connect() as conn:
        _init_schema(conn)
        _migrate_legacy(conn)


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

def _add_model(
    conn: sqlite3.Connection,
    name: str,
    model: str,
    provider: str,
    base_url: str,
    api_key: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO models (name, model, provider, base_url, api_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            name.strip(),
            (model or "").strip(),
            (provider or "").strip(),
            base_url.strip(),
            api_key,
            _now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def _get_model_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    cur = conn.execute("SELECT * FROM models WHERE name = ?", (name.strip(),))
    row = cur.fetchone()
    return dict(row) if row else None


def add_model(name: str, model: str, provider: str, base_url: str, api_key: str) -> dict:
    with _connect() as conn:
        mid = _add_model(conn, name, model, provider, base_url, api_key)
        return get_model(mid)


def get_model(model_id: int) -> dict | None:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_model_by_name(name: str) -> dict | None:
    with _connect() as conn:
        return _get_model_by_name(conn, name)


def list_models() -> list[dict]:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM models ORDER BY name")
        return [dict(r) for r in cur.fetchall()]


def update_model(model_id: int, **kwargs) -> dict | None:
    allowed = {"name", "model", "provider", "base_url", "api_key"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return get_model(model_id)
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE models SET {sets} WHERE id = ?", (*fields.values(), model_id))
        conn.commit()
        return get_model(model_id)


def delete_model(model_id: int) -> bool:
    with _connect() as conn:
        try:
            cur = conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False


# -----------------------------------------------------------------------------
# MCP servers
# -----------------------------------------------------------------------------

def _add_server(conn: sqlite3.Connection, name: str, connection: str) -> int:
    cur = conn.execute(
        "INSERT INTO mcp_servers (name, connection, created_at) VALUES (?, ?, ?)",
        (name.strip(), connection.strip(), _now()),
    )
    conn.commit()
    return cur.lastrowid


def add_server(name: str, connection: str) -> dict:
    with _connect() as conn:
        sid = _add_server(conn, name, connection)
        return get_server(sid)


def get_server(server_id: int) -> dict | None:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM mcp_servers WHERE id = ?", (server_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def list_servers() -> list[dict]:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM mcp_servers ORDER BY name")
        return [dict(r) for r in cur.fetchall()]


def update_server(server_id: int, **kwargs) -> dict | None:
    allowed = {"name", "connection"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return get_server(server_id)
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE mcp_servers SET {sets} WHERE id = ?", (*fields.values(), server_id))
        conn.commit()
        return get_server(server_id)


def delete_server(server_id: int) -> bool:
    with _connect() as conn:
        try:
            cur = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            conn.commit()
            return cur.rowcount > 0
        except sqlite3.IntegrityError:
            return False


# -----------------------------------------------------------------------------
# Subagents
# -----------------------------------------------------------------------------

def _add_subagent(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    system_prompt: str,
    model_id: int,
    mcp_server_id: int,
    parent: str = "supervisor",
) -> int:
    cur = conn.execute(
        "INSERT INTO subagents (name, description, system_prompt, model_id, mcp_server_id, parent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name.strip(), description.strip(), system_prompt.strip(), model_id, mcp_server_id, parent.strip() or "supervisor", _now()),
    )
    conn.commit()
    return cur.lastrowid


def add_subagent(
    name: str,
    description: str,
    system_prompt: str,
    model_id: int,
    mcp_server_id: int,
    parent: str = "supervisor",
) -> dict:
    with _connect() as conn:
        aid = _add_subagent(conn, name, description, system_prompt, model_id, mcp_server_id, parent)
        return get_subagent(aid)


def get_subagent(subagent_id: int) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT s.*, m.name AS model_name, m.provider, m.base_url, m.api_key,
                   srv.name AS server_name, srv.connection
            FROM subagents s
            JOIN models m ON s.model_id = m.id
            JOIN mcp_servers srv ON s.mcp_server_id = srv.id
            WHERE s.id = ?
            """,
            (subagent_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_subagent_by_name(name: str) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT s.*, m.name AS model_name, m.provider, m.base_url, m.api_key,
                   srv.name AS server_name, srv.connection
            FROM subagents s
            JOIN models m ON s.model_id = m.id
            JOIN mcp_servers srv ON s.mcp_server_id = srv.id
            WHERE s.name = ?
            """,
            (name.strip(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_subagents() -> list[dict]:
    with _connect() as conn:
        cur = conn.execute(
            """
            SELECT s.*, m.name AS model_name, m.provider, m.base_url, m.api_key,
                   srv.name AS server_name, srv.connection
            FROM subagents s
            JOIN models m ON s.model_id = m.id
            JOIN mcp_servers srv ON s.mcp_server_id = srv.id
            ORDER BY s.name
            """
        )
        return [dict(r) for r in cur.fetchall()]


def update_subagent(subagent_id: int, **kwargs) -> dict | None:
    allowed = {"name", "description", "system_prompt", "model_id", "mcp_server_id", "parent"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return get_subagent(subagent_id)
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE subagents SET {sets} WHERE id = ?", (*fields.values(), subagent_id))
        conn.commit()
        return get_subagent(subagent_id)


def delete_subagent(subagent_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM subagents WHERE id = ?", (subagent_id,))
        conn.commit()
        return cur.rowcount > 0


# -----------------------------------------------------------------------------
# Resolved specs for the LangGraph runtime
# -----------------------------------------------------------------------------

def _resolve_key(key: str) -> str:
    """Expand $VAR / ${VAR} references from the environment."""
    if not key:
        return ""
    return os.path.expandvars(key)


def build_specs() -> list[dict]:
    """Return the list of subagent specs the graph uses at compile time.

    Each spec resolves its model and MCP server into the flat shape the
    generic MCP specialist expects.
    """
    specs = []
    for s in list_subagents():
        specs.append(
            {
                "id": s["id"],
                "name": s["name"],
                "description": s.get("description") or f"Uses the {s['server_name']} MCP server.",
                "prompt": s["system_prompt"],
                "command": s["connection"],
                "parent": s.get("parent") or "supervisor",
                "model": (s.get("model") or "").strip() or s["model_name"],
                "base_url": s["base_url"],
                "api_key": _resolve_key(s.get("api_key") or ""),
                "env": {},
                "confirm_tools": [],
            }
        )
    return specs
