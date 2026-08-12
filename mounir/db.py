"""SQLite persistence for Mounir's local configuration and dynamic agents.

Core tables include:

- ``models``       reusable LLM presets (name, provider, base_url, api_key)
- ``mcp_servers``  reusable MCP server connections (transport + command/URL)
- ``subagents``    configurable agent definitions (name, prompt, model, MCP)
- ``subagent_connections`` legacy definition-level compatibility projection
- ``subagent_nodes`` independent placements and their placement-specific trees
- ``mcp_server_tools`` cached MCP capability metadata
- ``heartbeat_*`` heartbeat permissions, schedule, state, and bounded run log
- ``telegram_settings`` private Telegram bot configuration and pairing state
- ``whatsapp_settings`` private WhatsApp Cloud API configuration and pairing state

On first run, if ``~/.mounir/mcp_agents.json`` exists it is migrated into the
DB; after that the JSON file is ignored.

API keys are stored as you provide them. To avoid keeping raw secrets in the
DB, put an env var reference like ``$OPENAI_API_KEY`` or ``${OPENAI_API_KEY}``
and Mounir expands it at runtime.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import builtin_agents, config as cfg, default_agents

DB_PATH: Path = cfg.DATA_DIR / "mounir.db"
LEGACY_REGISTRY: Path = cfg.DATA_DIR / "mcp_agents.json"
MCP_TRANSPORTS = {"stdio", "streamable_http", "sse"}
MAX_SUBAGENT_DEPTH = 4
HEARTBEAT_DEFAULT_INSTRUCTIONS = (
    "Check my connected services for new items that genuinely need my attention. "
    "Ignore routine or unchanged information."
)


@dataclass(frozen=True)
class DeletionResult:
    """Outcome of a restricted delete, including what still uses the record."""

    status: str
    dependencies: tuple[str, ...] = ()

    @property
    def deleted(self) -> bool:
        return self.status == "deleted"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    # UI-entered credentials are stored locally in this DB. Restrict it to the
    # current OS user even when the process was started with a permissive umask.
    try:
        DB_PATH.chmod(0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    # WAL lets readers continue while a short configuration write is committed.
    # This matters when chat, heartbeats, and Agent Studio are active together.
    conn.execute("PRAGMA journal_mode = WAL")
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
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            parent_agent_id INTEGER
                REFERENCES subagents(id) ON DELETE RESTRICT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS subagent_connections (
            parent_agent_id INTEGER
                REFERENCES subagents(id) ON DELETE CASCADE,
            child_agent_id INTEGER NOT NULL
                REFERENCES subagents(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            CHECK (parent_agent_id IS NULL OR parent_agent_id != child_agent_id)
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_connections_dynamic
            ON subagent_connections (parent_agent_id, child_agent_id)
            WHERE parent_agent_id IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_connections_supervisor
            ON subagent_connections (child_agent_id)
            WHERE parent_agent_id IS NULL;

        CREATE INDEX IF NOT EXISTS idx_subagent_connections_child
            ON subagent_connections (child_agent_id);

        CREATE TABLE IF NOT EXISTS subagent_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_id INTEGER NOT NULL
                REFERENCES subagents(id) ON DELETE CASCADE,
            parent_node_id INTEGER
                REFERENCES subagent_nodes(id) ON DELETE RESTRICT,
            -- NULL inherits every tool; a JSON list is an exact node allowlist.
            enabled_tools TEXT,
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_nodes_dynamic
            ON subagent_nodes (parent_node_id, agent_id)
            WHERE parent_node_id IS NOT NULL;

        CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_nodes_supervisor
            ON subagent_nodes (agent_id)
            WHERE parent_node_id IS NULL;

        CREATE INDEX IF NOT EXISTS idx_subagent_nodes_parent
            ON subagent_nodes (parent_node_id);

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

        CREATE TABLE IF NOT EXISTS voice_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            stt_provider TEXT NOT NULL,
            stt_model TEXT NOT NULL,
            stt_base_url TEXT NOT NULL DEFAULT '',
            stt_api_key TEXT NOT NULL DEFAULT '',
            stt_language TEXT NOT NULL DEFAULT 'auto',
            tts_provider TEXT NOT NULL,
            tts_model TEXT NOT NULL,
            tts_base_url TEXT NOT NULL DEFAULT '',
            tts_api_key TEXT NOT NULL DEFAULT '',
            tts_language TEXT NOT NULL DEFAULT 'en-US',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS builtin_agent_settings (
            agent_key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            model_id INTEGER REFERENCES models(id),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS supervisor_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            model_id INTEGER NOT NULL REFERENCES models(id),
            updated_at TEXT NOT NULL
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
            notify_telegram INTEGER NOT NULL DEFAULT 1 CHECK (notify_telegram IN (0, 1)),
            notify_whatsapp INTEGER NOT NULL DEFAULT 0 CHECK (notify_whatsapp IN (0, 1)),
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS heartbeat_tools (
            subagent_id INTEGER NOT NULL
                REFERENCES subagents(id) ON DELETE CASCADE,
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (subagent_id, tool_name)
        );

        CREATE TABLE IF NOT EXISTS heartbeat_builtin_tools (
            builtin_key TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (builtin_key, tool_name)
        );

        CREATE TABLE IF NOT EXISTS heartbeat_agent_preferences (
            agent_key TEXT PRIMARY KEY,
            configured_at TEXT NOT NULL
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

        CREATE TABLE IF NOT EXISTS heartbeat_builtin_agent_state (
            builtin_key TEXT PRIMARY KEY,
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

        CREATE TABLE IF NOT EXISTS whatsapp_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            access_token TEXT NOT NULL DEFAULT '',
            phone_number_id TEXT NOT NULL DEFAULT '',
            business_account_id TEXT NOT NULL DEFAULT '',
            app_secret TEXT NOT NULL DEFAULT '',
            verify_token TEXT NOT NULL DEFAULT '',
            api_version TEXT NOT NULL DEFAULT 'v25.0',
            display_phone_number TEXT NOT NULL DEFAULT '',
            verified_name TEXT NOT NULL DEFAULT '',
            paired_phone TEXT NOT NULL DEFAULT '',
            paired_name TEXT NOT NULL DEFAULT '',
            connection_status TEXT NOT NULL DEFAULT 'disabled',
            last_error TEXT NOT NULL DEFAULT '',
            last_tested_at TEXT,
            webhook_verified_at TEXT,
            last_inbound_at TEXT,
            heartbeat_template_name TEXT NOT NULL DEFAULT '',
            heartbeat_template_language TEXT NOT NULL DEFAULT 'en_US',
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
        INSERT OR IGNORE INTO voice_settings
            (id, stt_provider, stt_model, stt_base_url, stt_api_key,
             stt_language, tts_provider, tts_model, tts_base_url,
             tts_api_key, tts_language, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "groq" if cfg.STT_BACKEND == "groq" else "local_whisper",
            cfg.GROQ_STT_MODEL if cfg.STT_BACKEND == "groq" else cfg.WHISPER_MODEL,
            cfg.GROQ_BASE_URL if cfg.STT_BACKEND == "groq" else "",
            "$GROQ_API_KEY" if cfg.STT_BACKEND == "groq" else "",
            cfg.WHISPER_LANGUAGE or "auto",
            "google" if cfg.TTS_BACKEND == "google" else "piper",
            cfg.GOOGLE_TTS_VOICE if cfg.TTS_BACKEND == "google" else cfg.PIPER_MODEL,
            "https://texttospeech.googleapis.com/v1" if cfg.TTS_BACKEND == "google" else "",
            "$GOOGLE_TTS_API_KEY" if cfg.TTS_BACKEND == "google" else "",
            cfg.GOOGLE_TTS_LANGUAGE,
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
    conn.executemany(
        """
        INSERT OR IGNORE INTO builtin_agent_settings
            (agent_key, model, updated_at)
        VALUES (?, ?, ?)
        """,
        [
            (item["key"], item["default_model"], _now())
            for item in builtin_agents.definitions()
        ],
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
    conn.execute(
        """
        INSERT OR IGNORE INTO whatsapp_settings
            (id, verify_token, updated_at)
        VALUES (1, ?, ?)
        """,
        (secrets.token_urlsafe(24), _now()),
    )
    # CREATE TABLE does not add columns to an existing SQLite table. Keep the
    # migrations explicit so upgrading an earlier feature-branch DB works.
    migrations = {
        "models": {"model": "TEXT"},
        "builtin_agent_settings": {
            "model_id": "INTEGER REFERENCES models(id)",
            "enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))",
        },
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
            "enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))",
            "parent_agent_id": "INTEGER REFERENCES subagents(id) ON DELETE RESTRICT",
        },
        "subagent_nodes": {
            "enabled_tools": "TEXT",
        },
        "heartbeat_settings": {
            "interval_minutes": "INTEGER NOT NULL DEFAULT 30",
            "instructions": "TEXT NOT NULL DEFAULT ''",
            "next_run_at": "TEXT",
            "last_run_at": "TEXT",
            "last_status": "TEXT NOT NULL DEFAULT 'never'",
            "last_message": "TEXT NOT NULL DEFAULT ''",
            "last_error": "TEXT NOT NULL DEFAULT ''",
            "notify_telegram": "INTEGER NOT NULL DEFAULT 1 CHECK (notify_telegram IN (0, 1))",
            "notify_whatsapp": "INTEGER NOT NULL DEFAULT 0 CHECK (notify_whatsapp IN (0, 1))",
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
    # Upgrade the short-lived text parent field used by earlier builds. Names
    # that cannot be resolved safely remain direct children of Mounir.
    subagent_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(subagents)")
    }
    if "parent" in subagent_columns:
        conn.execute(
            """
            UPDATE subagents AS child
            SET parent_agent_id = (
                SELECT parent.id FROM subagents AS parent
                WHERE lower(parent.name) = lower(trim(child.parent))
                  AND parent.id != child.id
                LIMIT 1
            )
            WHERE child.parent_agent_id IS NULL
              AND child.parent IS NOT NULL
              AND lower(trim(child.parent)) NOT IN ('', 'supervisor')
            """
        )
        # The column cannot be dropped safely in place on an existing SQLite
        # database. Neutralize it so a later init cannot undo a user reparent.
        conn.execute("UPDATE subagents SET parent = 'supervisor'")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagents_parent ON subagents(parent_agent_id)"
    )
    # Convert the old single-parent tree exactly once. A nullable parent in the
    # connection table represents a direct connection from Mounir.
    connection_migration_key = "subagent_connections_many_to_many_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (connection_migration_key,)
    ).fetchone() is None:
        conn.execute(
            """
            INSERT OR IGNORE INTO subagent_connections
                (parent_agent_id, child_agent_id, created_at)
            SELECT parent_agent_id, id, ? FROM subagents
            """,
            (_now(),),
        )
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (connection_migration_key, _now()),
        )
    # Turn definition-level connections into placement-level nodes. When an
    # agent already appears more than once, attach each historical child to
    # the newest placement that existed when that connection was created.
    node_migration_key = "subagent_nodes_per_placement_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (node_migration_key,)
    ).fetchone() is None:
        legacy_edges = [
            dict(row)
            for row in conn.execute(
                """
                SELECT parent_agent_id, child_agent_id, created_at
                FROM subagent_connections
                ORDER BY created_at, rowid
                """
            )
        ]
        placements: dict[int, list[tuple[int, str]]] = {}
        pending = []
        for edge in legacy_edges:
            parent_agent_id = edge["parent_agent_id"]
            child_agent_id = int(edge["child_agent_id"])
            created_at = edge.get("created_at") or _now()
            if parent_agent_id is None:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO subagent_nodes
                        (agent_id, parent_node_id, created_at)
                    VALUES (?, NULL, ?)
                    """,
                    (child_agent_id, created_at),
                )
                node = conn.execute(
                    """
                    SELECT id, created_at FROM subagent_nodes
                    WHERE agent_id = ? AND parent_node_id IS NULL
                    """,
                    (child_agent_id,),
                ).fetchone()
                if node:
                    placements.setdefault(child_agent_id, []).append(
                        (int(node["id"]), node["created_at"])
                    )
            else:
                pending.append(edge)
        while pending:
            progressed = False
            remaining = []
            for edge in pending:
                parent_agent_id = int(edge["parent_agent_id"])
                available = placements.get(parent_agent_id, [])
                if not available:
                    remaining.append(edge)
                    continue
                created_at = edge.get("created_at") or _now()
                eligible = [item for item in available if item[1] <= created_at]
                parent_node_id = max(eligible or available, key=lambda item: item[1])[0]
                child_agent_id = int(edge["child_agent_id"])
                conn.execute(
                    """
                    INSERT OR IGNORE INTO subagent_nodes
                        (agent_id, parent_node_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (child_agent_id, parent_node_id, created_at),
                )
                node = conn.execute(
                    """
                    SELECT id, created_at FROM subagent_nodes
                    WHERE agent_id = ? AND parent_node_id = ?
                    """,
                    (child_agent_id, parent_node_id),
                ).fetchone()
                if node:
                    placements.setdefault(child_agent_id, []).append(
                        (int(node["id"]), node["created_at"])
                    )
                progressed = True
            if not progressed:
                break
            pending = remaining
        # Keep every definition reachable even if a very old database contains
        # a broken relationship that could not be reconstructed.
        conn.execute(
            """
            INSERT OR IGNORE INTO subagent_nodes (agent_id, parent_node_id, created_at)
            SELECT id, NULL, ? FROM subagents
            WHERE id NOT IN (SELECT agent_id FROM subagent_nodes)
            """,
            (_now(),),
        )
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (node_migration_key, _now()),
        )
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


def _merge_masked_json_object(value, current, field: str) -> str:
    """Preserve stored values represented by blank placeholders in edit forms.

    Removing a key still removes its credential; only a present key with an empty
    value inherits the existing value.
    """
    incoming = json.loads(_json_object(value, field))
    existing = json.loads(_json_object(current, field))
    merged = {
        key: existing.get(key, "") if item == "" else item
        for key, item in incoming.items()
    }
    return json.dumps(merged, ensure_ascii=False, sort_keys=True)


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


def _masked_json_object(value) -> tuple[str, bool]:
    """Keep credential names useful to the UI without returning their values."""
    try:
        parsed = json.loads(value or "{}") if isinstance(value, str) else (value or {})
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    configured = any(str(item) for item in parsed.values())
    masked = {str(key): "" for key in parsed}
    return json.dumps(masked, ensure_ascii=False, sort_keys=True), configured


def model_for_api(model: dict | None) -> dict | None:
    """Return a model record safe for a management API response."""
    if model is None:
        return None
    result = dict(model)
    result["api_key_configured"] = bool(result.pop("api_key", ""))
    return result


def server_for_api(server: dict | None) -> dict | None:
    """Return MCP metadata and credential names, never credential values."""
    if server is None:
        return None
    result = dict(server)
    result["headers"], headers_configured = _masked_json_object(result.get("headers"))
    result["env"], env_configured = _masked_json_object(result.get("env"))
    result["headers_configured"] = headers_configured
    result["env_configured"] = env_configured
    result["credentials_configured"] = headers_configured or env_configured
    return result


def subagent_for_api(subagent: dict | None) -> dict | None:
    """Remove joined model/server credentials from a subagent API response."""
    if subagent is None:
        return None
    result = dict(subagent)
    result["api_key_configured"] = bool(result.pop("api_key", ""))
    headers = result.pop("headers", "{}")
    env = result.pop("env", "{}")
    _, headers_configured = _masked_json_object(headers)
    _, env_configured = _masked_json_object(env)
    result["mcp_credentials_configured"] = headers_configured or env_configured
    result["mcp_server_name"] = result.get("server_name", "")
    return result


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


def _active_supervisor_model_defaults() -> dict:
    if cfg.USE_MISTRAL:
        return {
            "provider": "Mistral",
            "model": cfg.MISTRAL_MODEL,
            "base_url": cfg.MISTRAL_BASE_URL,
            "api_key": "$MISTRAL_API_KEY",
        }
    if cfg.USE_GROQ:
        return {
            "provider": "Groq",
            "model": cfg.GROQ_MODEL,
            "base_url": cfg.GROQ_BASE_URL,
            "api_key": "$GROQ_API_KEY",
        }
    return {
        "provider": "Ollama (local)",
        "model": cfg.MODEL,
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
    }


def _supervisor_provider_supported(provider: str) -> bool:
    normalized = str(provider or "").strip().lower()
    return any(name in normalized for name in ("mistral", "groq", "ollama"))


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
    """Create tables and migrate only configuration the user already owns."""
    existing_installation = DB_PATH.exists()
    with _connect() as conn:
        _init_schema(conn)
        _migrate_legacy(conn)
        # Email and Researcher used to be hard-coded specialists. Preserve the
        # one-time conversion for upgrades, but never populate a fresh user's
        # intentionally empty registry.
        registry_policy_key = "user_owned_empty_registry_v1"
        registry_policy_set = conn.execute(
            "SELECT 1 FROM app_meta WHERE key = ?", (registry_policy_key,)
        ).fetchone()
        if not registry_policy_set:
            if existing_installation:
                _migrate_builtin_email(conn)
                _migrate_builtin_researcher(conn)
            conn.execute(
                "INSERT INTO app_meta (key, value) VALUES (?, ?)",
                (registry_policy_key, _now()),
            )
            conn.commit()
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
# Voice configuration
# -----------------------------------------------------------------------------

VOICE_PROVIDERS = {
    "stt": {"local_whisper", "groq"},
    "tts": {"piper", "google"},
}


def get_voice_settings(*, include_secrets: bool = False) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM voice_settings WHERE id = 1").fetchone()
    if row is None:
        return {"stt": {}, "tts": {}}
    result = {}
    for kind in ("stt", "tts"):
        secret = row[f"{kind}_api_key"] or ""
        item = {
            "provider": row[f"{kind}_provider"],
            "model": row[f"{kind}_model"],
            "base_url": row[f"{kind}_base_url"] or "",
            "language": row[f"{kind}_language"] or "auto",
            "api_key_configured": bool(secret),
        }
        if include_secrets:
            item["api_key"] = secret
        result[kind] = item
    result["updated_at"] = row["updated_at"]
    return result


def get_voice_runtime(kind: str) -> dict:
    normalized = str(kind or "").strip().lower()
    if normalized not in VOICE_PROVIDERS:
        raise ValueError("voice type is not supported")
    settings = get_voice_settings(include_secrets=True)[normalized]
    settings["api_key"] = _resolve_key(settings.get("api_key") or "")
    return settings


def update_voice_settings(*, stt=None, tts=None) -> dict:
    updates: dict[str, object] = {}
    current = get_voice_settings(include_secrets=True)
    for kind, supplied in (("stt", stt), ("tts", tts)):
        if supplied is None:
            continue
        if not isinstance(supplied, dict):
            raise ValueError(f"{kind.upper()} configuration must be an object")
        provider = str(supplied.get("provider") or "").strip().lower()
        if provider not in VOICE_PROVIDERS[kind]:
            raise ValueError(f"{kind.upper()} provider is not supported")
        model = _required(supplied.get("model"), f"{kind.upper()} model")
        language = str(supplied.get("language") or "auto").strip()
        if len(language) > 32:
            raise ValueError(f"{kind.upper()} language is too long")
        base_url = str(supplied.get("base_url") or "").strip()
        cloud = provider in {"groq", "google"}
        if cloud:
            if provider == "groq":
                base_url = base_url.rstrip("/").removesuffix("/audio/transcriptions")
            else:
                base_url = base_url.rstrip("/").removesuffix("/text:synthesize")
            base_url = _normalize_model_base_url(base_url)
        else:
            base_url = ""
        updates.update(
            {
                f"{kind}_provider": provider,
                f"{kind}_model": model,
                f"{kind}_base_url": base_url,
                f"{kind}_language": language or "auto",
            }
        )
        api_key = supplied.get("api_key")
        if api_key is not None and str(api_key).strip():
            updates[f"{kind}_api_key"] = str(api_key).strip()
        elif cloud and not current[kind].get("api_key"):
            raise ValueError(f"{kind.upper()} API key is required for this provider")
    if not updates:
        return get_voice_settings()
    updates["updated_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE voice_settings SET {sets} WHERE id = 1",
            tuple(updates.values()),
        )
        conn.commit()
    return get_voice_settings()


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
# WhatsApp Cloud API configuration
# -----------------------------------------------------------------------------

WHATSAPP_STATUSES = {"disabled", "incomplete", "configured", "connected", "error"}


def get_whatsapp_settings(*, include_secret: bool = False) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM whatsapp_settings WHERE id = 1"
        ).fetchone()
    if row is None:
        result = {
            "id": 1,
            "enabled": False,
            "access_token": "",
            "phone_number_id": "",
            "business_account_id": "",
            "app_secret": "",
            "verify_token": "",
            "api_version": "v25.0",
            "display_phone_number": "",
            "verified_name": "",
            "paired_phone": "",
            "paired_name": "",
            "connection_status": "disabled",
            "last_error": "",
            "last_tested_at": None,
            "webhook_verified_at": None,
            "last_inbound_at": None,
            "heartbeat_template_name": "",
            "heartbeat_template_language": "en_US",
            "updated_at": None,
        }
    else:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
    result["token_configured"] = bool(result["access_token"])
    result["app_secret_configured"] = bool(result["app_secret"])
    result["credentials_configured"] = bool(
        result["access_token"]
        and result["phone_number_id"]
        and result["business_account_id"]
        and result["app_secret"]
    )
    result["webhook_verified"] = bool(result["webhook_verified_at"])
    result["paired"] = bool(result["paired_phone"])
    result["paired_phone_hint"] = (
        f"••••{result['paired_phone'][-4:]}" if result["paired_phone"] else ""
    )
    if not include_secret:
        result.pop("access_token", None)
        result.pop("app_secret", None)
        result.pop("paired_phone", None)
    return result


def update_whatsapp_settings(
    *,
    enabled: bool | None = None,
    access_token: str | None = None,
    phone_number_id: str | None = None,
    business_account_id: str | None = None,
    app_secret: str | None = None,
    api_version: str | None = None,
    heartbeat_template_name: str | None = None,
    heartbeat_template_language: str | None = None,
    clear_credentials: bool = False,
    regenerate_verify_token: bool = False,
) -> dict:
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    current = get_whatsapp_settings(include_secret=True)
    fields: dict[str, object] = {}
    if clear_credentials:
        fields.update(
            enabled=0,
            access_token="",
            phone_number_id="",
            business_account_id="",
            app_secret="",
            display_phone_number="",
            verified_name="",
            paired_phone="",
            paired_name="",
            connection_status="disabled",
            last_error="",
            last_tested_at=None,
            webhook_verified_at=None,
            last_inbound_at=None,
            heartbeat_template_name="",
            heartbeat_template_language="en_US",
            verify_token=secrets.token_urlsafe(24),
        )
    else:
        supplied = {
            "access_token": access_token,
            "phone_number_id": phone_number_id,
            "business_account_id": business_account_id,
            "app_secret": app_secret,
            "heartbeat_template_name": heartbeat_template_name,
            "heartbeat_template_language": heartbeat_template_language,
        }
        limits = {
            "access_token": 4096,
            "phone_number_id": 80,
            "business_account_id": 80,
            "app_secret": 512,
            "heartbeat_template_name": 160,
            "heartbeat_template_language": 32,
        }
        for key, value in supplied.items():
            if value is None:
                continue
            normalized = str(value or "").strip()
            if key in {"access_token", "phone_number_id", "business_account_id", "app_secret"} and not normalized:
                raise ValueError(f"{key.replace('_', ' ')} is required")
            if len(normalized) > limits[key]:
                raise ValueError(f"{key.replace('_', ' ')} is too long")
            fields[key] = normalized
        if api_version is not None:
            normalized_version = str(api_version or "").strip().lower()
            if normalized_version.startswith("v"):
                normalized_version = normalized_version[1:]
            parts = normalized_version.split(".")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError("Graph API version must look like v25.0")
            fields["api_version"] = f"v{int(parts[0])}.{int(parts[1])}"
        if regenerate_verify_token:
            fields["verify_token"] = secrets.token_urlsafe(24)
            fields["webhook_verified_at"] = None

        identity_changed = any(
            key in fields and fields[key] != current[key]
            for key in ("phone_number_id", "business_account_id")
        )
        signature_changed = (
            "app_secret" in fields and fields["app_secret"] != current["app_secret"]
        )
        credentials_changed = identity_changed or signature_changed or (
            "access_token" in fields and fields["access_token"] != current["access_token"]
        )
        if identity_changed:
            fields.update(
                display_phone_number="",
                verified_name="",
                paired_phone="",
                paired_name="",
                last_inbound_at=None,
            )
        if identity_changed or signature_changed:
            fields["webhook_verified_at"] = None
        if credentials_changed:
            fields.update(last_error="", last_tested_at=None)

        active = current["enabled"] if enabled is None else enabled
        resulting = {
            key: fields.get(key, current[key])
            for key in ("access_token", "phone_number_id", "business_account_id", "app_secret")
        }
        complete = all(resulting.values())
        if active and not complete:
            raise ValueError(
                "add the access token, phone number ID, business account ID, and app secret before enabling WhatsApp"
            )
        if enabled is not None:
            fields["enabled"] = int(enabled)
        webhook_verified = fields.get(
            "webhook_verified_at", current["webhook_verified_at"]
        )
        fields["connection_status"] = (
            "disabled" if not active
            else "incomplete" if not complete
            else "connected" if webhook_verified and not credentials_changed
            else "configured"
        )

    fields["updated_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE whatsapp_settings SET {sets} WHERE id = 1",
            tuple(fields.values()),
        )
        conn.commit()
    return get_whatsapp_settings()


def update_whatsapp_connection(
    status: str,
    *,
    display_phone_number: str | None = None,
    verified_name: str | None = None,
    error: str = "",
    tested: bool = False,
    webhook_verified: bool = False,
) -> dict:
    status = str(status or "").strip()
    if status not in WHATSAPP_STATUSES:
        raise ValueError("WhatsApp connection status is not supported")
    fields: dict[str, object] = {
        "connection_status": status,
        "last_error": str(error or "")[:1000],
        "updated_at": _now(),
    }
    if display_phone_number is not None:
        fields["display_phone_number"] = str(display_phone_number or "").strip()[:80]
    if verified_name is not None:
        fields["verified_name"] = " ".join(str(verified_name or "").split())[:160]
    if tested:
        fields["last_tested_at"] = _now()
    if webhook_verified:
        fields["webhook_verified_at"] = _now()
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE whatsapp_settings SET {sets} WHERE id = 1",
            tuple(fields.values()),
        )
        conn.commit()
    return get_whatsapp_settings()


def pair_whatsapp_phone(phone: str, name: str = "") -> dict:
    normalized = "".join(character for character in str(phone or "") if character.isdigit())
    if not 6 <= len(normalized) <= 20:
        raise ValueError("WhatsApp phone number is invalid")
    with _connect() as conn:
        conn.execute(
            """
            UPDATE whatsapp_settings
            SET paired_phone = ?, paired_name = ?, last_inbound_at = ?,
                connection_status = 'connected', last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (normalized, " ".join(str(name or "").split())[:160], _now(), _now()),
        )
        conn.commit()
    return get_whatsapp_settings()


def clear_whatsapp_pairing() -> dict:
    current = get_whatsapp_settings(include_secret=True)
    status = "configured" if current["enabled"] and current["credentials_configured"] else "disabled"
    with _connect() as conn:
        conn.execute(
            """
            UPDATE whatsapp_settings
            SET paired_phone = '', paired_name = '', last_inbound_at = NULL,
                connection_status = ?, last_error = '', updated_at = ?
            WHERE id = 1
            """,
            (status, _now()),
        )
        conn.commit()
    return get_whatsapp_settings()


def mark_whatsapp_inbound(phone: str, name: str = "") -> dict:
    current = get_whatsapp_settings(include_secret=True)
    normalized = "".join(character for character in str(phone or "") if character.isdigit())
    if not current["paired_phone"] or normalized != current["paired_phone"]:
        return get_whatsapp_settings()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE whatsapp_settings
            SET paired_name = CASE WHEN ? = '' THEN paired_name ELSE ? END,
                last_inbound_at = ?, updated_at = ?
            WHERE id = 1
            """,
            (str(name or "").strip(), " ".join(str(name or "").split())[:160], _now(), _now()),
        )
        conn.commit()
    return get_whatsapp_settings()


# -----------------------------------------------------------------------------
# Supervisor and built-in specialist configuration
# -----------------------------------------------------------------------------

def get_supervisor_runtime(fallback_model: str = "") -> dict:
    """Return the database-managed runtime used by Mounir's supervisor."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT m.id AS model_id, m.model, m.provider,
                       m.base_url, m.api_key
                FROM supervisor_settings s
                JOIN models m ON m.id = s.model_id
                WHERE s.id = 1
                """
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row:
        return {
            "model_id": row["model_id"],
            "model": row["model"],
            "provider": row["provider"],
            "base_url": row["base_url"],
            "api_key": _resolve_key(row["api_key"] or ""),
        }

    defaults = _active_supervisor_model_defaults()
    secret = ""
    if defaults["provider"] == "Mistral":
        secret = cfg.MISTRAL_API_KEY
    elif defaults["provider"] == "Groq":
        secret = cfg.GROQ_API_KEY
    return {
        "model_id": None,
        "model": defaults["model"] or fallback_model,
        "provider": defaults["provider"],
        "base_url": defaults["base_url"],
        "api_key": secret,
    }


def get_supervisor_config() -> dict:
    runtime = get_supervisor_runtime(cfg.MODEL)
    options = [
        {
            "id": model["id"],
            "model": model["model"],
            "label": f"{model['name']} — {model['model']}",
        }
        for model in list_models()
        if _supervisor_provider_supported(model.get("provider", ""))
    ]
    return {
        "model_id": runtime["model_id"],
        "model": runtime["model"],
        "provider": runtime["provider"],
        "model_options": options,
    }


def update_supervisor_model(model_id: int) -> dict:
    try:
        requested_id = int(model_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("choose a model") from exc
    selected = get_model(requested_id)
    if selected is None or not _supervisor_provider_supported(
        selected.get("provider", "")
    ):
        raise ValueError("choose a configured Mistral, Groq, or Ollama model")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO supervisor_settings (id, model_id, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                model_id = excluded.model_id,
                updated_at = excluded.updated_at
            """,
            (requested_id, _now()),
        )
        conn.commit()
    return get_supervisor_config()

def get_builtin_agent_model(agent_key: str, fallback: str = "") -> str:
    key = str(agent_key or "").removeprefix("builtin:").strip()
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(m.model, s.model) AS model
                FROM builtin_agent_settings s
                LEFT JOIN models m ON m.id = s.model_id
                WHERE s.agent_key = ?
                """,
                (key,),
            ).fetchone()
    except sqlite3.OperationalError:
        return fallback
    return (row["model"] or fallback) if row else fallback


def get_builtin_agent_runtime(
    agent_key: str,
    *,
    fallback_model: str,
    fallback_base_url: str,
    fallback_api_key: str,
) -> dict:
    """Resolve the assigned Model record, falling back to the native adapter."""
    key = str(agent_key or "").removeprefix("builtin:").strip()
    try:
        with _connect() as conn:
            selected = conn.execute(
                """
                SELECT m.model, m.base_url, m.api_key
                FROM builtin_agent_settings s
                JOIN models m ON m.id = s.model_id
                WHERE s.agent_key = ?
                """,
                (key,),
            ).fetchone()
    except sqlite3.OperationalError:
        selected = None
    if selected:
        return {
            "model": selected["model"],
            "base_url": selected["base_url"],
            "api_key": _resolve_key(selected["api_key"] or ""),
        }
    return {
        "model": fallback_model,
        "base_url": fallback_base_url,
        "api_key": fallback_api_key,
    }


def list_builtin_agents() -> list[dict]:
    capabilities = {
        item["builtin_key"]: item for item in builtin_agents.capabilities()
    }
    models = list_models()
    result = []
    for definition in builtin_agents.definitions():
        key = definition["key"]
        compatible = [
            model for model in models
            if builtin_agents.provider_matches(key, model.get("provider", ""))
        ]
        options = [
            {
                "id": model["id"],
                "model": model["model"],
                "label": f"{model['name']} — {model['model']}",
            }
            for model in compatible
        ]
        with _connect() as conn:
            setting = conn.execute(
                """
                SELECT s.model_id, s.enabled,
                       COALESCE(m.model, s.model) AS model
                FROM builtin_agent_settings s
                LEFT JOIN models m ON m.id = s.model_id
                WHERE s.agent_key = ?
                """,
                (key,),
            ).fetchone()
        capability = capabilities[key]
        result.append(
            {
                **definition,
                "model_id": setting["model_id"] if setting else None,
                "model": (
                    setting["model"] if setting else definition["default_model"]
                ),
                "enabled": bool(setting["enabled"]) if setting else True,
                "model_options": options,
                "tools": capability["tools"],
            }
        )
    return result


def update_builtin_agent(
    agent_key: str,
    *,
    model_id: int | None = None,
    enabled: bool | None = None,
) -> dict:
    definition = builtin_agents.definition(agent_key)
    if definition is None:
        raise ValueError("built-in specialist was not found")
    if model_id is None and enabled is None:
        raise ValueError("provide a model or availability change")
    selected = None
    requested_id = None
    if model_id is not None:
        try:
            requested_id = int(model_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("choose a model") from exc
        selected = get_model(requested_id)
        if selected is None or not builtin_agents.provider_matches(
            definition["key"], selected.get("provider", "")
        ):
            raise ValueError(
                f"choose a configured {definition['provider']} model"
            )
    normalized_enabled = (
        int(_bool(enabled, "enabled")) if enabled is not None else None
    )
    with _connect() as conn:
        if selected is not None:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET model = ?, model_id = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (selected["model"], requested_id, _now(), definition["key"]),
            )
        if normalized_enabled is not None:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET enabled = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (normalized_enabled, _now(), definition["key"]),
            )
        conn.commit()
    return next(
        agent for agent in list_builtin_agents()
        if agent["key"] == definition["key"]
    )


def update_builtin_agent_model(agent_key: str, model_id: int) -> dict:
    """Backward-compatible model-only update used by existing callers."""
    return update_builtin_agent(agent_key, model_id=model_id)


def is_builtin_agent_enabled(agent_key: str) -> bool:
    key = str(agent_key or "").removeprefix("builtin:").strip()
    with _connect() as conn:
        row = conn.execute(
            "SELECT enabled FROM builtin_agent_settings WHERE agent_key = ?",
            (key,),
        ).fetchone()
    return bool(row and row["enabled"])


def enabled_builtin_agent_keys() -> set[str]:
    with _connect() as conn:
        return {
            str(row["agent_key"])
            for row in conn.execute(
                "SELECT agent_key FROM builtin_agent_settings WHERE enabled = 1"
            )
        }


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
            "notify_telegram": True,
            "notify_whatsapp": False,
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
        "notify_telegram": bool(row["notify_telegram"]),
        "notify_whatsapp": bool(row["notify_whatsapp"]),
        "updated_at": row["updated_at"],
    }


def update_heartbeat_settings(
    *,
    enabled: bool | None = None,
    interval_minutes: int | None = None,
    instructions: str | None = None,
    selected_tools: list[dict] | None = None,
    notify_telegram: bool | None = None,
    notify_whatsapp: bool | None = None,
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
    for field_name, value in (
        ("notify_telegram", notify_telegram),
        ("notify_whatsapp", notify_whatsapp),
    ):
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{field_name} must be true or false")

    normalized_tools: list[tuple[str, str]] | None = None
    if selected_tools is not None:
        if not isinstance(selected_tools, list):
            raise ValueError("selected_tools must be a list")
        normalized_tools = []
        seen: set[tuple[str, str]] = set()
        for entry in selected_tools:
            if not isinstance(entry, dict):
                raise ValueError("each heartbeat tool selection must be an object")
            agent_key = str(entry.get("agent_key") or "").strip()
            if not agent_key:
                try:
                    agent_key = f"mcp:{int(entry.get('subagent_id'))}"
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "heartbeat tool selection has an invalid subagent"
                    ) from exc
            tool_name = str(entry.get("tool_name") or "").strip()
            if not tool_name:
                raise ValueError("heartbeat tool selection has no tool name")
            key = (agent_key, tool_name)
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
                (f"mcp:{int(row['subagent_id'])}", row["name"])
                for row in conn.execute(
                    """
                    SELECT s.id AS subagent_id, t.name
                    FROM subagents s
                    JOIN mcp_server_tools t ON t.mcp_server_id = s.mcp_server_id
                    WHERE s.enabled = 1
                    """
                )
            }
            confirmation_rules = {
                f"mcp:{int(row['id'])}": set(
                    json.loads(row["confirm_tools"] or "[]")
                )
                for row in conn.execute(
                    "SELECT id, confirm_tools FROM subagents WHERE enabled = 1"
                )
            }
            active_builtin_keys = enabled_builtin_agent_keys()
            builtin_capabilities = [
                agent for agent in builtin_agents.capabilities()
                if agent["builtin_key"] in active_builtin_keys
            ]
            for agent in builtin_capabilities:
                confirmation_rules[agent["key"]] = {
                    tool["name"]
                    for tool in agent["tools"]
                    if tool["requires_confirmation"]
                }
                valid.update(
                    (agent["key"], tool["name"])
                    for tool in agent["tools"]
                )
            unknown = [key for key in normalized_tools if key not in valid]
            if unknown:
                raise ValueError("one or more selected heartbeat tools are unavailable")
            protected = [
                (agent_key, name)
                for agent_key, name in normalized_tools
                if "*" in confirmation_rules.get(agent_key, {"*"})
                or name in confirmation_rules.get(agent_key, {"*"})
            ]
            if protected:
                raise ValueError(
                    "tools that require confirmation cannot run in the heartbeat"
                )
            conn.execute("DELETE FROM heartbeat_tools")
            conn.execute("DELETE FROM heartbeat_builtin_tools")
            conn.executemany(
                """
                INSERT INTO heartbeat_tools (subagent_id, tool_name, created_at)
                VALUES (?, ?, ?)
                """,
                [
                    (int(agent_key.removeprefix("mcp:")), name, now)
                    for agent_key, name in normalized_tools
                    if agent_key.startswith("mcp:")
                ],
            )
            conn.executemany(
                """
                INSERT INTO heartbeat_builtin_tools
                    (builtin_key, tool_name, created_at)
                VALUES (?, ?, ?)
                """,
                [
                    (agent_key, name, now)
                    for agent_key, name in normalized_tools
                    if agent_key.startswith("builtin:")
                ],
            )
            conn.executemany(
                """
                INSERT INTO heartbeat_agent_preferences (agent_key, configured_at)
                VALUES (?, ?)
                ON CONFLICT(agent_key) DO UPDATE SET
                    configured_at = excluded.configured_at
                """,
                [(agent_key, now) for agent_key in confirmation_rules],
            )
        fields = {"updated_at": now}
        if enabled is not None:
            fields["enabled"] = int(enabled)
        if interval_minutes is not None:
            fields["interval_minutes"] = interval_minutes
        if instructions is not None:
            fields["instructions"] = instructions
        if notify_telegram is not None:
            fields["notify_telegram"] = int(notify_telegram)
        if notify_whatsapp is not None:
            fields["notify_whatsapp"] = int(notify_whatsapp)
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
    """Return built-in and cached MCP tools grouped for heartbeat configuration."""
    with _connect() as conn:
        selected = {
            (f"mcp:{int(row['subagent_id'])}", row["tool_name"])
            for row in conn.execute("SELECT subagent_id, tool_name FROM heartbeat_tools")
        }
        selected.update(
            (row["builtin_key"], row["tool_name"])
            for row in conn.execute(
                "SELECT builtin_key, tool_name FROM heartbeat_builtin_tools"
            )
        )
        configured = {
            row["agent_key"]
            for row in conn.execute(
                "SELECT agent_key FROM heartbeat_agent_preferences"
            )
        }
        # Preserve the meaning of selections made before per-agent preference
        # tracking was introduced.
        configured.update(agent_key for agent_key, _ in selected)
        agents = conn.execute(
            """
            SELECT s.id, s.name, s.confirm_tools, s.mcp_server_id,
                   ms.connection_status
            FROM subagents s
            JOIN mcp_servers ms ON ms.id = s.mcp_server_id
            WHERE s.enabled = 1
            ORDER BY s.name
            """
        ).fetchall()
        tool_rows = conn.execute(
            """
            SELECT s.id AS subagent_id, t.name, t.description, t.position
            FROM subagents s
            JOIN mcp_server_tools t ON t.mcp_server_id = s.mcp_server_id
            WHERE s.enabled = 1
            ORDER BY s.name, t.position, t.id
            """
        ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in tool_rows:
        grouped.setdefault(int(row["subagent_id"]), []).append(
            {
                "name": row["name"],
                "description": row["description"] or "",
                "selected": False,
            }
        )
    result = []
    for row in agents:
        try:
            protected = set(json.loads(row["confirm_tools"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            protected = {"*"}
        tools = grouped.get(int(row["id"]), [])
        agent_key = f"mcp:{int(row['id'])}"
        for tool in tools:
            tool["requires_confirmation"] = (
                "*" in protected or tool["name"] in protected
            )
            tool["selected"] = (
                (agent_key, tool["name"]) in selected
                if agent_key in configured
                else not tool["requires_confirmation"]
            )
        result.append(
            {
                "id": int(row["id"]),
                "key": agent_key,
                "kind": "mcp",
                "name": row["name"],
                "connection_status": row["connection_status"] or "untested",
                "tools": tools,
            }
        )
    active_builtin_keys = enabled_builtin_agent_keys()
    builtins = [
        agent for agent in builtin_agents.capabilities()
        if agent["builtin_key"] in active_builtin_keys
    ]
    for agent in builtins:
        for tool in agent["tools"]:
            tool["selected"] = (
                (agent["key"], tool["name"]) in selected
                if agent["key"] in configured
                else not tool["requires_confirmation"]
            )
    return [*builtins, *result]


def get_heartbeat_targets() -> list[dict]:
    """Return resolved built-in and MCP specs with selected safe tools only."""
    selected: dict[int, list[str]] = {}
    selected_builtins: dict[str, list[str]] = {}
    with _connect() as conn:
        for row in conn.execute(
            """
            SELECT ht.subagent_id, ht.tool_name
            FROM heartbeat_tools ht
            JOIN subagents s ON s.id = ht.subagent_id
            JOIN mcp_server_tools t
              ON t.mcp_server_id = s.mcp_server_id
             AND t.name = ht.tool_name
            WHERE s.enabled = 1
            ORDER BY ht.subagent_id, ht.tool_name
            """
        ):
            selected.setdefault(int(row["subagent_id"]), []).append(row["tool_name"])
        for row in conn.execute(
            """
            SELECT builtin_key, tool_name
            FROM heartbeat_builtin_tools
            ORDER BY builtin_key, tool_name
            """
        ):
            selected_builtins.setdefault(row["builtin_key"], []).append(
                row["tool_name"]
            )
    targets = []
    active_builtin_keys = enabled_builtin_agent_keys()
    for agent in builtin_agents.capabilities():
        if agent["builtin_key"] not in active_builtin_keys:
            continue
        chosen = selected_builtins.get(agent["key"], [])
        safe_names = {
            tool["name"]
            for tool in agent["tools"]
            if not tool["requires_confirmation"]
        }
        safe = [name for name in chosen if name in safe_names]
        if safe:
            targets.append(
                {
                    "id": agent["key"],
                    "kind": "builtin",
                    "builtin_key": agent["builtin_key"],
                    "name": agent["name"],
                    "allowed_tools": safe,
                }
            )
    seen_dynamic_agents: set[int] = set()
    for spec in build_specs():
        if int(spec["id"]) in seen_dynamic_agents:
            continue
        seen_dynamic_agents.add(int(spec["id"]))
        chosen = selected.get(int(spec["id"]), [])
        protected = set(spec.get("confirm_tools") or [])
        safe = [name for name in chosen if "*" not in protected and name not in protected]
        node_allowlist = spec.get("allowed_tools")
        if node_allowlist is not None:
            allowed = set(node_allowlist)
            safe = [name for name in safe if name in allowed]
        if safe:
            target = dict(spec)
            target["kind"] = "mcp"
            target["allowed_tools"] = safe
            targets.append(target)
    return targets


def get_heartbeat_agent_report(subagent_id: int | str) -> str:
    with _connect() as conn:
        if isinstance(subagent_id, str) and subagent_id.startswith("builtin:"):
            row = conn.execute(
                """
                SELECT last_report FROM heartbeat_builtin_agent_state
                WHERE builtin_key = ?
                """,
                (subagent_id,),
            ).fetchone()
            return (row["last_report"] or "") if row else ""
        row = conn.execute(
            "SELECT last_report FROM heartbeat_agent_state WHERE subagent_id = ?",
            (subagent_id,),
        ).fetchone()
    return (row["last_report"] or "") if row else ""


def set_heartbeat_agent_report(subagent_id: int | str, report: str) -> None:
    with _connect() as conn:
        if isinstance(subagent_id, str) and subagent_id.startswith("builtin:"):
            conn.execute(
                """
                INSERT INTO heartbeat_builtin_agent_state
                    (builtin_key, last_report, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(builtin_key) DO UPDATE SET
                    last_report = excluded.last_report,
                    updated_at = excluded.updated_at
                """,
                (subagent_id, str(report or "").strip()[:8000], _now()),
            )
            conn.commit()
            return
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


def list_heartbeat_notifications(limit: int = 25) -> list[dict]:
    """Return recent heartbeat alerts suitable for user notifications."""
    limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, trigger, started_at, finished_at, message
            FROM heartbeat_runs
            WHERE status = 'alert' AND TRIM(message) != ''
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
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
        current = conn.execute(
            "SELECT * FROM models WHERE id = ?", (model_id,)
        ).fetchone()
        if current is None:
            return None
        resulting_provider = fields.get("provider", current["provider"])
        assigned = conn.execute(
            """
            SELECT agent_key FROM builtin_agent_settings
            WHERE model_id = ?
            """,
            (model_id,),
        ).fetchall()
        incompatible = [
            builtin_agents.definition(row["agent_key"])
            for row in assigned
            if not builtin_agents.provider_matches(
                row["agent_key"], resulting_provider
            )
        ]
        incompatible = [item for item in incompatible if item is not None]
        if incompatible:
            names = ", ".join(item["name"] for item in incompatible)
            expected = incompatible[0]["provider"]
            raise ValueError(
                f"This model is assigned to {names} and must remain a {expected} model."
            )
        supervisor_assigned = conn.execute(
            "SELECT 1 FROM supervisor_settings WHERE model_id = ?",
            (model_id,),
        ).fetchone()
        if supervisor_assigned and not _supervisor_provider_supported(
            resulting_provider
        ):
            raise ValueError(
                "This model is assigned to Mounir and must remain a Mistral, "
                "Groq, or Ollama model."
            )
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(
                f"UPDATE models SET {sets} WHERE id = ?",
                (*fields.values(), model_id),
            )
            if "model" in fields:
                conn.execute(
                    """
                    UPDATE builtin_agent_settings
                    SET model = ?, updated_at = ?
                    WHERE model_id = ?
                    """,
                    (fields["model"], _now(), model_id),
                )
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
        conn.commit()
        return get_model(model_id)


def delete_model(model_id: int) -> bool:
    return delete_model_result(model_id).deleted


def delete_model_result(model_id: int) -> DeletionResult:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM models WHERE id = ?", (model_id,)
        ).fetchone() is None:
            conn.rollback()
            return DeletionResult("not_found")

        dependencies = []
        if conn.execute(
            "SELECT 1 FROM supervisor_settings WHERE model_id = ?", (model_id,)
        ).fetchone():
            dependencies.append("the supervisor")
        builtin_rows = conn.execute(
            "SELECT agent_key FROM builtin_agent_settings WHERE model_id = ? ORDER BY agent_key",
            (model_id,),
        ).fetchall()
        for row in builtin_rows:
            definition = builtin_agents.definition(row["agent_key"])
            dependencies.append(
                f"the {definition['name']} built-in agent"
                if definition
                else f"the {row['agent_key']} built-in agent"
            )
        agent_rows = conn.execute(
            "SELECT name FROM subagents WHERE model_id = ? ORDER BY name", (model_id,)
        ).fetchall()
        dependencies.extend(f"the {row['name']} subagent" for row in agent_rows)
        if dependencies:
            conn.rollback()
            return DeletionResult("in_use", tuple(dependencies))
        try:
            cur = conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
            conn.commit()
            return DeletionResult("deleted" if cur.rowcount else "not_found")
        except sqlite3.IntegrityError:
            conn.rollback()
            return DeletionResult("in_use", ("another saved configuration",))


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
        fields["headers"] = _merge_masked_json_object(
            fields["headers"], current["headers"], "headers"
        )
    if "env" in fields:
        fields["env"] = _merge_masked_json_object(
            fields["env"], current["env"], "environment"
        )
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
    return delete_server_result(server_id).deleted


def delete_server_result(server_id: int) -> DeletionResult:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone() is None:
            conn.rollback()
            return DeletionResult("not_found")
        agent_rows = conn.execute(
            "SELECT name FROM subagents WHERE mcp_server_id = ? ORDER BY name",
            (server_id,),
        ).fetchall()
        dependencies = tuple(f"the {row['name']} subagent" for row in agent_rows)
        if dependencies:
            conn.rollback()
            return DeletionResult("in_use", dependencies)
        try:
            cur = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            conn.commit()
            return DeletionResult("deleted" if cur.rowcount else "not_found")
        except sqlite3.IntegrityError:
            conn.rollback()
            return DeletionResult("in_use", ("another saved configuration",))


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

def _subagent_parent_id(value) -> int | None:
    """Normalize the API/CLI representation of Mounir as the root parent."""
    if value in (None, "", 0, "0", "supervisor"):
        return None
    try:
        parent_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select Mounir or an existing subagent as the parent.") from exc
    if parent_id <= 0:
        raise ValueError("Select Mounir or an existing subagent as the parent.")
    return parent_id


def _child_agent_ids(value) -> set[int]:
    """Normalize a multi-select child payload without accepting scalar strings."""
    if value is None:
        return set()
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("Child subagents must be provided as a list.")
    result: set[int] = set()
    for item in value:
        try:
            child_id = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("Every selected child must be an existing subagent.") from exc
        if child_id <= 0:
            raise ValueError("Every selected child must be an existing subagent.")
        result.add(child_id)
    return result


def _parent_agent_ids(value) -> set[int | None]:
    """Normalize a multi-parent payload; null represents Mounir."""
    if value is None:
        return {None}
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("Parent agents must be provided as a list.")
    result = {_subagent_parent_id(item) for item in value}
    if not result:
        raise ValueError("Select at least one parent connection.")
    return result


def _parent_node_ids(value) -> set[int | None]:
    """Normalize placement parents; null is the Mounir root."""
    if value is None:
        return {None}
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("Parent nodes must be provided as a list.")
    result: set[int | None] = set()
    for item in value:
        if item in (None, "", 0, "0", "supervisor"):
            result.add(None)
            continue
        try:
            node_id = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("Every parent must be an existing node.") from exc
        if node_id <= 0:
            raise ValueError("Every parent must be an existing node.")
        result.add(node_id)
    if not result:
        raise ValueError("Select at least one parent connection.")
    return result


def _node_depth(conn: sqlite3.Connection, node_id: int) -> int:
    depth = 1
    current_id = int(node_id)
    visited: set[int] = set()
    while True:
        if current_id in visited:
            raise ValueError("The saved node hierarchy contains a cycle.")
        visited.add(current_id)
        row = conn.execute(
            "SELECT parent_node_id FROM subagent_nodes WHERE id = ?", (current_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Select an existing parent node.")
        if row["parent_node_id"] is None:
            return depth
        current_id = int(row["parent_node_id"])
        depth += 1


def _create_subagent_node(
    conn: sqlite3.Connection,
    agent_id: int,
    parent_node_id: int | None,
) -> int:
    if conn.execute(
        "SELECT 1 FROM subagents WHERE id = ?", (agent_id,)
    ).fetchone() is None:
        raise ValueError("Select an existing subagent.")
    if parent_node_id is not None:
        parent = conn.execute(
            "SELECT agent_id FROM subagent_nodes WHERE id = ?", (parent_node_id,)
        ).fetchone()
        if parent is None:
            raise ValueError("Select an existing parent node.")
        if int(parent["agent_id"]) == int(agent_id):
            raise ValueError("A node cannot use itself as its direct child.")
        if _node_depth(conn, parent_node_id) >= MAX_SUBAGENT_DEPTH:
            raise ValueError(
                f"Subagents can be nested at most {MAX_SUBAGENT_DEPTH} levels below Mounir."
            )
    existing = conn.execute(
        """
        SELECT id FROM subagent_nodes
        WHERE agent_id = ? AND (
            (parent_node_id IS NULL AND ? IS NULL) OR parent_node_id = ?
        )
        """,
        (agent_id, parent_node_id, parent_node_id),
    ).fetchone()
    if existing:
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO subagent_nodes (agent_id, parent_node_id, created_at)
        VALUES (?, ?, ?)
        """,
        (agent_id, parent_node_id, _now()),
    )
    return int(cur.lastrowid)


def _canonical_parent_node(conn: sqlite3.Connection, agent_id: int) -> int:
    row = conn.execute(
        """
        SELECT id FROM subagent_nodes WHERE agent_id = ?
        ORDER BY parent_node_id IS NOT NULL, created_at, id LIMIT 1
        """,
        (agent_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Select an existing parent subagent.")
    return int(row["id"])


def _sync_legacy_connections(conn: sqlite3.Connection) -> None:
    """Maintain the old definition-level projection for CLI compatibility."""
    conn.execute("DELETE FROM subagent_connections")
    conn.execute(
        """
        INSERT OR IGNORE INTO subagent_connections
            (parent_agent_id, child_agent_id, created_at)
        SELECT parent.agent_id, child.agent_id, child.created_at
        FROM subagent_nodes child
        LEFT JOIN subagent_nodes parent ON parent.id = child.parent_node_id
        """
    )
    conn.execute("UPDATE subagents SET parent_agent_id = NULL")
    conn.execute(
        """
        UPDATE subagents AS child
        SET parent_agent_id = (
            SELECT parent.agent_id
            FROM subagent_nodes child_node
            JOIN subagent_nodes parent ON parent.id = child_node.parent_node_id
            WHERE child_node.agent_id = child.id
            ORDER BY child_node.created_at, child_node.id
            LIMIT 1
        )
        """
    )


def _add_subagent(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    system_prompt: str,
    model_id: int,
    mcp_server_id: int,
    confirm_tool_calls: bool = True,
    parent_agent_id: int | None = None,
    confirm_tools=None,
    icon_data: bytes = b"",
    icon_mime: str = "",
    dedupe_tools=None,
    enabled: bool = True,
    parent_agent_ids=None,
    parent_node_ids=None,
) -> int:
    try:
        selected_model_id = int(model_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select a model before creating the subagent.") from exc
    try:
        selected_server_id = int(mcp_server_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select an MCP server before creating the subagent.") from exc
    if selected_model_id <= 0 or conn.execute(
        "SELECT 1 FROM models WHERE id = ?", (selected_model_id,)
    ).fetchone() is None:
        raise ValueError("Select an existing model before creating the subagent.")
    if selected_server_id <= 0 or conn.execute(
        "SELECT 1 FROM mcp_servers WHERE id = ?", (selected_server_id,)
    ).fetchone() is None:
        raise ValueError("Select an existing MCP server before creating the subagent.")
    if confirm_tools is None:
        confirm_tools = ["*"] if _bool(confirm_tool_calls, "confirm_tool_calls") else []
    confirm_tools_json = _json_string_list(confirm_tools, "confirmation tools")
    dedupe_tools_json = _json_string_list(dedupe_tools or [], "duplicate protection tools")
    has_confirmations = bool(json.loads(confirm_tools_json))
    selected_parent_agents = (
        _parent_agent_ids(parent_agent_ids)
        if parent_agent_ids is not None
        else {_subagent_parent_id(parent_agent_id)}
    )
    selected_parent_nodes = (
        _parent_node_ids(parent_node_ids)
        if parent_node_ids is not None
        else {
            None if parent_id is None else _canonical_parent_node(conn, parent_id)
            for parent_id in selected_parent_agents
        }
    )
    existing_agent_ids = {
        int(row["id"]) for row in conn.execute("SELECT id FROM subagents")
    }
    if any(
        parent_id is not None and parent_id not in existing_agent_ids
        for parent_id in selected_parent_agents
    ):
        raise ValueError("Select an existing subagent as the parent.")
    selected_parent_id = (
        None if None in selected_parent_agents else min(selected_parent_agents)
    )
    try:
        cur = conn.execute(
            """
            INSERT INTO subagents
                (name, description, system_prompt, icon_data, icon_mime,
                 model_id, mcp_server_id, confirm_tool_calls, confirm_tools,
                 dedupe_tools, enabled, parent_agent_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _required(name, "name"),
                _required(description, "description"),
                (system_prompt or "").strip(),
                bytes(icon_data or b""),
                (icon_mime or "").strip(),
                selected_model_id,
                selected_server_id,
                int(has_confirmations),
                confirm_tools_json,
                dedupe_tools_json,
                int(_bool(enabled, "enabled")),
                selected_parent_id,
                _now(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _friendly_integrity_error(exc) from exc
    agent_id = int(cur.lastrowid)
    for parent_node_id in selected_parent_nodes:
        _create_subagent_node(conn, agent_id, parent_node_id)
    _sync_legacy_connections(conn)
    conn.commit()
    return agent_id


def add_subagent(
    name: str,
    description: str,
    system_prompt: str,
    model_id: int,
    mcp_server_id: int,
    confirm_tool_calls: bool = True,
    parent_agent_id: int | None = None,
    confirm_tools=None,
    icon_data: bytes = b"",
    icon_mime: str = "",
    dedupe_tools=None,
    enabled: bool = True,
    parent_agent_ids=None,
    parent_node_ids=None,
) -> dict:
    with _connect() as conn:
        aid = _add_subagent(
            conn, name, description, system_prompt, model_id, mcp_server_id,
            confirm_tool_calls, parent_agent_id, confirm_tools, icon_data, icon_mime,
            dedupe_tools, enabled, parent_agent_ids, parent_node_ids,
        )
        return get_subagent(aid)


_SUBAGENT_SELECT = """
    SELECT s.id, s.name, s.description, s.system_prompt,
           s.model_id, s.mcp_server_id, s.confirm_tool_calls, s.confirm_tools,
           s.dedupe_tools, s.enabled,
           s.created_at,
           CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon,
           m.name AS model_name, m.model, m.provider, m.base_url, m.api_key,
           srv.name AS server_name, srv.transport, srv.connection,
           srv.headers, srv.env
    FROM subagents s
    JOIN models m ON s.model_id = m.id
    JOIN mcp_servers srv ON s.mcp_server_id = srv.id
"""


def _subagent_node_path(
    node: dict, by_node_id: dict[int, dict], names: dict[int, str]
) -> list[str]:
    path = [names.get(int(node["agent_id"]), "Unknown")]
    parent_node_id = node["parent_node_id"]
    visited = {int(node["id"])}
    while parent_node_id is not None:
        parent = by_node_id.get(int(parent_node_id))
        if parent is None or int(parent["id"]) in visited:
            break
        visited.add(int(parent["id"]))
        path.append(names.get(int(parent["agent_id"]), "Unknown"))
        parent_node_id = parent["parent_node_id"]
    return ["Mounir", *reversed(path)]


def _enrich_subagent_connections(
    conn: sqlite3.Connection, rows: list[dict]
) -> list[dict]:
    if not rows:
        return rows
    names = {
        int(row["id"]): row["name"]
        for row in conn.execute("SELECT id, name FROM subagents")
    }
    nodes = [
        dict(node)
        for node in conn.execute(
            """
            SELECT id, agent_id, parent_node_id, enabled_tools, created_at
            FROM subagent_nodes
            """
        )
    ]
    by_node_id = {int(node["id"]): node for node in nodes}
    children: dict[int, list[dict]] = {}
    for node in nodes:
        if node["parent_node_id"] is not None:
            children.setdefault(int(node["parent_node_id"]), []).append(node)

    for row in rows:
        agent_id = int(row["id"])
        agent_nodes = sorted(
            (node for node in nodes if int(node["agent_id"]) == agent_id),
            key=lambda node: (node["created_at"], int(node["id"])),
        )
        placements = []
        for node in agent_nodes:
            parent_node = (
                by_node_id.get(int(node["parent_node_id"]))
                if node["parent_node_id"] is not None
                else None
            )
            direct_children = sorted(
                children.get(int(node["id"]), []),
                key=lambda child: names.get(int(child["agent_id"]), ""),
            )
            path_names = _subagent_node_path(node, by_node_id, names)
            placements.append(
                {
                    "id": int(node["id"]),
                    "agent_id": agent_id,
                    "parent_node_id": (
                        int(node["parent_node_id"])
                        if node["parent_node_id"] is not None
                        else None
                    ),
                    "parent_agent_id": (
                        int(parent_node["agent_id"]) if parent_node else None
                    ),
                    "parent_name": (
                        names.get(int(parent_node["agent_id"]), "Mounir")
                        if parent_node
                        else "Mounir"
                    ),
                    "depth": len(path_names) - 1,
                    "path_names": path_names,
                    "path_label": " / ".join(path_names),
                    "enabled_tools": (
                        json.loads(
                            _json_string_list(
                                node["enabled_tools"], "enabled node tools"
                            )
                        )
                        if node["enabled_tools"] is not None
                        else None
                    ),
                    "child_node_ids": [int(child["id"]) for child in direct_children],
                    "child_agent_ids": [
                        int(child["agent_id"]) for child in direct_children
                    ],
                }
            )
        parent_ids = sorted(
            {
                int(placement["parent_agent_id"])
                for placement in placements
                if placement["parent_agent_id"] is not None
            }
        )
        connected_to_supervisor = any(
            placement["parent_node_id"] is None for placement in placements
        )
        parent_names = (["Mounir"] if connected_to_supervisor else []) + [
            names[parent_id] for parent_id in parent_ids if parent_id in names
        ]
        row["placements"] = placements
        row["parent_node_ids"] = [
            placement["parent_node_id"]
            for placement in placements
            if placement["parent_node_id"] is not None
        ]
        row["parent_agent_ids"] = parent_ids
        row["connected_to_supervisor"] = connected_to_supervisor
        row["parent_names"] = parent_names
        # Compatibility for older clients: expose one representative parent,
        # but never use it to calculate or write relationships.
        row["parent_agent_id"] = parent_ids[0] if parent_ids else None
        row["parent_name"] = (
            names.get(parent_ids[0], "Mounir") if parent_ids else "Mounir"
        )
        row["child_agent_ids"] = sorted(
            {
                child_id
                for placement in placements
                for child_id in placement["child_agent_ids"]
            }
        )
        row["child_count"] = sum(
            len(placement["child_node_ids"]) for placement in placements
        )
    return rows


def get_subagent(subagent_id: int) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            f"{_SUBAGENT_SELECT} WHERE s.id = ?",
            (subagent_id,),
        )
        row = cur.fetchone()
        result = _enrich_subagent_connections(conn, [dict(row)] if row else [])
        return result[0] if result else None


def get_subagent_by_name(name: str) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            f"{_SUBAGENT_SELECT} WHERE s.name = ?",
            (name.strip(),),
        )
        row = cur.fetchone()
        result = _enrich_subagent_connections(conn, [dict(row)] if row else [])
        return result[0] if result else None


def get_subagent_node(node_id: int) -> dict | None:
    """Return one placement and its direct relations, separate from its subagent."""
    with _connect() as conn:
        node = conn.execute(
            """
            SELECT n.id, n.agent_id, n.parent_node_id, n.enabled_tools, n.created_at,
                   s.name, s.description, s.model_id, s.mcp_server_id, s.enabled,
                   CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon,
                   m.name AS model_name, m.model,
                   srv.name AS mcp_server_name
            FROM subagent_nodes n
            JOIN subagents s ON s.id = n.agent_id
            JOIN models m ON m.id = s.model_id
            JOIN mcp_servers srv ON srv.id = s.mcp_server_id
            WHERE n.id = ?
            """,
            (node_id,),
        ).fetchone()
        if node is None:
            return None

        nodes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT n.id, n.agent_id, n.parent_node_id, n.created_at,
                       s.name, s.enabled,
                       CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon
                FROM subagent_nodes n
                JOIN subagents s ON s.id = n.agent_id
                """
            )
        ]
        by_id = {int(item["id"]): item for item in nodes}
        names = {int(item["agent_id"]): item["name"] for item in nodes}

        node_data = dict(node)
        placement = by_id[int(node_id)]
        path_names = _subagent_node_path(placement, by_id, names)
        parent = (
            by_id.get(int(node_data["parent_node_id"]))
            if node_data["parent_node_id"] is not None
            else None
        )
        children = sorted(
            (
                item
                for item in nodes
                if item["parent_node_id"] is not None
                and int(item["parent_node_id"]) == int(node_id)
            ),
            key=lambda item: (item["name"].casefold(), int(item["id"])),
        )
        return {
            "id": int(node_data["id"]),
            "subagent_id": int(node_data["agent_id"]),
            "parent_node_id": (
                int(node_data["parent_node_id"])
                if node_data["parent_node_id"] is not None
                else None
            ),
            "created_at": node_data["created_at"],
            "enabled_tools": (
                json.loads(
                    _json_string_list(
                        node_data["enabled_tools"], "enabled node tools"
                    )
                )
                if node_data["enabled_tools"] is not None
                else None
            ),
            "depth": len(path_names) - 1,
            "path_names": path_names,
            "path_label": " / ".join(path_names),
            "parent": (
                {
                    "id": int(parent["id"]),
                    "subagent_id": int(parent["agent_id"]),
                    "name": parent["name"],
                    "path_label": " / ".join(
                        _subagent_node_path(parent, by_id, names)
                    ),
                }
                if parent
                else None
            ),
            "subagent": {
                "id": int(node_data["agent_id"]),
                "name": node_data["name"],
                "description": node_data["description"],
                "model_id": int(node_data["model_id"]),
                "model_name": node_data["model_name"],
                "model": node_data["model"],
                "mcp_server_id": int(node_data["mcp_server_id"]),
                "mcp_server_name": node_data["mcp_server_name"],
                "enabled": bool(node_data["enabled"]),
                "has_icon": bool(node_data["has_icon"]),
            },
            "children": [
                {
                    "id": int(child["id"]),
                    "subagent_id": int(child["agent_id"]),
                    "name": child["name"],
                    "enabled": bool(child["enabled"]),
                    "has_icon": bool(child["has_icon"]),
                }
                for child in children
            ],
        }


def update_subagent_node(node_id: int, *, enabled_tools) -> dict | None:
    """Save an exact MCP tool allowlist for one placement; NULL inherits all."""
    if enabled_tools is None:
        normalized = None
    else:
        normalized = _json_string_list(enabled_tools, "enabled tools")
        parsed = json.loads(normalized)
        if "*" in parsed:
            raise ValueError("enabled tools must use explicit tool names")
        if len(parsed) > 1000 or any(len(name) > 512 for name in parsed):
            raise ValueError("enabled tools contain too many or overly long names")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM subagent_nodes WHERE id = ?", (node_id,)
        ).fetchone() is None:
            conn.rollback()
            return None
        conn.execute(
            "UPDATE subagent_nodes SET enabled_tools = ? WHERE id = ?",
            (normalized, node_id),
        )
        conn.commit()
    return get_subagent_node(node_id)


def remove_subagent_node(node_id: int) -> dict | None:
    """Disconnect one non-root placement and its descendants, preserving definitions."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        node = conn.execute(
            "SELECT id, agent_id, parent_node_id FROM subagent_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if node is None:
            conn.rollback()
            return None
        if node["parent_node_id"] is None:
            conn.rollback()
            raise ValueError("Only a child node can be disconnected from this view.")

        descendants: list[int] = []
        pending = [int(node_id)]
        visited: set[int] = set()
        while pending:
            current_id = pending.pop()
            if current_id in visited:
                raise ValueError("The saved node hierarchy contains a cycle.")
            visited.add(current_id)
            descendants.append(current_id)
            pending.extend(
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM subagent_nodes WHERE parent_node_id = ?",
                    (current_id,),
                )
            )

        for descendant_id in reversed(descendants):
            conn.execute("DELETE FROM subagent_nodes WHERE id = ?", (descendant_id,))
        _sync_legacy_connections(conn)
        conn.commit()
        return {
            "ok": True,
            "subagent_id": int(node["agent_id"]),
            "parent_node_id": int(node["parent_node_id"]),
            "removed_nodes": len(descendants),
        }


def list_subagents() -> list[dict]:
    with _connect() as conn:
        cur = conn.execute(f"{_SUBAGENT_SELECT} ORDER BY s.name")
        return _enrich_subagent_connections(conn, [dict(r) for r in cur.fetchall()])


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


def _set_node_children(
    conn: sqlite3.Connection, parent_node_id: int, child_agent_ids: set[int]
) -> None:
    parent = conn.execute(
        "SELECT agent_id FROM subagent_nodes WHERE id = ?", (parent_node_id,)
    ).fetchone()
    if parent is None:
        raise ValueError("Select an existing parent node.")
    parent_agent_id = int(parent["agent_id"])
    if parent_agent_id in child_agent_ids:
        raise ValueError("A node cannot use itself as its direct child.")
    known_agents = {
        int(row["id"]) for row in conn.execute("SELECT id FROM subagents")
    }
    if child_agent_ids - known_agents:
        raise ValueError("Every selected child must be an existing subagent.")
    current = {
        int(row["agent_id"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, agent_id FROM subagent_nodes WHERE parent_node_id = ?",
            (parent_node_id,),
        )
    }
    for child_agent_id in current.keys() - child_agent_ids:
        child_node_id = current[child_agent_id]
        if conn.execute(
            "SELECT 1 FROM subagent_nodes WHERE parent_node_id = ?", (child_node_id,)
        ).fetchone():
            raise ValueError(
                "Remove this node's own children before disconnecting it."
            )
        conn.execute("DELETE FROM subagent_nodes WHERE id = ?", (child_node_id,))
        if conn.execute(
            "SELECT 1 FROM subagent_nodes WHERE agent_id = ?", (child_agent_id,)
        ).fetchone() is None:
            _create_subagent_node(conn, child_agent_id, None)
    for child_agent_id in child_agent_ids - current.keys():
        _create_subagent_node(conn, child_agent_id, parent_node_id)


def update_subagent(subagent_id: int, **kwargs) -> dict | None:
    parent_node_selection_supplied = "parent_node_ids" in kwargs
    selected_parent_node_ids = (
        _parent_node_ids(kwargs.pop("parent_node_ids"))
        if parent_node_selection_supplied
        else set()
    )
    parent_selection_supplied = "parent_agent_ids" in kwargs
    selected_parent_ids = (
        _parent_agent_ids(kwargs.pop("parent_agent_ids"))
        if parent_selection_supplied
        else set()
    )
    legacy_parent_supplied = "parent_agent_id" in kwargs
    legacy_parent_id = (
        _subagent_parent_id(kwargs.pop("parent_agent_id"))
        if legacy_parent_supplied
        else None
    )
    if legacy_parent_supplied and not parent_selection_supplied:
        selected_parent_ids = {legacy_parent_id}
        parent_selection_supplied = True
    placement_children = kwargs.pop("placement_children", None)
    placement_children_supplied = placement_children is not None
    if placement_children_supplied and not isinstance(placement_children, list):
        raise ValueError("Placement children must be provided as a list.")
    child_selection_supplied = "child_agent_ids" in kwargs
    selected_child_ids = (
        _child_agent_ids(kwargs.pop("child_agent_ids"))
        if child_selection_supplied
        else set()
    )
    allowed = {
        "name", "description", "system_prompt", "model_id",
        "mcp_server_id", "confirm_tool_calls", "confirm_tools",
        "icon_data", "icon_mime", "dedupe_tools", "enabled",
    }
    fields = {
        k: v
        for k, v in kwargs.items()
        if k in allowed and v is not None
    }
    if (
        not fields
        and not parent_node_selection_supplied
        and not parent_selection_supplied
        and not child_selection_supplied
        and not placement_children_supplied
    ):
        return get_subagent(subagent_id)
    if "name" in fields:
        fields["name"] = _required(fields["name"], "name")
    if "description" in fields:
        fields["description"] = _required(fields["description"], "description")
    if "model_id" in fields:
        try:
            fields["model_id"] = int(fields["model_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Select a model for the subagent.") from exc
    if "mcp_server_id" in fields:
        try:
            fields["mcp_server_id"] = int(fields["mcp_server_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Select an MCP server for the subagent.") from exc
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
    if "enabled" in fields:
        fields["enabled"] = int(_bool(fields["enabled"], "enabled"))
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent_ids = {
            int(row["id"]) for row in conn.execute("SELECT id FROM subagents")
        }
        if subagent_id not in agent_ids:
            return None
        if parent_selection_supplied and not parent_node_selection_supplied:
            if any(
                parent_id is not None and parent_id not in agent_ids
                for parent_id in selected_parent_ids
            ):
                raise ValueError("Select an existing parent subagent.")
            selected_parent_node_ids = {
                None if parent_id is None else _canonical_parent_node(conn, parent_id)
                for parent_id in selected_parent_ids
            }
            parent_node_selection_supplied = True
        if parent_node_selection_supplied:
            existing_nodes = {
                (
                    int(row["parent_node_id"])
                    if row["parent_node_id"] is not None
                    else None
                ): int(row["id"])
                for row in conn.execute(
                    "SELECT id, parent_node_id FROM subagent_nodes WHERE agent_id = ?",
                    (subagent_id,),
                )
            }
            for parent_node_id in existing_nodes.keys() - selected_parent_node_ids:
                node_id = existing_nodes[parent_node_id]
                if conn.execute(
                    "SELECT 1 FROM subagent_nodes WHERE parent_node_id = ?", (node_id,)
                ).fetchone():
                    raise ValueError(
                        "Remove this placement's children before disconnecting it."
                    )
                conn.execute("DELETE FROM subagent_nodes WHERE id = ?", (node_id,))
            for parent_node_id in selected_parent_node_ids - existing_nodes.keys():
                _create_subagent_node(conn, subagent_id, parent_node_id)
        if "model_id" in fields and conn.execute(
            "SELECT 1 FROM models WHERE id = ?", (fields["model_id"],)
        ).fetchone() is None:
            raise ValueError("Select an existing model for the subagent.")
        if "mcp_server_id" in fields and conn.execute(
            "SELECT 1 FROM mcp_servers WHERE id = ?",
            (fields["mcp_server_id"],),
        ).fetchone() is None:
            raise ValueError("Select an existing MCP server for the subagent.")
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            if fields:
                conn.execute(
                    f"UPDATE subagents SET {sets} WHERE id = ?",
                    (*fields.values(), subagent_id),
                )
            if child_selection_supplied:
                primary_node_id = _canonical_parent_node(conn, subagent_id)
                _set_node_children(conn, primary_node_id, selected_child_ids)
            if placement_children_supplied:
                for selection in placement_children:
                    if not isinstance(selection, dict):
                        raise ValueError("Every placement selection must be an object.")
                    try:
                        node_id = int(selection.get("node_id"))
                    except (TypeError, ValueError) as exc:
                        raise ValueError("Select an existing agent placement.") from exc
                    owner = conn.execute(
                        "SELECT agent_id FROM subagent_nodes WHERE id = ?", (node_id,)
                    ).fetchone()
                    if owner is None or int(owner["agent_id"]) != subagent_id:
                        raise ValueError("Select a placement belonging to this subagent.")
                    _set_node_children(
                        conn,
                        node_id,
                        _child_agent_ids(selection.get("child_agent_ids")),
                    )
            if (
                parent_node_selection_supplied
                or child_selection_supplied
                or placement_children_supplied
            ):
                _sync_legacy_connections(conn)
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
        conn.commit()
        return get_subagent(subagent_id)


def connect_subagent(
    child_id: int,
    parent_agent_id=None,
    *,
    parent_node_id=None,
) -> dict | None:
    """Create one independent placement under a specific parent node."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent_ids = {
            int(row["id"]) for row in conn.execute("SELECT id FROM subagents")
        }
        if child_id not in agent_ids:
            return None
        if parent_node_id is None and parent_agent_id is not None:
            selected_parent_node_id = _canonical_parent_node(
                conn, _subagent_parent_id(parent_agent_id)
            )
        elif parent_node_id in (None, "", 0, "0", "supervisor"):
            selected_parent_node_id = None
        else:
            try:
                selected_parent_node_id = int(parent_node_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("Select an existing parent node.") from exc
        _create_subagent_node(conn, int(child_id), selected_parent_node_id)
        _sync_legacy_connections(conn)
        conn.commit()
    return get_subagent(child_id)


def delete_subagent(subagent_id: int) -> bool:
    with _connect() as conn:
        children = [
            row["name"]
            for row in conn.execute(
                """
                SELECT DISTINCT child.name
                FROM subagent_nodes parent
                JOIN subagent_nodes child_node ON child_node.parent_node_id = parent.id
                JOIN subagents child ON child.id = child_node.agent_id
                WHERE parent.agent_id = ?
                ORDER BY child.name
                """,
                (subagent_id,),
            )
        ]
        if children:
            names = ", ".join(children)
            raise ValueError(
                f"Move or delete this subagent's children first: {names}."
            )
        cur = conn.execute("DELETE FROM subagents WHERE id = ?", (subagent_id,))
        if cur.rowcount:
            _sync_legacy_connections(conn)
            conn.execute(
                "DELETE FROM heartbeat_agent_preferences WHERE agent_key = ?",
                (f"mcp:{int(subagent_id)}",),
            )
        conn.commit()
        return cur.rowcount > 0


def is_subagent_enabled(subagent_id: int) -> bool:
    try:
        requested_id = int(subagent_id)
    except (TypeError, ValueError):
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT enabled FROM subagents WHERE id = ?", (requested_id,)
        ).fetchone()
    return bool(row and row["enabled"])


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
        if not s.get("enabled"):
            continue
        for placement in s.get("placements") or []:
            specs.append({
                "id": s["id"],
                "node_id": placement["id"],
                "parent_node_id": placement["parent_node_id"],
                "mcp_server_id": s["mcp_server_id"],
                "name": s["name"],
                "description": s.get("description") or f"Uses the {s['server_name']} MCP server.",
                "prompt": s["system_prompt"],

                "transport": s.get("transport") or "stdio",
                "connection": s["connection"],

                "headers": _resolved_json_object(s.get("headers") or "{}"),
                "env": _resolved_json_object(s.get("env") or "{}"),

                "parent_agent_id": placement.get("parent_agent_id"),
                "parent_name": placement.get("parent_name") or "Mounir",
                "parent_agent_ids": (
                    [placement["parent_agent_id"]]
                    if placement.get("parent_agent_id") is not None
                    else []
                ),
                "parent_names": [placement.get("parent_name") or "Mounir"],
                "connected_to_supervisor": placement["parent_node_id"] is None,
                "child_agent_ids": list(placement.get("child_agent_ids") or []),
                "allowed_tools": placement.get("enabled_tools"),

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
            })
    return specs
