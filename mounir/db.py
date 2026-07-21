"""SQLite persistence for Mounir's local configuration and dynamic agents.

Core tables include:

- ``models``       reusable LLM presets (name, provider, base_url, api_key)
- ``mcp_servers``  reusable MCP server connections (transport + command/URL)
- ``subagents``    the actual agents the supervisor can delegate to
                   (name, system_prompt, model_id, mcp_server_id, parent)
- ``mcp_server_tools`` cached MCP capability metadata
- ``heartbeat_*`` heartbeat permissions, schedule, state, and bounded run log
- ``telegram_settings`` private Telegram bot configuration and pairing state

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as cfg, default_agents

DB_PATH: Path = cfg.DATA_DIR / "mounir.db"
LEGACY_REGISTRY: Path = cfg.DATA_DIR / "mcp_agents.json"
MCP_TRANSPORTS = {"stdio", "streamable_http", "sse"}
HEARTBEAT_DEFAULT_INSTRUCTIONS = (
    "Check my connected services for new items that genuinely need my attention. "
    "Ignore routine or unchanged information."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # UI-entered credentials are stored locally in this DB. Restrict it to the
    # current OS user even when the process was started with a permissive umask.
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
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

            -- User-facing information shown by Agent Studio. This belongs to
            -- the saved server instead of being inferred from its package.
            description TEXT NOT NULL DEFAULT '',

            -- Optional built-in onboarding adapter. Ordinary MCP servers leave
            -- this empty and use the standard transport/authentication fields.
            setup_type TEXT NOT NULL DEFAULT '',

            -- stdio | sse | streamable_http
            transport TEXT NOT NULL DEFAULT 'stdio',

            -- command for stdio OR URL for remote transports
            connection TEXT NOT NULL,

            -- JSON object:
            -- {
            --   "Authorization": "Bearer xxx"
            -- }
            headers TEXT NOT NULL DEFAULT '{}',

            -- JSON object:
            -- {
            --   "API_KEY": "xxx"
            -- }
            env TEXT NOT NULL DEFAULT '{}',

            -- none | bearer | header | custom (UI metadata; runtime uses headers)
            auth_scheme TEXT NOT NULL DEFAULT '',

            -- untested | connected | stale | failed
            connection_status TEXT NOT NULL DEFAULT 'untested',
            last_tested_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',

            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS mcp_server_tools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mcp_server_id INTEGER NOT NULL
                REFERENCES mcp_servers(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            input_schema TEXT NOT NULL DEFAULT '{}',
            position INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT NOT NULL,
            UNIQUE (mcp_server_id, name)
        );

        CREATE INDEX IF NOT EXISTS idx_mcp_server_tools_server_position
            ON mcp_server_tools (mcp_server_id, position);

        CREATE TABLE IF NOT EXISTS subagents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            icon_data BLOB NOT NULL DEFAULT X'',
            icon_mime TEXT NOT NULL DEFAULT '',
            model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
            mcp_server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE RESTRICT,
            confirm_tool_calls INTEGER NOT NULL DEFAULT 1,
            confirm_tools TEXT NOT NULL DEFAULT '["*"]',
            dedupe_tools TEXT NOT NULL DEFAULT '[]',
            parent TEXT DEFAULT 'supervisor',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS profile_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            user_name TEXT NOT NULL,
            assistant_name TEXT NOT NULL,
            location TEXT NOT NULL,
            preferred_language TEXT NOT NULL DEFAULT 'auto',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS heartbeat_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            interval_minutes INTEGER NOT NULL DEFAULT 30,
            instructions TEXT NOT NULL DEFAULT '',
            next_run_at TEXT,
            last_run_at TEXT,
            last_status TEXT NOT NULL DEFAULT 'never',
            last_message TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS heartbeat_tools (
            subagent_id INTEGER NOT NULL
                REFERENCES subagents(id) ON DELETE CASCADE,
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (subagent_id, tool_name)
        );

        CREATE TABLE IF NOT EXISTS heartbeat_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            message TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS heartbeat_agent_state (
            subagent_id INTEGER PRIMARY KEY
                REFERENCES subagents(id) ON DELETE CASCADE,
            last_report TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_started
            ON heartbeat_runs (started_at DESC);

        CREATE TABLE IF NOT EXISTS telegram_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            bot_token TEXT NOT NULL DEFAULT '',
            chat_id TEXT NOT NULL DEFAULT '',
            chat_name TEXT NOT NULL DEFAULT '',
            chat_username TEXT NOT NULL DEFAULT '',
            bot_username TEXT NOT NULL DEFAULT '',
            connection_status TEXT NOT NULL DEFAULT 'disabled',
            last_error TEXT NOT NULL DEFAULT '',
            last_tested_at TEXT,
            updated_at TEXT
        );
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO profile_settings
            (id, user_name, assistant_name, location, preferred_language, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        """,
        (
            cfg.DEFAULT_USER_NAME,
            cfg.DEFAULT_ASSISTANT_NAME,
            cfg.DEFAULT_LOCATION,
            cfg.DEFAULT_LANGUAGE,
            _now(),
        ),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO heartbeat_settings (id, enabled, updated_at)
        VALUES (1, 0, ?)
        """,
        (_now(),),
    )
    telegram_token = cfg.TELEGRAM_BOT_TOKEN.strip()
    telegram_chat = cfg.TELEGRAM_CHAT_ID.strip()
    telegram_enabled = bool(cfg.TELEGRAM_ENABLED and telegram_token)
    telegram_status = (
        "disabled" if not telegram_enabled
        else "configured" if telegram_chat
        else "waiting_pairing"
    )
    # Environment values are a one-time bootstrap for existing installations.
    # Once this row exists, Agent Studio owns the configuration.
    conn.execute(
        """
        INSERT OR IGNORE INTO telegram_settings
            (id, enabled, bot_token, chat_id, connection_status, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        """,
        (
            int(telegram_enabled), telegram_token, telegram_chat,
            telegram_status, _now(),
        ),
    )
    # CREATE TABLE does not add columns to an existing SQLite table. Keep the
    # migrations explicit so upgrading an earlier feature-branch DB works.
    migrations = {
        "models": {"model": "TEXT"},
        "mcp_servers": {
            "description": "TEXT NOT NULL DEFAULT ''",
            "setup_type": "TEXT NOT NULL DEFAULT ''",
            "transport": "TEXT NOT NULL DEFAULT 'stdio'",
            "headers": "TEXT NOT NULL DEFAULT '{}'",
            "env": "TEXT NOT NULL DEFAULT '{}'",
            "auth_scheme": "TEXT NOT NULL DEFAULT ''",
            "connection_status": "TEXT NOT NULL DEFAULT 'untested'",
            "last_tested_at": "TEXT",
            "last_error": "TEXT NOT NULL DEFAULT ''",
        },
        "subagents": {
            "description": "TEXT NOT NULL DEFAULT ''",
            "icon_data": "BLOB NOT NULL DEFAULT X''",
            "icon_mime": "TEXT NOT NULL DEFAULT ''",
            "confirm_tool_calls": "INTEGER NOT NULL DEFAULT 1",
            "confirm_tools": "TEXT NOT NULL DEFAULT '[\"*\"]'",
            "dedupe_tools": "TEXT NOT NULL DEFAULT '[]'",
        },
        "heartbeat_settings": {
            "interval_minutes": "INTEGER NOT NULL DEFAULT 30",
            "instructions": "TEXT NOT NULL DEFAULT ''",
            "next_run_at": "TEXT",
            "last_run_at": "TEXT",
            "last_status": "TEXT NOT NULL DEFAULT 'never'",
            "last_message": "TEXT NOT NULL DEFAULT ''",
            "last_error": "TEXT NOT NULL DEFAULT ''",
        },
    }
    added_columns: set[tuple[str, str]] = set()
    for table, columns in migrations.items():
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                added_columns.add((table, column))
    conn.execute(
        """
        UPDATE heartbeat_settings SET instructions = ?
        WHERE instructions IS NULL OR trim(instructions) = ''
        """,
        (HEARTBEAT_DEFAULT_INSTRUCTIONS,),
    )
    # Earlier versions inferred every URL as legacy SSE and had no transport
    # column. A URL cannot be a valid stdio command, so migrate it to the
    # current remote default; users can explicitly switch old servers to SSE.
    conn.execute(
        """
        UPDATE mcp_servers
        SET transport = 'streamable_http'
        WHERE transport = 'stdio'
          AND (connection LIKE 'http://%' OR connection LIKE 'https://%')
        """
    )
    conn.execute(
        "UPDATE models SET model = name WHERE model IS NULL OR trim(model) = ''"
    )
    confirmation_filter = (
        "" if ("subagents", "confirm_tools") in added_columns
        else "WHERE confirm_tools IS NULL OR trim(confirm_tools) = ''"
    )
    conn.execute(
        f"""
        UPDATE subagents
        SET confirm_tools = CASE
            WHEN confirm_tool_calls = 1 THEN '["*"]'
            ELSE '[]'
        END
        {confirmation_filter}
        """
    )
    # Normalize values accepted by the earlier UI: a full completions URL and
    # Ollama's native /api/chat URL were commonly pasted into "Base URL".
    for row in conn.execute("SELECT id, base_url FROM models"):
        try:
            normalized = _normalize_model_base_url(row["base_url"])
        except ValueError:
            continue  # Keep the DB readable so the invalid preset can be edited.
        if normalized != row["base_url"]:
            conn.execute(
                "UPDATE models SET base_url = ? WHERE id = ?",
                (normalized, row["id"]),
            )
    conn.commit()


def _required(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    return text


def _normalize_model_base_url(value) -> str:
    base_url = _required(value, "base URL").rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise ValueError("base URL must start with http:// or https://.")
    if base_url.endswith("/chat/completions"):
        base_url = base_url.removesuffix("/chat/completions")
    if base_url.endswith("/api/chat") and (
        "//localhost:" in base_url or "//127.0.0.1:" in base_url
    ):
        base_url = base_url.removesuffix("/api/chat") + "/v1"
    return base_url


def _json_object(value, field: str) -> str:
    """Validate and canonicalize a JSON object stored in SQLite."""
    if value in (None, ""):
        parsed = {}
    elif isinstance(value, dict):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a valid JSON object: {exc.msg}.") from exc
    else:
        raise ValueError(f"{field} must be a JSON object.")
    if not isinstance(parsed, dict):
        raise ValueError(f"{field} must be a JSON object.")
    normalized = {str(k): str(v) for k, v in parsed.items()}
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True)


def _json_string_list(value, field: str) -> str:
    """Validate and canonicalize a JSON list of unique, non-empty strings."""
    if value in (None, ""):
        parsed = []
    elif isinstance(value, (list, tuple, set)):
        parsed = list(value)
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} must be a valid JSON list: {exc.msg}.") from exc
    else:
        raise ValueError(f"{field} must be a JSON list.")
    if not isinstance(parsed, list):
        raise ValueError(f"{field} must be a JSON list.")
    normalized = list(dict.fromkeys(str(item).strip() for item in parsed if str(item).strip()))
    if "*" in normalized and len(normalized) > 1:
        raise ValueError(f"{field} cannot combine '*' with named tools.")
    return json.dumps(normalized, ensure_ascii=False)


def _bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"true", "false", "1", "0"}:
        return value.lower() in {"true", "1"}
    raise ValueError(f"{field} must be true or false.")


def _validate_transport(transport, connection) -> tuple[str, str]:
    transport = str(transport or "stdio").strip().lower()
    connection = _required(connection, "connection")
    if transport not in MCP_TRANSPORTS:
        raise ValueError(
            "transport must be stdio, streamable_http, or sse."
        )
    is_url = connection.startswith(("http://", "https://"))
    if transport == "stdio" and is_url:
        raise ValueError("stdio needs a local command, not a URL.")
    if transport != "stdio" and not is_url:
        raise ValueError(f"{transport} needs an http:// or https:// URL.")
    return transport, connection


def _friendly_integrity_error(exc: sqlite3.IntegrityError) -> ValueError:
    message = str(exc)
    if "UNIQUE constraint failed" in message:
        return ValueError("An item with that name already exists.")
    if "FOREIGN KEY constraint failed" in message:
        return ValueError("The selected model or MCP server does not exist.")
    return ValueError(message)


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
            model_id = _add_model(
                conn,
                model_name,
                model_name,
                "Imported",
                a.get("base_url") or "http://localhost:11434/v1",
                api_key,
            )
        else:
            model_id = model["id"]

        cmd = (a.get("command") or "").strip()
        server_name = f"Imported: {cmd[:50]}" if cmd else "Imported server"
        if not cmd:
            continue
        server_id = _add_server(conn, server_name, cmd)

        _add_subagent(
            conn,
            name=a.get("name", "Imported agent"),
            description=a.get("description") or f"Uses the {server_name} MCP server.",
            system_prompt=a.get("prompt", ""),
            model_id=model_id,
            mcp_server_id=server_id,
            parent=a.get("parent", "supervisor"),
        )
    conn.commit()


def _unique_name(conn: sqlite3.Connection, table: str, preferred: str) -> str:
    """Return a readable unused name for a seeded model or server."""
    candidate = preferred
    suffix = 2
    while conn.execute(
        f"SELECT 1 FROM {table} WHERE lower(name) = lower(?)", (candidate,)
    ).fetchone():
        candidate = f"{preferred} {suffix}"
        suffix += 1
    return candidate


def _migrate_builtin_email(conn: sqlite3.Connection) -> None:
    """Move the former hard-coded Gmail specialist into the dynamic registry once."""
    migration_key = "builtin_email_to_dynamic_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    existing = conn.execute(
        "SELECT 1 FROM subagents WHERE lower(name) = 'email'"
    ).fetchone()
    if not existing:
        base_url = _normalize_model_base_url(default_agents.email_model_base_url())
        model_row = conn.execute(
            "SELECT id FROM models WHERE model = ? AND rtrim(base_url, '/') = ? LIMIT 1",
            (default_agents.EMAIL_MODEL, base_url),
        ).fetchone()
        if model_row:
            model_id = model_row["id"]
        else:
            model_id = _add_model(
                conn,
                _unique_name(conn, "models", default_agents.EMAIL_MODEL_NAME),
                default_agents.EMAIL_MODEL,
                "Ollama Cloud",
                base_url,
                cfg.OLLAMA_API_KEY,
            )

        server_row = conn.execute(
            """
            SELECT id FROM mcp_servers
            WHERE transport = 'stdio' AND connection = ?
            LIMIT 1
            """,
            (default_agents.EMAIL_SERVER_COMMAND,),
        ).fetchone()
        if server_row:
            server_id = server_row["id"]
        else:
            server_id = _add_server(
                conn,
                _unique_name(conn, "mcp_servers", default_agents.EMAIL_SERVER_NAME),
                default_agents.EMAIL_SERVER_COMMAND,
                description=default_agents.EMAIL_SERVER_DESCRIPTION,
                setup_type=default_agents.EMAIL_SERVER_SETUP_TYPE,
            )

        _add_subagent(
            conn,
            default_agents.EMAIL_AGENT_NAME,
            default_agents.EMAIL_DESCRIPTION,
            default_agents.EMAIL_SYSTEM_PROMPT,
            model_id,
            server_id,
            confirm_tools=default_agents.EMAIL_CONFIRM_TOOLS,
            dedupe_tools=default_agents.EMAIL_DEDUPE_TOOLS,
        )

    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?)",
        (migration_key, _now()),
    )
    conn.commit()


def _migrate_builtin_researcher(conn: sqlite3.Connection) -> None:
    """Move the former hand-written web researcher into the dynamic registry once."""
    migration_key = "builtin_researcher_to_dynamic_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    existing = conn.execute(
        "SELECT 1 FROM subagents WHERE lower(name) = 'researcher'"
    ).fetchone()
    if not existing:
        base_url = _normalize_model_base_url(
            default_agents.researcher_model_base_url()
        )
        model_row = conn.execute(
            "SELECT id FROM models WHERE model = ? AND rtrim(base_url, '/') = ? LIMIT 1",
            (default_agents.RESEARCHER_MODEL, base_url),
        ).fetchone()
        if model_row:
            model_id = model_row["id"]
        else:
            model_id = _add_model(
                conn,
                _unique_name(conn, "models", default_agents.RESEARCHER_MODEL_NAME),
                default_agents.RESEARCHER_MODEL,
                "Ollama Cloud",
                base_url,
                cfg.OLLAMA_API_KEY,
            )

        command = default_agents.researcher_server_command()
        server_row = conn.execute(
            """
            SELECT id FROM mcp_servers
            WHERE transport = 'stdio' AND connection = ?
            LIMIT 1
            """,
            (command,),
        ).fetchone()
        if server_row:
            server_id = server_row["id"]
        else:
            server_id = _add_server(
                conn,
                _unique_name(
                    conn, "mcp_servers", default_agents.RESEARCHER_SERVER_NAME
                ),
                command,
                description=default_agents.RESEARCHER_SERVER_DESCRIPTION,
            )

        _add_subagent(
            conn,
            default_agents.RESEARCHER_AGENT_NAME,
            default_agents.RESEARCHER_DESCRIPTION,
            default_agents.RESEARCHER_SYSTEM_PROMPT,
            model_id,
            server_id,
            confirm_tools=default_agents.RESEARCHER_CONFIRM_TOOLS,
        )

    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?)",
        (migration_key, _now()),
    )
    conn.commit()


def _migrate_server_metadata(conn: sqlite3.Connection) -> None:
    """Attach UI metadata to already-seeded servers without inspecting commands."""
    migration_key = "dynamic_server_metadata_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (migration_key,)
    ).fetchone():
        return

    presets = (
        (
            default_agents.EMAIL_AGENT_NAME,
            default_agents.EMAIL_SERVER_DESCRIPTION,
            default_agents.EMAIL_SERVER_SETUP_TYPE,
        ),
        (
            default_agents.RESEARCHER_AGENT_NAME,
            default_agents.RESEARCHER_SERVER_DESCRIPTION,
            "",
        ),
    )
    for agent_name, description, setup_type in presets:
        conn.execute(
            """
            UPDATE mcp_servers
            SET description = CASE
                    WHEN trim(description) = '' THEN ? ELSE description END,
                setup_type = CASE
                    WHEN trim(setup_type) = '' THEN ? ELSE setup_type END
            WHERE id IN (
                SELECT mcp_server_id FROM subagents WHERE lower(name) = lower(?)
            )
            """,
            (description, setup_type, agent_name),
        )
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?)",
        (migration_key, _now()),
    )
    conn.commit()


def _migrate_action_deduplication(conn: sqlite3.Connection) -> None:
    """Enable duplicate-send protection for an existing seeded Email agent."""
    migration_key = "dynamic_action_deduplication_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (migration_key,)
    ).fetchone():
        return
    conn.execute(
        """
        UPDATE subagents SET dedupe_tools = ?
        WHERE lower(name) = lower(?)
          AND (dedupe_tools IS NULL OR trim(dedupe_tools) IN ('', '[]'))
        """,
        (
            json.dumps(default_agents.EMAIL_DEDUPE_TOOLS),
            default_agents.EMAIL_AGENT_NAME,
        ),
    )
    conn.execute(
        "INSERT INTO app_meta (key, value) VALUES (?, ?)",
        (migration_key, _now()),
    )
    conn.commit()


def init() -> None:
    """Create tables and run one-time registry migrations."""
    with _connect() as conn:
        _init_schema(conn)
        _migrate_legacy(conn)
        _migrate_builtin_email(conn)
        _migrate_builtin_researcher(conn)
        _migrate_server_metadata(conn)
        _migrate_action_deduplication(conn)


# -----------------------------------------------------------------------------
# User profile
# -----------------------------------------------------------------------------

def get_profile() -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM profile_settings WHERE id = 1"
        ).fetchone()
        if row:
            return dict(row)
    return {
        "id": 1,
        "user_name": cfg.DEFAULT_USER_NAME,
        "assistant_name": cfg.DEFAULT_ASSISTANT_NAME,
        "location": cfg.DEFAULT_LOCATION,
        "preferred_language": cfg.DEFAULT_LANGUAGE,
        "updated_at": None,
    }


def _profile_text(value, field: str, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_length:
        raise ValueError(f"{field} must be {max_length} characters or fewer")
    return text


def update_profile(**kwargs) -> dict:
    allowed = {"user_name", "assistant_name", "location", "preferred_language"}
    fields = {key: value for key, value in kwargs.items() if key in allowed}
    if "user_name" in fields:
        fields["user_name"] = _profile_text(fields["user_name"], "user name", 80)
    if "assistant_name" in fields:
        fields["assistant_name"] = _profile_text(
            fields["assistant_name"], "assistant name", 80
        )
    if "location" in fields:
        fields["location"] = _profile_text(fields["location"], "location", 160)
    if "preferred_language" in fields:
        language = str(fields["preferred_language"] or "").strip().lower()
        if language not in {"auto", "en", "fr", "ar"}:
            raise ValueError("preferred language is not supported")
        fields["preferred_language"] = language
    if not fields:
        return get_profile()
    fields["updated_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE profile_settings SET {sets} WHERE id = 1",
            tuple(fields.values()),
        )
        conn.commit()
    return get_profile()


# -----------------------------------------------------------------------------
# Telegram configuration
# -----------------------------------------------------------------------------

TELEGRAM_STATUSES = {
    "disabled", "configured", "connecting", "waiting_pairing", "connected", "error"
}


def get_telegram_settings(*, include_secret: bool = False) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM telegram_settings WHERE id = 1"
        ).fetchone()
    if row is None:
        result = {
            "id": 1,
            "enabled": False,
            "bot_token": "",
            "chat_id": "",
            "chat_name": "",
            "chat_username": "",
            "bot_username": "",
            "connection_status": "disabled",
            "last_error": "",
            "last_tested_at": None,
            "updated_at": None,
        }
    else:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
    result["token_configured"] = bool(result["bot_token"])
    result["paired"] = bool(result["chat_id"])
    if not include_secret:
        result.pop("bot_token", None)
        result.pop("chat_id", None)
    return result


def update_telegram_settings(
    *,
    enabled: bool | None = None,
    bot_token: str | None = None,
    clear_token: bool = False,
) -> dict:
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if bot_token is not None:
        bot_token = str(bot_token).strip()
        if not bot_token:
            raise ValueError("bot token is required")
        if len(bot_token) > 512:
            raise ValueError("bot token is too long")
    current = get_telegram_settings(include_secret=True)
    fields: dict[str, object] = {}
    token_changed = bot_token is not None and bot_token != current["bot_token"]
    if clear_token:
        fields.update(
            enabled=0,
            bot_token="",
            chat_id="",
            chat_name="",
            chat_username="",
            bot_username="",
            connection_status="disabled",
            last_error="",
            last_tested_at=None,
        )
    else:
        if bot_token is not None:
            fields["bot_token"] = bot_token
        if token_changed:
            # A token identifies one specific bot, so a replacement must never
            # inherit the previous bot's paired account.
            fields.update(
                chat_id="",
                chat_name="",
                chat_username="",
                bot_username="",
                last_error="",
                last_tested_at=None,
            )
        active = current["enabled"] if enabled is None else enabled
        resulting_token = "" if clear_token else (bot_token or current["bot_token"])
        resulting_chat = "" if token_changed else current["chat_id"]
        if active and not resulting_token:
            raise ValueError("add a bot token before enabling Telegram")
        if enabled is not None:
            fields["enabled"] = int(enabled)
        fields["connection_status"] = (
            "disabled" if not active
            else "configured" if resulting_chat
            else "waiting_pairing"
        )
    fields["updated_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE telegram_settings SET {sets} WHERE id = 1",
            tuple(fields.values()),
        )
        conn.commit()
    return get_telegram_settings()


def update_telegram_connection(
    status: str,
    *,
    bot_username: str | None = None,
    error: str = "",
    tested: bool = False,
) -> dict:
    status = str(status or "").strip()
    if status not in TELEGRAM_STATUSES:
        raise ValueError("Telegram connection status is not supported")
    fields: dict[str, object] = {
        "connection_status": status,
        "last_error": str(error or "")[:1000],
        "updated_at": _now(),
    }
    if bot_username is not None:
        fields["bot_username"] = str(bot_username or "").strip().lstrip("@")[:80]
    if tested:
        fields["last_tested_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE telegram_settings SET {sets} WHERE id = 1",
            tuple(fields.values()),
        )
        conn.commit()
    return get_telegram_settings()


def pair_telegram_chat(chat_id: int, name: str = "", username: str = "") -> dict:
    try:
        normalized_id = str(int(chat_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Telegram chat id is invalid") from exc
    with _connect() as conn:
        conn.execute(
            """
            UPDATE telegram_settings
            SET chat_id = ?, chat_name = ?, chat_username = ?,
                connection_status = 'connected', last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (
                normalized_id,
                " ".join(str(name or "").split())[:160],
                str(username or "").strip().lstrip("@")[:80],
                _now(),
            ),
        )
        conn.commit()
    return get_telegram_settings()


def clear_telegram_pairing() -> dict:
    current = get_telegram_settings(include_secret=True)
    status = "waiting_pairing" if current["enabled"] and current["bot_token"] else "disabled"
    with _connect() as conn:
        conn.execute(
            """
            UPDATE telegram_settings
            SET chat_id = '', chat_name = '', chat_username = '',
                connection_status = ?, last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (status, _now()),
        )
        conn.commit()
    return get_telegram_settings()


# -----------------------------------------------------------------------------
# Heartbeat configuration
# -----------------------------------------------------------------------------

def get_heartbeat_settings() -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM heartbeat_settings WHERE id = 1"
        ).fetchone()
    if row is None:
        return {
            "enabled": False,
            "interval_minutes": 30,
            "instructions": HEARTBEAT_DEFAULT_INSTRUCTIONS,
            "next_run_at": None,
            "last_run_at": None,
            "last_status": "never",
            "last_message": "",
            "last_error": "",
            "updated_at": None,
        }
    return {
        "enabled": bool(row["enabled"]),
        "interval_minutes": int(row["interval_minutes"] or 30),
        "instructions": row["instructions"] or HEARTBEAT_DEFAULT_INSTRUCTIONS,
        "next_run_at": row["next_run_at"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"] or "never",
        "last_message": row["last_message"] or "",
        "last_error": row["last_error"] or "",
        "updated_at": row["updated_at"],
    }


def update_heartbeat_settings(
    *,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
    instructions: str | None = None,
    selected_tools: list[dict] | None = None,
) -> dict:
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if interval_minutes is not None:
        if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int):
            raise ValueError("interval must be a whole number of minutes")
        if not 5 <= interval_minutes <= 1440:
            raise ValueError("interval must be between 5 and 1440 minutes")
    if instructions is not None:
        instructions = str(instructions or "").strip()
        if not instructions:
            raise ValueError("heartbeat instructions are required")
        if len(instructions) > 2000:
            raise ValueError("heartbeat instructions must be 2000 characters or fewer")

    normalized_tools: list[tuple[int, str]] | None = None
    if selected_tools is not None:
        if not isinstance(selected_tools, list):
            raise ValueError("selected_tools must be a list")
        normalized_tools = []
        seen: set[tuple[int, str]] = set()
        for entry in selected_tools:
            if not isinstance(entry, dict):
                raise ValueError("each heartbeat tool selection must be an object")
            try:
                subagent_id = int(entry.get("subagent_id"))
            except (TypeError, ValueError) as exc:
                raise ValueError("heartbeat tool selection has an invalid subagent") from exc
            tool_name = str(entry.get("tool_name") or "").strip()
            if not tool_name:
                raise ValueError("heartbeat tool selection has no tool name")
            key = (subagent_id, tool_name)
            if key not in seen:
                seen.add(key)
                normalized_tools.append(key)

    now = _now()
    with _connect() as conn:
        current = conn.execute(
            "SELECT * FROM heartbeat_settings WHERE id = 1"
        ).fetchone()
        active = bool(current["enabled"]) if enabled is None else enabled
        interval = (
            int(current["interval_minutes"] or 30)
            if interval_minutes is None
            else interval_minutes
        )
        if normalized_tools is not None:
            valid = {
                (int(row["subagent_id"]), row["name"])
                for row in conn.execute(
                    """
                    SELECT s.id AS subagent_id, t.name
                    FROM subagents s
                    JOIN mcp_server_tools t ON t.mcp_server_id = s.mcp_server_id
                    """
                )
            }
            unknown = [key for key in normalized_tools if key not in valid]
            if unknown:
                raise ValueError("one or more selected heartbeat tools are unavailable")
            confirmation_rules = {
                int(row["id"]): set(json.loads(row["confirm_tools"] or "[]"))
                for row in conn.execute("SELECT id, confirm_tools FROM subagents")
            }
            protected = [
                (agent_id, name)
                for agent_id, name in normalized_tools
                if "*" in confirmation_rules.get(agent_id, {"*"})
                or name in confirmation_rules.get(agent_id, {"*"})
            ]
            if protected:
                raise ValueError(
                    "tools that require confirmation cannot run in the heartbeat"
                )
            conn.execute("DELETE FROM heartbeat_tools")
            conn.executemany(
                """
                INSERT INTO heartbeat_tools (subagent_id, tool_name, created_at)
                VALUES (?, ?, ?)
                """,
                [(agent_id, name, now) for agent_id, name in normalized_tools],
            )
        fields = {"updated_at": now}
        if enabled is not None:
            fields["enabled"] = int(enabled)
        if interval_minutes is not None:
            fields["interval_minutes"] = interval_minutes
        if instructions is not None:
            fields["instructions"] = instructions
        # Saving an active schedule starts a fresh interval; disabling it
        # removes the due time so a stale wake-up cannot launch a run.
        fields["next_run_at"] = (
            (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat()
            if active
            else None
        )
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE heartbeat_settings SET {sets} WHERE id = 1",
            tuple(fields.values()),
        )
        conn.commit()
    return get_heartbeat_settings()


def get_heartbeat_capabilities() -> list[dict]:
    """Return cached MCP tools grouped by subagent for heartbeat configuration."""
    with _connect() as conn:
        selected = {
            (int(row["subagent_id"]), row["tool_name"])
            for row in conn.execute("SELECT subagent_id, tool_name FROM heartbeat_tools")
        }
        agents = conn.execute(
            """
            SELECT s.id, s.name, s.confirm_tools, s.mcp_server_id,
                   ms.connection_status
            FROM subagents s
            JOIN mcp_servers ms ON ms.id = s.mcp_server_id
            ORDER BY s.name
            """
        ).fetchall()
        tool_rows = conn.execute(
            """
            SELECT s.id AS subagent_id, t.name, t.description, t.position
            FROM subagents s
            JOIN mcp_server_tools t ON t.mcp_server_id = s.mcp_server_id
            ORDER BY s.name, t.position, t.id
            """
        ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in tool_rows:
        grouped.setdefault(int(row["subagent_id"]), []).append(
            {
                "name": row["name"],
                "description": row["description"] or "",
                "selected": (int(row["subagent_id"]), row["name"]) in selected,
            }
        )
    result = []
    for row in agents:
        try:
            protected = set(json.loads(row["confirm_tools"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            protected = {"*"}
        tools = grouped.get(int(row["id"]), [])
        for tool in tools:
            tool["requires_confirmation"] = (
                "*" in protected or tool["name"] in protected
            )
        result.append(
            {
                "id": int(row["id"]),
                "name": row["name"],
                "connection_status": row["connection_status"] or "untested",
                "tools": tools,
            }
        )
    return result


def get_heartbeat_targets() -> list[dict]:
    """Return resolved subagent specs with only their selected safe tools."""
    selected: dict[int, list[str]] = {}
    with _connect() as conn:
        for row in conn.execute(
            """
            SELECT ht.subagent_id, ht.tool_name
            FROM heartbeat_tools ht
            JOIN subagents s ON s.id = ht.subagent_id
            JOIN mcp_server_tools t
              ON t.mcp_server_id = s.mcp_server_id
             AND t.name = ht.tool_name
            ORDER BY ht.subagent_id, ht.tool_name
            """
        ):
            selected.setdefault(int(row["subagent_id"]), []).append(row["tool_name"])
    targets = []
    for spec in build_specs():
        chosen = selected.get(int(spec["id"]), [])
        protected = set(spec.get("confirm_tools") or [])
        safe = [name for name in chosen if "*" not in protected and name not in protected]
        if safe:
            target = dict(spec)
            target["allowed_tools"] = safe
            targets.append(target)
    return targets


def get_heartbeat_agent_report(subagent_id: int) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_report FROM heartbeat_agent_state WHERE subagent_id = ?",
            (subagent_id,),
        ).fetchone()
    return (row["last_report"] or "") if row else ""


def set_heartbeat_agent_report(subagent_id: int, report: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO heartbeat_agent_state (subagent_id, last_report, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(subagent_id) DO UPDATE SET
                last_report = excluded.last_report,
                updated_at = excluded.updated_at
            """,
            (subagent_id, str(report or "").strip()[:8000], _now()),
        )
        conn.commit()


def begin_heartbeat_run(trigger: str) -> int:
    started = _now()
    settings = get_heartbeat_settings()
    next_run = (
        datetime.now(timezone.utc)
        + timedelta(minutes=int(settings["interval_minutes"]))
    ).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO heartbeat_runs (trigger, started_at, status)
            VALUES (?, ?, 'running')
            """,
            (trigger, started),
        )
        conn.execute(
            """
            UPDATE heartbeat_settings
            SET last_run_at = ?, last_status = 'running', last_message = '',
                last_error = '', next_run_at = ?
            WHERE id = 1
            """,
            (started, next_run),
        )
        conn.commit()
        return int(cur.lastrowid)


def recover_interrupted_heartbeat_runs() -> None:
    """Close runs left in progress by a previous process shutdown or crash."""
    finished = _now()
    error = "The previous heartbeat check was interrupted before it finished."
    with _connect() as conn:
        running = conn.execute(
            "SELECT 1 FROM heartbeat_runs WHERE status = 'running' LIMIT 1"
        ).fetchone()
        if not running:
            return
        conn.execute(
            """
            UPDATE heartbeat_runs
            SET finished_at = ?, status = 'error', error = ?
            WHERE status = 'running'
            """,
            (finished, error),
        )
        conn.execute(
            """
            UPDATE heartbeat_settings
            SET last_status = 'error', last_error = ?
            WHERE id = 1
            """,
            (error,),
        )
        conn.commit()


def finish_heartbeat_run(
    run_id: int, *, status: str, message: str = "", error: str = ""
) -> dict:
    if status not in {"quiet", "alert", "error", "skipped"}:
        raise ValueError("invalid heartbeat run status")
    finished = _now()
    message = str(message or "").strip()[:8000]
    error = " ".join(str(error or "").split())[:2000]
    with _connect() as conn:
        conn.execute(
            """
            UPDATE heartbeat_runs
            SET finished_at = ?, status = ?, message = ?, error = ?
            WHERE id = ?
            """,
            (finished, status, message, error, run_id),
        )
        conn.execute(
            """
            UPDATE heartbeat_settings
            SET last_status = ?, last_message = ?, last_error = ?
            WHERE id = 1
            """,
            (status, message, error),
        )
        # Keep bounded local diagnostics rather than growing forever.
        conn.execute(
            """
            DELETE FROM heartbeat_runs
            WHERE id NOT IN (
                SELECT id FROM heartbeat_runs ORDER BY id DESC LIMIT 100
            )
            """
        )
        conn.commit()
    return get_heartbeat_settings()


def list_heartbeat_runs(limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM heartbeat_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


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
    try:
        cur = conn.execute(
            "INSERT INTO models (name, model, provider, base_url, api_key, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                _required(name, "name"),
                _required(model, "model ID"),
                (provider or "").strip(),
                _normalize_model_base_url(base_url),
                api_key or "",
                _now(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _friendly_integrity_error(exc) from exc
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
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_model(model_id)
    if "name" in fields:
        fields["name"] = _required(fields["name"], "name")
    if "model" in fields:
        fields["model"] = _required(fields["model"], "model ID")
    if "base_url" in fields:
        fields["base_url"] = _normalize_model_base_url(fields["base_url"])
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(
                f"UPDATE models SET {sets} WHERE id = ?",
                (*fields.values(), model_id),
            )
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
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

def _add_server(
    conn: sqlite3.Connection,
    name: str,
    connection: str,
    transport: str = "stdio",
    headers="{}",
    env="{}",
    description: str = "",
    setup_type: str = "",
    auth_scheme: str = "",
) -> int:
    transport, connection = _validate_transport(transport, connection)
    auth_scheme = str(auth_scheme or "").strip()
    if auth_scheme not in {"", "none", "bearer", "header", "custom"}:
        raise ValueError("authentication method is not supported")
    try:
        cur = conn.execute(
            """
            INSERT INTO mcp_servers
                (name, description, setup_type, transport, connection, headers, env,
                 auth_scheme, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _required(name, "name"),
                (description or "").strip(),
                (setup_type or "").strip(),
                transport,
                connection,
                _json_object(headers, "headers"),
                _json_object(env, "environment"),
                auth_scheme,
                _now(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _friendly_integrity_error(exc) from exc
    conn.commit()
    return cur.lastrowid


def add_server(
    name: str,
    connection: str,
    transport="stdio",
    headers="{}",
    env="{}",
    description: str = "",
    auth_scheme: str = "",
) -> dict:
    with _connect() as conn:
        sid = _add_server(
            conn,
            name,
            connection,
            transport,
            headers,
            env,
            description,
            auth_scheme=auth_scheme,
        )
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
    allowed = {
        "name",
        "connection",
        "transport",
        "headers",
        "env",
        "description",
        "auth_scheme",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_server(server_id)
    current = get_server(server_id)
    if current is None:
        return None
    transport, connection = _validate_transport(
        fields.get("transport", current["transport"]),
        fields.get("connection", current["connection"]),
    )
    if "transport" in fields or "connection" in fields:
        fields["transport"] = transport
        fields["connection"] = connection
    if "name" in fields:
        fields["name"] = _required(fields["name"], "name")
    if "headers" in fields:
        fields["headers"] = _json_object(fields["headers"], "headers")
    if "env" in fields:
        fields["env"] = _json_object(fields["env"], "environment")
    if "description" in fields:
        fields["description"] = (fields["description"] or "").strip()
    if "auth_scheme" in fields:
        fields["auth_scheme"] = str(fields["auth_scheme"] or "").strip()
        if fields["auth_scheme"] not in {"", "none", "bearer", "header", "custom"}:
            raise ValueError("authentication method is not supported")
    connection_fields = {"transport", "connection", "headers", "env"}
    connection_changed = any(
        key in fields and fields[key] != current.get(key)
        for key in connection_fields
    )
    if connection_changed:
        fields["connection_status"] = "stale"
        fields["last_error"] = ""
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(
                f"UPDATE mcp_servers SET {sets} WHERE id = ?",
                (*fields.values(), server_id),
            )
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
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


def get_server_tools_state(server_id: int) -> dict | None:
    """Return cached discovery state without contacting the MCP server."""
    server = get_server(server_id)
    if server is None:
        return None
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT name, description, input_schema, discovered_at
            FROM mcp_server_tools
            WHERE mcp_server_id = ?
            ORDER BY position, id
            """,
            (server_id,),
        ).fetchall()
    tools = []
    for row in rows:
        try:
            input_schema = json.loads(row["input_schema"] or "{}")
        except json.JSONDecodeError:
            input_schema = {}
        tools.append(
            {
                "name": row["name"],
                "description": row["description"],
                "input_schema": input_schema,
            }
        )
    return {
        "server_id": server_id,
        "status": server.get("connection_status") or "untested",
        "last_tested_at": server.get("last_tested_at"),
        "last_error": server.get("last_error") or "",
        "tools": tools,
    }


def save_server_tools(server_id: int, tools: list[dict]) -> dict:
    """Atomically replace a server's cached tool snapshot after a successful test."""
    if get_server(server_id) is None:
        raise ValueError("Server not found")
    discovered_at = _now()
    normalized = []
    seen = set()
    for position, tool in enumerate(tools):
        name = _required((tool or {}).get("name"), "tool name")
        if name in seen:
            continue
        seen.add(name)
        schema = (tool or {}).get("input_schema") or {}
        if not isinstance(schema, dict):
            schema = {}
        normalized.append(
            (
                server_id,
                name,
                str((tool or {}).get("description") or ""),
                json.dumps(schema, ensure_ascii=False, sort_keys=True),
                position,
                discovered_at,
            )
        )
    with _connect() as conn:
        conn.execute(
            "DELETE FROM mcp_server_tools WHERE mcp_server_id = ?", (server_id,)
        )
        conn.executemany(
            """
            INSERT INTO mcp_server_tools
                (mcp_server_id, name, description, input_schema, position, discovered_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            normalized,
        )
        conn.execute(
            """
            UPDATE mcp_servers
            SET connection_status = 'connected', last_tested_at = ?, last_error = ''
            WHERE id = ?
            """,
            (discovered_at, server_id),
        )
        conn.commit()
    return get_server_tools_state(server_id)


def record_server_test_failure(server_id: int, error: str) -> dict | None:
    """Record a failed test while preserving the last successful tool snapshot."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mcp_servers
            SET connection_status = 'failed', last_tested_at = ?, last_error = ?
            WHERE id = ?
            """,
            (_now(), " ".join(str(error or "Connection failed").split())[:1000], server_id),
        )
        conn.commit()
    return get_server_tools_state(server_id)


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
    confirm_tool_calls: bool = True,
    parent: str = "supervisor",
    confirm_tools=None,
    icon_data: bytes = b"",
    icon_mime: str = "",
    dedupe_tools=None,
) -> int:
    if confirm_tools is None:
        confirm_tools = ["*"] if _bool(confirm_tool_calls, "confirm_tool_calls") else []
    confirm_tools_json = _json_string_list(confirm_tools, "confirmation tools")
    dedupe_tools_json = _json_string_list(dedupe_tools or [], "duplicate protection tools")
    has_confirmations = bool(json.loads(confirm_tools_json))
    try:
        cur = conn.execute(
            """
            INSERT INTO subagents
                (name, description, system_prompt, icon_data, icon_mime,
                 model_id, mcp_server_id, confirm_tool_calls, confirm_tools,
                 dedupe_tools, parent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _required(name, "name"),
                _required(description, "description"),
                (system_prompt or "").strip(),
                bytes(icon_data or b""),
                (icon_mime or "").strip(),
                int(model_id),
                int(mcp_server_id),
                int(has_confirmations),
                confirm_tools_json,
                dedupe_tools_json,
                (parent or "supervisor").strip() or "supervisor",
                _now(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _friendly_integrity_error(exc) from exc
    conn.commit()
    return cur.lastrowid


def add_subagent(
    name: str,
    description: str,
    system_prompt: str,
    model_id: int,
    mcp_server_id: int,
    confirm_tool_calls: bool = True,
    parent: str = "supervisor",
    confirm_tools=None,
    icon_data: bytes = b"",
    icon_mime: str = "",
    dedupe_tools=None,
) -> dict:
    with _connect() as conn:
        aid = _add_subagent(
            conn, name, description, system_prompt, model_id, mcp_server_id,
            confirm_tool_calls, parent, confirm_tools, icon_data, icon_mime,
            dedupe_tools,
        )
        return get_subagent(aid)


_SUBAGENT_SELECT = """
    SELECT s.id, s.name, s.description, s.system_prompt,
           s.model_id, s.mcp_server_id, s.confirm_tool_calls, s.confirm_tools,
           s.dedupe_tools,
           s.parent, s.created_at,
           CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon,
           m.name AS model_name, m.model, m.provider, m.base_url, m.api_key,
           srv.name AS server_name, srv.transport, srv.connection,
           srv.headers, srv.env
    FROM subagents s
    JOIN models m ON s.model_id = m.id
    JOIN mcp_servers srv ON s.mcp_server_id = srv.id
"""


def get_subagent(subagent_id: int) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            f"{_SUBAGENT_SELECT} WHERE s.id = ?",
            (subagent_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_subagent_by_name(name: str) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            f"{_SUBAGENT_SELECT} WHERE s.name = ?",
            (name.strip(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def list_subagents() -> list[dict]:
    with _connect() as conn:
        cur = conn.execute(f"{_SUBAGENT_SELECT} ORDER BY s.name")
        return [dict(r) for r in cur.fetchall()]


def get_subagent_icon(subagent_id: int) -> tuple[bytes, str] | None:
    """Return the stored icon without including its bytes in registry responses."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT icon_data, icon_mime FROM subagents WHERE id = ?",
            (subagent_id,),
        ).fetchone()
        if not row or not row["icon_data"]:
            return None
        return bytes(row["icon_data"]), row["icon_mime"]


def update_subagent(subagent_id: int, **kwargs) -> dict | None:
    allowed = {
        "name", "description", "system_prompt", "model_id",
        "mcp_server_id", "confirm_tool_calls", "confirm_tools", "parent",
        "icon_data", "icon_mime", "dedupe_tools",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_subagent(subagent_id)
    if "name" in fields:
        fields["name"] = _required(fields["name"], "name")
    if "description" in fields:
        fields["description"] = _required(fields["description"], "description")
    if "icon_data" in fields:
        fields["icon_data"] = bytes(fields["icon_data"] or b"")
    if "icon_mime" in fields:
        fields["icon_mime"] = (fields["icon_mime"] or "").strip()
    if "confirm_tools" in fields:
        fields["confirm_tools"] = _json_string_list(
            fields["confirm_tools"], "confirmation tools"
        )
        fields["confirm_tool_calls"] = int(bool(json.loads(fields["confirm_tools"])))
    elif "confirm_tool_calls" in fields:
        enabled = _bool(fields["confirm_tool_calls"], "confirm_tool_calls")
        fields["confirm_tool_calls"] = int(enabled)
        fields["confirm_tools"] = '["*"]' if enabled else "[]"
    if "dedupe_tools" in fields:
        fields["dedupe_tools"] = _json_string_list(
            fields["dedupe_tools"], "duplicate protection tools"
        )
    with _connect() as conn:
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(
                f"UPDATE subagents SET {sets} WHERE id = ?",
                (*fields.values(), subagent_id),
            )
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
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
    expanded = os.path.expandvars(key)
    # os.path.expandvars leaves an unknown $NAME untouched. Treat that as an
    # unset optional key instead of sending the literal "$NAME" as a secret.
    if expanded == key and key.strip().startswith("$"):
        return ""
    return expanded


def _resolved_json_object(value: str) -> dict[str, str]:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("Stored MCP headers/environment must be JSON objects.")
    return {str(k): os.path.expandvars(str(v)) for k, v in parsed.items()}


def build_server_spec(server_id: int) -> dict | None:
    """Resolve one saved server into the shape the MCP client expects."""
    server = get_server(server_id)
    if server is None:
        return None
    return {
        "name": server["name"],
        "transport": server.get("transport") or "stdio",
        "connection": server["connection"],
        "headers": _resolved_json_object(server.get("headers") or "{}"),
        "env": _resolved_json_object(server.get("env") or "{}"),
    }


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
                "mcp_server_id": s["mcp_server_id"],
                "name": s["name"],
                "description": s.get("description") or f"Uses the {s['server_name']} MCP server.",
                "prompt": s["system_prompt"],

                "transport": s.get("transport") or "stdio",
                "connection": s["connection"],

                "headers": _resolved_json_object(s.get("headers") or "{}"),
                "env": _resolved_json_object(s.get("env") or "{}"),

                "parent": s.get("parent") or "supervisor",

                "model": (s.get("model") or "").strip() or s["model_name"],
                "base_url": s["base_url"],
                "api_key": _resolve_key(s.get("api_key") or ""),

                "confirm_tools": json.loads(
                    _json_string_list(
                        s.get("confirm_tools")
                        if s.get("confirm_tools") is not None
                        else (["*"] if s.get("confirm_tool_calls", 1) else []),
                        "confirmation tools",
                    )
                ),
                "dedupe_tools": json.loads(
                    _json_string_list(
                        s.get("dedupe_tools") or "[]",
                        "duplicate protection tools",
                    )
                ),
            }
        )
    return specs
