"""SQLite persistence for Mounir's local configuration and dynamic agents.

Core tables include:

- ``models``       canonical registry for every configured model
- ``*_model_details`` one-to-one, type-specific model configuration
- ``mcp_servers``  reusable MCP server connections (transport + command/URL)
- ``subagents``    reusable specialist configurations (prompt, model, MCP, tools)
- ``subagent_connections`` legacy compatibility projection
- ``subagent_nodes`` workflow placements referencing reusable subagents
- ``mcp_server_tools`` cached MCP capability metadata
- ``skills``       portable Agent Skills packages imported by the user
- ``skill_assignments`` reusable skill access for every kind of agent
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
import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import builtin_agents, config as cfg, knowledge_protocol

DB_PATH: Path = cfg.DATA_DIR / "mounir.db"
LEGACY_REGISTRY: Path = cfg.DATA_DIR / "mcp_agents.json"
MCP_TRANSPORTS = {"stdio", "streamable_http", "sse"}
MCP_CREDENTIAL_FILE_LIMIT = 2 * 1024 * 1024
MCP_CREDENTIAL_FILE_COUNT = 10
MAX_SUBAGENT_DEPTH = 4
_UNSET = object()


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
    try:
        DB_PATH.parent.chmod(0o700)
    except OSError:
        pass
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
        for path in (DB_PATH, Path(f"{DB_PATH}-wal"), Path(f"{DB_PATH}-shm")):
            try:
                path.chmod(0o600)
            except OSError:
                pass


def _enable_reusable_subagent_placements(conn: sqlite3.Connection) -> None:
    """Allow one saved subagent to appear in multiple workflow branches.

    A short-lived architecture made ``subagent_nodes.agent_id`` globally
    unique. Removing that index is enough to restore reusable placements; the
    parent-scoped indexes still prevent the same delegation tool from appearing
    twice under one parent.
    """
    conn.execute("DROP INDEX IF EXISTS idx_subagent_nodes_one_per_agent")
    conn.execute(
        """
        UPDATE subagent_nodes
        SET enabled_tools = (
            SELECT enabled_tools FROM subagents
            WHERE subagents.id = subagent_nodes.agent_id
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO app_meta (key, value) VALUES (?, ?)
        """,
        ("reusable_subagent_placements_v1", _now()),
    )


def _allow_prompt_only_subagents(conn: sqlite3.Connection) -> None:
    """Rebuild the legacy subagent table so its MCP server can be NULL."""
    columns = {
        row["name"]: row for row in conn.execute("PRAGMA table_info(subagents)")
    }
    server_column = columns.get("mcp_server_id")
    if server_column is None or not int(server_column["notnull"]):
        return

    # SQLite cannot drop a NOT NULL constraint in place. Foreign keys are
    # disabled only for this committed table swap, then checked immediately.
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS subagents_nullable_upgrade;
            CREATE TABLE subagents_nullable_upgrade (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                icon_data BLOB NOT NULL DEFAULT X'',
                icon_mime TEXT NOT NULL DEFAULT '',
                model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
                mcp_server_id INTEGER REFERENCES mcp_servers(id) ON DELETE RESTRICT,
                confirm_tool_calls INTEGER NOT NULL DEFAULT 1,
                confirm_tools TEXT NOT NULL DEFAULT '["*"]',
                dedupe_tools TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                enabled_tools TEXT,
                parent_agent_id INTEGER
                    REFERENCES subagents(id) ON DELETE RESTRICT,
                created_at TEXT
            );
            INSERT INTO subagents_nullable_upgrade (
                id, name, description, system_prompt, icon_data, icon_mime,
                model_id, mcp_server_id, confirm_tool_calls, confirm_tools,
                dedupe_tools, enabled, enabled_tools, parent_agent_id, created_at
            )
            SELECT id, name, description, system_prompt, icon_data, icon_mime,
                   model_id, mcp_server_id, confirm_tool_calls, confirm_tools,
                   dedupe_tools, enabled, enabled_tools, parent_agent_id, created_at
            FROM subagents;
            DROP TABLE subagents;
            ALTER TABLE subagents_nullable_upgrade RENAME TO subagents;
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violation = conn.execute("PRAGMA foreign_key_check").fetchone()
    if violation is not None:
        raise sqlite3.IntegrityError(
            f"Foreign key check failed after subagent migration: {tuple(violation)}"
        )


def _init_schema(conn: sqlite3.Connection) -> None:
    # WAL lets readers continue while a short configuration write is committed.
    # This matters when chat, heartbeats, and Agent Studio are active together.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            model_type TEXT NOT NULL DEFAULT 'text'
                CHECK (model_type IN ('text', 'embedding', 'speech', 'transcription')),
            location TEXT NOT NULL DEFAULT 'cloud'
                CHECK (location IN ('cloud', 'local')),
            model TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            base_url TEXT NOT NULL DEFAULT '',
            api_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS text_model_details (
            model_id INTEGER PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,
            provider_options TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS embedding_model_details (
            model_id INTEGER PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,
            adapter TEXT NOT NULL DEFAULT 'openai_compatible'
                CHECK (adapter IN ('openai_compatible', 'ollama')),
            dimensions INTEGER,
            connection_status TEXT NOT NULL DEFAULT 'untested'
                CHECK (connection_status IN ('untested', 'connected', 'stale', 'failed')),
            last_tested_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            provider_options TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS speech_model_details (
            model_id INTEGER PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,
            voice TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'auto',
            provider_options TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS transcription_model_details (
            model_id INTEGER PRIMARY KEY REFERENCES models(id) ON DELETE CASCADE,
            language TEXT NOT NULL DEFAULT 'auto',
            provider_options TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS mcp_servers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,

            -- User-facing information shown by Agent Studio. This belongs to
            -- the saved server instead of being inferred from its package.
            description TEXT NOT NULL DEFAULT '',

            -- Retained only for upgrading old databases. New setup is described
            -- entirely by the generic fields below.
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

            -- none | bearer | header | custom | oauth
            auth_scheme TEXT NOT NULL DEFAULT '',

            -- Optional user-defined local initialization or authorization command.
            setup_command TEXT NOT NULL DEFAULT '',

            -- Origin metadata keeps installed, Registry, and manually configured
            -- servers in one collection without changing their runtime behavior.
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_name TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            source_version TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',

            oauth_redirect_uri TEXT NOT NULL DEFAULT '',
            oauth_tokens TEXT NOT NULL DEFAULT '',
            oauth_token_expires_at REAL NOT NULL DEFAULT 0,
            oauth_client_info TEXT NOT NULL DEFAULT '',

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

        CREATE TABLE IF NOT EXISTS mcp_server_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mcp_server_id INTEGER NOT NULL
                REFERENCES mcp_servers(id) ON DELETE CASCADE,
            env_var TEXT NOT NULL,
            filename TEXT NOT NULL,
            content BLOB NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (mcp_server_id, env_var)
        );

        CREATE TABLE IF NOT EXISTS subagents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            icon_data BLOB NOT NULL DEFAULT X'',
            icon_mime TEXT NOT NULL DEFAULT '',
            model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
            mcp_server_id INTEGER REFERENCES mcp_servers(id) ON DELETE RESTRICT,
            confirm_tool_calls INTEGER NOT NULL DEFAULT 1,
            confirm_tools TEXT NOT NULL DEFAULT '["*"]',
            dedupe_tools TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            -- NULL inherits every tool; a JSON list is this agent's allowlist.
            enabled_tools TEXT,
            parent_agent_id INTEGER
                REFERENCES subagents(id) ON DELETE RESTRICT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS subagent_mcp_sources (
            subagent_id INTEGER NOT NULL
                REFERENCES subagents(id) ON DELETE CASCADE,
            mcp_server_id INTEGER NOT NULL
                REFERENCES mcp_servers(id) ON DELETE RESTRICT,
            -- NULL follows all tools currently advertised by this server.
            enabled_tools TEXT,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (subagent_id, mcp_server_id)
        );

        CREATE INDEX IF NOT EXISTS idx_subagent_mcp_sources_server
            ON subagent_mcp_sources (mcp_server_id, subagent_id);

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

        CREATE TABLE IF NOT EXISTS workflows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            system_prompt TEXT NOT NULL DEFAULT '',
            model_id INTEGER REFERENCES models(id) ON DELETE SET NULL,
            execution_mode TEXT NOT NULL DEFAULT 'agentic'
                CHECK (execution_mode IN ('agentic', 'direct')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- A NULL workflow_id keeps the existing global Mounir overview intact.
        -- A value places the reusable subagent inside a custom workflow design.
        -- Runtime resolution remains scope-aware: NULL is the global Mounir
        -- overview and a concrete value is one saved workflow definition.
        CREATE TABLE IF NOT EXISTS workflow_graph_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_workflow_id INTEGER
                REFERENCES workflows(id) ON DELETE CASCADE,
            child_workflow_id INTEGER NOT NULL
                REFERENCES workflows(id) ON DELETE RESTRICT,
            parent_node_id INTEGER
                REFERENCES subagent_nodes(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            CHECK (
                owner_workflow_id IS NULL
                OR owner_workflow_id != child_workflow_id
            )
        );

        CREATE INDEX IF NOT EXISTS idx_workflow_graph_owner_position
            ON workflow_graph_nodes (owner_workflow_id, position, id);

        CREATE INDEX IF NOT EXISTS idx_workflow_graph_parent
            ON workflow_graph_nodes (parent_node_id);

        CREATE INDEX IF NOT EXISTS idx_subagent_nodes_parent
            ON subagent_nodes (parent_node_id);

        CREATE TABLE IF NOT EXISTS skills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            skill_md TEXT NOT NULL,
            package_blob BLOB NOT NULL,
            files TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            source_type TEXT NOT NULL DEFAULT 'import',
            source_name TEXT NOT NULL DEFAULT '',
            source_ref TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_source
            ON skills (source_type, source_ref)
            WHERE source_ref != '';

        CREATE TABLE IF NOT EXISTS skill_assignments (
            skill_id INTEGER NOT NULL
                REFERENCES skills(id) ON DELETE CASCADE,
            agent_type TEXT NOT NULL
                CHECK (agent_type IN ('supervisor', 'builtin', 'subagent')),
            agent_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            created_at TEXT NOT NULL,
            PRIMARY KEY (skill_id, agent_type, agent_key)
        );

        CREATE INDEX IF NOT EXISTS idx_skill_assignments_agent
            ON skill_assignments (agent_type, agent_key, enabled);

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
            tts_voice TEXT NOT NULL DEFAULT '',
            tts_base_url TEXT NOT NULL DEFAULT '',
            tts_api_key TEXT NOT NULL DEFAULT '',
            tts_language TEXT NOT NULL DEFAULT 'en-US',
            stt_model_id INTEGER REFERENCES transcription_model_details(model_id) ON DELETE RESTRICT,
            tts_model_id INTEGER REFERENCES speech_model_details(model_id) ON DELETE RESTRICT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS builtin_agent_settings (
            agent_key TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            model_id INTEGER REFERENCES models(id),
            generation_model_id INTEGER REFERENCES models(id),
            mcp_server_id INTEGER REFERENCES mcp_servers(id) ON DELETE RESTRICT,
            knowledge_service_status TEXT NOT NULL DEFAULT 'untested'
                CHECK (knowledge_service_status IN ('untested', 'connected', 'stale', 'failed')),
            knowledge_service_last_tested_at TEXT,
            knowledge_service_last_error TEXT NOT NULL DEFAULT '',
            knowledge_service_tools TEXT NOT NULL DEFAULT '[]',
            automatic_knowledge_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (automatic_knowledge_enabled IN (0, 1)),
            embedding_enabled INTEGER NOT NULL DEFAULT 0
                CHECK (embedding_enabled IN (0, 1)),
            embedding_model_id INTEGER REFERENCES embedding_model_details(model_id) ON DELETE RESTRICT,
            confirm_tools TEXT NOT NULL DEFAULT '[]',
            connected INTEGER NOT NULL DEFAULT 1 CHECK (connected IN (0, 1)),
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS supervisor_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            model_id INTEGER NOT NULL REFERENCES models(id),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS heartbeat_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            interval_minutes INTEGER NOT NULL DEFAULT 30,
            execution_limit INTEGER NOT NULL DEFAULT -1,
            remaining_runs INTEGER NOT NULL DEFAULT -1,
            instructions TEXT NOT NULL,
            next_run_at TEXT,
            last_run_at TEXT,
            last_status TEXT NOT NULL DEFAULT 'never',
            last_message TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            notify_telegram INTEGER NOT NULL DEFAULT 1 CHECK (notify_telegram IN (0, 1)),
            notify_whatsapp INTEGER NOT NULL DEFAULT 0 CHECK (notify_whatsapp IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS heartbeat_task_agents (
            task_id INTEGER NOT NULL
                REFERENCES heartbeat_tasks(id) ON DELETE CASCADE,
            agent_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (task_id, agent_key)
        );

        CREATE TABLE IF NOT EXISTS heartbeat_task_tools (
            task_id INTEGER NOT NULL
                REFERENCES heartbeat_tasks(id) ON DELETE CASCADE,
            agent_key TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (task_id, agent_key, tool_name),
            FOREIGN KEY (task_id, agent_key)
                REFERENCES heartbeat_task_agents(task_id, agent_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS heartbeat_task_agent_state (
            task_id INTEGER NOT NULL
                REFERENCES heartbeat_tasks(id) ON DELETE CASCADE,
            agent_key TEXT NOT NULL,
            last_report TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (task_id, agent_key)
        );

        CREATE TABLE IF NOT EXISTS heartbeat_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            heartbeat_task_id INTEGER REFERENCES heartbeat_tasks(id) ON DELETE SET NULL,
            heartbeat_task_name TEXT NOT NULL DEFAULT '',
            trigger TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            message TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            notification_read_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_started
            ON heartbeat_runs (started_at DESC);

        CREATE INDEX IF NOT EXISTS idx_heartbeat_tasks_due
            ON heartbeat_tasks (enabled, next_run_at);

        CREATE TABLE IF NOT EXISTS telegram_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
            bot_token TEXT NOT NULL DEFAULT '',
            chat_id TEXT NOT NULL DEFAULT '',
            chat_name TEXT NOT NULL DEFAULT '',
            chat_username TEXT NOT NULL DEFAULT '',
            bot_username TEXT NOT NULL DEFAULT '',
            reply_mode TEXT NOT NULL DEFAULT 'text'
                CHECK (reply_mode IN ('text', 'voice')),
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
            "local_whisper"
            if cfg.STT_BACKEND in {"local", "local_whisper"}
            else "openai_compatible",
            cfg.WHISPER_MODEL
            if cfg.STT_BACKEND in {"local", "local_whisper"}
            else cfg.OPENAI_STT_MODEL,
            ""
            if cfg.STT_BACKEND in {"local", "local_whisper"}
            else cfg.OPENAI_STT_BASE_URL,
            (
                "$MOUNIR_STT_API_KEY"
                if os.environ.get("MOUNIR_STT_API_KEY")
                else "$GROQ_API_KEY"
                if cfg.STT_BACKEND == "groq" and os.environ.get("GROQ_API_KEY")
                else ""
            ),
            cfg.WHISPER_LANGUAGE or "auto",
            cfg.TTS_BACKEND
            if cfg.TTS_BACKEND in {"google", "openai_compatible"}
            else "piper",
            (
                cfg.GOOGLE_TTS_VOICE
                if cfg.TTS_BACKEND == "google"
                else cfg.OPENAI_TTS_MODEL
                if cfg.TTS_BACKEND == "openai_compatible"
                else cfg.PIPER_MODEL
            ),
            (
                "https://texttospeech.googleapis.com/v1"
                if cfg.TTS_BACKEND == "google"
                else cfg.OPENAI_TTS_BASE_URL
                if cfg.TTS_BACKEND == "openai_compatible"
                else ""
            ),
            (
                "$GOOGLE_TTS_API_KEY"
                if cfg.TTS_BACKEND == "google" and os.environ.get("GOOGLE_TTS_API_KEY")
                else "$MOUNIR_TTS_API_KEY"
                if cfg.TTS_BACKEND == "openai_compatible"
                and os.environ.get("MOUNIR_TTS_API_KEY")
                else ""
            ),
            cfg.GOOGLE_TTS_LANGUAGE,
            _now(),
        ),
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
        "models": {
            "model": "TEXT",
            "model_type": "TEXT NOT NULL DEFAULT 'text' CHECK (model_type IN ('text', 'embedding', 'speech', 'transcription'))",
            "location": "TEXT NOT NULL DEFAULT 'cloud' CHECK (location IN ('cloud', 'local'))",
            "updated_at": "TEXT",
        },
        "skills": {"source_name": "TEXT NOT NULL DEFAULT ''"},
        "builtin_agent_settings": {
            "model_id": "INTEGER REFERENCES models(id)",
            "generation_model_id": "INTEGER REFERENCES models(id)",
            "mcp_server_id": "INTEGER REFERENCES mcp_servers(id) ON DELETE RESTRICT",
            "knowledge_service_status": (
                "TEXT NOT NULL DEFAULT 'untested' "
                "CHECK (knowledge_service_status IN ('untested', 'connected', 'stale', 'failed'))"
            ),
            "knowledge_service_last_tested_at": "TEXT",
            "knowledge_service_last_error": "TEXT NOT NULL DEFAULT ''",
            "knowledge_service_tools": "TEXT NOT NULL DEFAULT '[]'",
            "automatic_knowledge_enabled": (
                "INTEGER NOT NULL DEFAULT 1 "
                "CHECK (automatic_knowledge_enabled IN (0, 1))"
            ),
            "embedding_enabled": "INTEGER NOT NULL DEFAULT 0 CHECK (embedding_enabled IN (0, 1))",
            "embedding_model_id": "INTEGER REFERENCES embedding_model_details(model_id) ON DELETE RESTRICT",
            "confirm_tools": "TEXT NOT NULL DEFAULT '[]'",
            "connected": "INTEGER NOT NULL DEFAULT 1 CHECK (connected IN (0, 1))",
            "enabled": "INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1))",
        },
        "mcp_servers": {
            "description": "TEXT NOT NULL DEFAULT ''",
            "setup_type": "TEXT NOT NULL DEFAULT ''",
            "transport": "TEXT NOT NULL DEFAULT 'stdio'",
            "headers": "TEXT NOT NULL DEFAULT '{}'",
            "env": "TEXT NOT NULL DEFAULT '{}'",
            "auth_scheme": "TEXT NOT NULL DEFAULT ''",
            "setup_command": "TEXT NOT NULL DEFAULT ''",
            "source_type": "TEXT NOT NULL DEFAULT 'manual'",
            "source_name": "TEXT NOT NULL DEFAULT ''",
            "source_ref": "TEXT NOT NULL DEFAULT ''",
            "source_version": "TEXT NOT NULL DEFAULT ''",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "oauth_redirect_uri": "TEXT NOT NULL DEFAULT ''",
            "oauth_tokens": "TEXT NOT NULL DEFAULT ''",
            "oauth_token_expires_at": "REAL NOT NULL DEFAULT 0",
            "oauth_client_info": "TEXT NOT NULL DEFAULT ''",
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
            "enabled_tools": "TEXT",
            "parent_agent_id": "INTEGER REFERENCES subagents(id) ON DELETE RESTRICT",
        },
        "subagent_nodes": {
            "enabled_tools": "TEXT",
            "workflow_id": "INTEGER REFERENCES workflows(id) ON DELETE CASCADE",
            "position": "INTEGER NOT NULL DEFAULT 0",
        },
        "heartbeat_tasks": {
            "execution_limit": "INTEGER NOT NULL DEFAULT -1",
            "remaining_runs": "INTEGER NOT NULL DEFAULT -1",
        },
        "heartbeat_runs": {
            "notification_read_at": "TEXT",
            "heartbeat_task_id": "INTEGER REFERENCES heartbeat_tasks(id) ON DELETE SET NULL",
            "heartbeat_task_name": "TEXT NOT NULL DEFAULT ''",
        },
        "voice_settings": {
            "tts_voice": "TEXT NOT NULL DEFAULT ''",
            "stt_model_id": "INTEGER REFERENCES transcription_model_details(model_id) ON DELETE RESTRICT",
            "tts_model_id": "INTEGER REFERENCES speech_model_details(model_id) ON DELETE RESTRICT",
        },
        "telegram_settings": {
            "reply_mode": "TEXT NOT NULL DEFAULT 'text' CHECK (reply_mode IN ('text', 'voice'))",
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
    # Discard the incompatible workflow experiment once. It used active/default
    # state and a seeded system record; the current architecture has neither.
    workflow_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(workflows)")
    }
    workflow_reset_key = "workflow_definitions_clean_v2"
    if (
        "is_default" in workflow_columns
        and conn.execute(
            "SELECT 1 FROM app_meta WHERE key = ?", (workflow_reset_key,)
        ).fetchone() is None
    ):
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workflow_nodes'"
        ).fetchone():
            conn.execute("DELETE FROM workflow_nodes")
        conn.execute("DELETE FROM workflow_graph_nodes")
        conn.execute("DELETE FROM subagent_nodes WHERE workflow_id IS NOT NULL")
        conn.execute("DELETE FROM workflows")
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (workflow_reset_key, _now()),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagent_nodes_workflow_position "
        "ON subagent_nodes (workflow_id, position, id)"
    )
    conn.execute("DROP INDEX IF EXISTS idx_subagent_nodes_dynamic")
    conn.execute("DROP INDEX IF EXISTS idx_subagent_nodes_supervisor")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_nodes_global_root "
        "ON subagent_nodes (agent_id) "
        "WHERE workflow_id IS NULL AND parent_node_id IS NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_nodes_global_parent "
        "ON subagent_nodes (parent_node_id, agent_id) "
        "WHERE workflow_id IS NULL AND parent_node_id IS NOT NULL"
    )
    conn.execute("DROP INDEX IF EXISTS idx_subagent_nodes_workflow_root")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagent_nodes_workflow_root "
        "ON subagent_nodes (workflow_id, agent_id) "
        "WHERE workflow_id IS NOT NULL AND parent_node_id IS NULL"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_subagent_nodes_workflow_parent "
        "ON subagent_nodes (workflow_id, parent_node_id, agent_id) "
        "WHERE workflow_id IS NOT NULL AND parent_node_id IS NOT NULL"
    )
    # Retire the former provider-specific onboarding adapter without removing
    # any user-owned server, connection, environment, or subagent records.
    conn.execute(
        "UPDATE mcp_servers SET setup_type = '' WHERE setup_type = 'gmail_oauth'"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_heartbeat_runs_task
        ON heartbeat_runs (heartbeat_task_id, started_at DESC)
        """
    )
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
    _allow_prompt_only_subagents(conn)
    source_migration_key = "subagent_mcp_sources_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (source_migration_key,)
    ).fetchone() is None:
        conn.execute(
            """
            INSERT OR IGNORE INTO subagent_mcp_sources (
                subagent_id, mcp_server_id, enabled_tools, position, created_at
            )
            SELECT id, mcp_server_id, enabled_tools, 0, ?
            FROM subagents
            WHERE mcp_server_id IS NOT NULL
            """,
            (_now(),),
        )
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (source_migration_key, _now()),
        )
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
    _enable_reusable_subagent_placements(conn)
    # The former singleton heartbeat is obsolete. Mark new installations as
    # migrated without creating a visible "General heartbeat" task.
    heartbeat_tasks_migration = "heartbeat_tasks_multi_record_v1"
    heartbeat_tasks_marker = conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (heartbeat_tasks_migration,)
    ).fetchone()
    if heartbeat_tasks_marker is None:
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (heartbeat_tasks_migration, "removed"),
        )
    # Remove the automatically migrated singleton from existing installations.
    # Its recorded ID distinguishes it from user-created heartbeat tasks.
    remove_legacy_heartbeat = "heartbeat_remove_legacy_general_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (remove_legacy_heartbeat,)
    ).fetchone() is None:
        migrated = conn.execute(
            "SELECT value FROM app_meta WHERE key = ?", (heartbeat_tasks_migration,)
        ).fetchone()
        try:
            migrated_task_id = int(migrated["value"])
        except (TypeError, ValueError):
            migrated_task_id = None
        if migrated_task_id is not None:
            conn.execute("DELETE FROM heartbeat_tasks WHERE id = ?", (migrated_task_id,))
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (remove_legacy_heartbeat, _now()),
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
    conn.execute(
        "UPDATE models SET provider = '' WHERE provider IS NULL"
    )
    conn.execute(
        "UPDATE models SET api_key = '' WHERE api_key IS NULL"
    )
    conn.execute(
        "UPDATE models SET created_at = ? WHERE created_at IS NULL OR trim(created_at) = ''",
        (_now(),),
    )
    conn.execute(
        "UPDATE models SET updated_at = COALESCE(updated_at, created_at, ?)",
        (_now(),),
    )
    # Groq uses the same audio-transcriptions contract as the generic transport.
    # Preserve old installations while removing the provider-specific runtime.
    conn.execute(
        """
        UPDATE voice_settings SET stt_provider = 'openai_compatible'
        WHERE stt_provider = 'groq'
        """
    )
    conn.execute(
        """
        UPDATE voice_settings SET tts_voice = ?
        WHERE tts_provider = 'openai_compatible' AND trim(tts_voice) = ''
        """,
        (cfg.OPENAI_TTS_VOICE,),
    )
    _migrate_unified_models(conn)
    _bootstrap_voice_models(conn)
    _ensure_model_detail_triggers(conn)
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
    builtin_confirmation_migration = "builtin_confirmation_defaults_v1"
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?",
        (builtin_confirmation_migration,),
    ).fetchone() is None:
        for definition in builtin_agents.definitions():
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET confirm_tools = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (
                    json.dumps(
                        builtin_agents.default_confirmation_tools(definition["key"]),
                        ensure_ascii=False,
                    ),
                    _now(),
                    definition["key"],
                ),
            )
        conn.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?)",
            (builtin_confirmation_migration, _now()),
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
    _migrate_builtin_gbrain_to_knowledge(conn)
    conn.commit()


def _migrate_builtin_gbrain_to_knowledge(conn: sqlite3.Connection) -> None:
    """Move legacy managed-GBrain state under the Knowledge subagent."""
    row = conn.execute(
        "SELECT * FROM mcp_servers WHERE setup_type = ?",
        (knowledge_protocol.LEGACY_MCP_SETUP_TYPE,),
    ).fetchone()
    if row is None:
        conn.execute(
            "UPDATE builtin_agent_settings SET mcp_server_id = NULL "
            "WHERE agent_key = 'knowledge'"
        )
        return

    server_id = int(row["id"])
    tools = []
    for tool in conn.execute(
        """
        SELECT name, description, input_schema
        FROM mcp_server_tools
        WHERE mcp_server_id = ?
        ORDER BY position, id
        """,
        (server_id,),
    ):
        try:
            input_schema = json.loads(tool["input_schema"] or "{}")
        except json.JSONDecodeError:
            input_schema = {}
        tools.append(
            {
                "name": tool["name"],
                "description": tool["description"] or "",
                "input_schema": input_schema,
            }
        )
    conn.execute(
        """
        UPDATE builtin_agent_settings
        SET mcp_server_id = NULL,
            knowledge_service_status = ?,
            knowledge_service_last_tested_at = ?,
            knowledge_service_last_error = ?,
            knowledge_service_tools = ?,
            updated_at = ?
        WHERE agent_key = 'knowledge'
        """,
        (
            row["connection_status"] or "untested",
            row["last_tested_at"],
            row["last_error"] or "",
            json.dumps(tools, ensure_ascii=False, sort_keys=True),
            _now(),
        ),
    )
    conn.execute(
        "DELETE FROM subagent_mcp_sources WHERE mcp_server_id = ?", (server_id,)
    )
    conn.execute(
        "UPDATE subagents SET mcp_server_id = NULL, enabled_tools = NULL "
        "WHERE mcp_server_id = ?",
        (server_id,),
    )
    conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))


def _required(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    return text


def _normalize_model_base_url(value) -> str:
    base_url = str(value or "").strip().rstrip("/")
    if not base_url:
        return ""
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
    result.pop("setup_type", None)
    result["headers"], headers_configured = _masked_json_object(result.get("headers"))
    result["env"], env_configured = _masked_json_object(result.get("env"))
    result["headers_configured"] = headers_configured
    result["env_configured"] = env_configured
    result["oauth_connected"] = bool(result.pop("oauth_tokens", ""))
    result.pop("oauth_token_expires_at", None)
    result.pop("oauth_client_info", None)
    result["credential_files"] = list_server_files(int(result["id"]))
    result["credentials_configured"] = bool(
        headers_configured
        or env_configured
        or result["oauth_connected"]
        or result["credential_files"]
    )
    result["setup_configured"] = bool(
        result.get("setup_command")
        or result.get("auth_scheme") == "oauth"
        or result["credential_files"]
    )
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
    result["mcp_server_name"] = result.get("server_name") or ""
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


def _active_supervisor_model_defaults() -> dict:
    return {
        "provider": "Ollama (local)",
        "model": cfg.MODEL,
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
    }


def init() -> None:
    """Create tables, migrate user data, and ensure required built-in services."""
    with _connect() as conn:
        _init_schema(conn)
        _migrate_legacy(conn)


# -----------------------------------------------------------------------------
# Agent Skills
# -----------------------------------------------------------------------------

def _skill_public(conn: sqlite3.Connection, row: sqlite3.Row | dict) -> dict:
    item = dict(row)
    item.pop("package_blob", None)
    try:
        item["files"] = json.loads(item.get("files") or "[]")
    except (TypeError, ValueError):
        item["files"] = []
    try:
        item["metadata"] = json.loads(item.get("metadata") or "{}")
    except (TypeError, ValueError):
        item["metadata"] = {}
    assignments = [
        {
            "agent_type": assignment["agent_type"],
            "agent_key": assignment["agent_key"],
        }
        for assignment in conn.execute(
            """
            SELECT agent_type, agent_key FROM skill_assignments
            WHERE skill_id = ? AND enabled = 1
            ORDER BY agent_type, agent_key
            """,
            (item["id"],),
        )
    ]
    item["assignments"] = assignments
    item["assignment_count"] = len(assignments)
    item["file_count"] = len(item["files"])
    item["has_supporting_files"] = len(item["files"]) > 1
    return item


def list_skills() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM skills ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [_skill_public(conn, row) for row in rows]


def get_skill(skill_id: int, *, include_package: bool = False) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            return None
        item = _skill_public(conn, row)
        if include_package:
            item["package_blob"] = bytes(row["package_blob"])
        return item


def get_skill_by_source(source_type: str, source_ref: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM skills WHERE source_type = ? AND source_ref = ?",
            (source_type, source_ref),
        ).fetchone()
        return _skill_public(conn, row) if row else None


def add_skill_package(package: dict) -> dict:
    required = ("name", "description", "skill_md", "package_blob", "content_hash")
    if any(package.get(field) in (None, "") for field in required):
        raise ValueError("The skill package is incomplete.")
    now = _now()
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO skills (
                    name, description, skill_md, package_blob, files, metadata,
                    source_type, source_name, source_ref, source_url, version,
                    content_hash,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package["name"], package["description"], package["skill_md"],
                    sqlite3.Binary(package["package_blob"]),
                    json.dumps(package.get("files") or []),
                    json.dumps(package.get("metadata") or {}, default=str),
                    str(package.get("source_type") or "import"),
                    str(package.get("source_name") or ""),
                    str(package.get("source_ref") or ""),
                    str(package.get("source_url") or ""),
                    str(package.get("version") or ""), package["content_hash"],
                    now, now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError("This skill package is already installed.") from exc
        row = conn.execute(
            "SELECT * FROM skills WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _skill_public(conn, row)


def delete_skill(skill_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        conn.commit()
        return bool(cur.rowcount)


def list_skill_targets() -> list[dict]:
    profile = get_profile()
    targets = [
        {
            "agent_type": "supervisor",
            "agent_key": "supervisor",
            "name": profile.get("assistant_name") or "Mounir",
            "group": "Supervisor",
        }
    ]
    targets.extend(
        {
            "agent_type": "builtin",
            "agent_key": str(item["key"]),
            "name": str(item["name"]),
            "group": "Built-in subagents",
        }
        for item in list_builtin_agents()
    )
    targets.extend(
        {
            "agent_type": "subagent",
            "agent_key": str(item["id"]),
            "name": str(item["name"]),
            "group": "Subagents",
        }
        for item in list_subagents()
    )
    return targets


def _validate_skill_target(
    conn: sqlite3.Connection, agent_type: str, agent_key: str
) -> None:
    if agent_type == "supervisor" and agent_key == "supervisor":
        return
    if agent_type == "builtin":
        if builtin_agents.definition(agent_key) is not None:
            return
    elif agent_type == "subagent":
        try:
            subagent_id = int(agent_key)
        except (TypeError, ValueError):
            subagent_id = -1
        if conn.execute(
            "SELECT 1 FROM subagents WHERE id = ?", (subagent_id,)
        ).fetchone():
            return
    raise ValueError("Select an existing agent.")


def set_skill_assignments(skill_id: int, assignments: list[dict]) -> dict | None:
    normalized: set[tuple[str, str]] = set()
    for assignment in assignments or []:
        if not isinstance(assignment, dict):
            raise ValueError("Skill assignments must identify an agent.")
        normalized.add(
            (
                str(assignment.get("agent_type") or ""),
                str(assignment.get("agent_key") or ""),
            )
        )
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        skill = conn.execute(
            "SELECT * FROM skills WHERE id = ?", (skill_id,)
        ).fetchone()
        if skill is None:
            conn.rollback()
            return None
        for agent_type, agent_key in normalized:
            _validate_skill_target(conn, agent_type, agent_key)
            collision = conn.execute(
                """
                SELECT s.name FROM skill_assignments a
                JOIN skills s ON s.id = a.skill_id
                WHERE a.agent_type = ? AND a.agent_key = ? AND a.enabled = 1
                  AND a.skill_id != ? AND lower(s.name) = lower(?)
                """,
                (agent_type, agent_key, skill_id, skill["name"]),
            ).fetchone()
            if collision:
                raise ValueError(
                    f'This agent already has a skill named "{skill["name"]}".'
                )
        conn.execute("DELETE FROM skill_assignments WHERE skill_id = ?", (skill_id,))
        conn.executemany(
            """
            INSERT INTO skill_assignments
                (skill_id, agent_type, agent_key, enabled, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(skill_id, kind, key, 1, _now()) for kind, key in sorted(normalized)],
        )
        conn.commit()
        return get_skill(skill_id)


def _replace_subagent_skill_assignments(
    conn: sqlite3.Connection, subagent_id: int, skill_ids
) -> None:
    _replace_agent_skill_assignments(
        conn, "subagent", str(int(subagent_id)), skill_ids
    )


def _replace_agent_skill_assignments(
    conn: sqlite3.Connection, agent_type: str, agent_key: str, skill_ids
) -> None:
    if not isinstance(skill_ids, (list, tuple, set)):
        raise ValueError("Selected skills must be a list.")
    try:
        selected = {int(skill_id) for skill_id in skill_ids}
    except (TypeError, ValueError) as exc:
        raise ValueError("Select installed skills for the subagent.") from exc
    if any(skill_id <= 0 for skill_id in selected):
        raise ValueError("Select installed skills for the subagent.")
    rows = list(
        conn.execute(
            f"SELECT id, name FROM skills WHERE id IN ({','.join('?' for _ in selected)})",
            tuple(sorted(selected)),
        )
    ) if selected else []
    if len(rows) != len(selected):
        raise ValueError("One or more selected skills are no longer installed.")
    names: set[str] = set()
    for row in rows:
        normalized = str(row["name"]).casefold()
        if normalized in names:
            raise ValueError(
                f'This agent cannot use multiple skills named "{row["name"]}".'
            )
        names.add(normalized)
    conn.execute(
        "DELETE FROM skill_assignments WHERE agent_type = ? AND agent_key = ?",
        (agent_type, agent_key),
    )
    conn.executemany(
        """
        INSERT INTO skill_assignments
            (skill_id, agent_type, agent_key, enabled, created_at)
        VALUES (?, ?, ?, 1, ?)
        """,
        [(skill_id, agent_type, agent_key, _now()) for skill_id in sorted(selected)],
    )


def replace_agent_skill_assignments(
    agent_type: str, agent_key: str, skill_ids
) -> list[int]:
    normalized_type = str(agent_type or "")
    normalized_key = str(agent_key or "")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _validate_skill_target(conn, normalized_type, normalized_key)
        _replace_agent_skill_assignments(
            conn, normalized_type, normalized_key, skill_ids
        )
        conn.commit()
    return [int(skill["id"]) for skill in list_agent_skills(normalized_type, normalized_key)]


def list_agent_skills(agent_type: str, agent_key: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.* FROM skills s
            JOIN skill_assignments a ON a.skill_id = s.id
            WHERE a.agent_type = ? AND a.agent_key = ? AND a.enabled = 1
            ORDER BY s.name COLLATE NOCASE, s.id
            """,
            (agent_type, str(agent_key)),
        ).fetchall()
        return [_skill_public(conn, row) for row in rows]


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
# Unified model registry migration
# -----------------------------------------------------------------------------

MODEL_TYPES = {"text", "embedding", "speech", "transcription"}
MODEL_MIGRATION_KEY = "unified_model_registry_v1"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _infer_model_location(provider: str, base_url: str) -> str:
    provider_name = str(provider or "").lower()
    address = str(base_url or "").lower()
    local_markers = ("local", "ollama", "lm studio", "vllm", "llama.cpp")
    local_hosts = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
    return "local" if any(value in provider_name for value in local_markers) or any(
        value in address for value in local_hosts
    ) else "cloud"


def _normalize_model_location(location: str, provider: str, base_url: str) -> str:
    normalized = str(location or "").strip().lower()
    if not normalized:
        return _infer_model_location(provider, base_url)
    if normalized not in {"cloud", "local"}:
        raise ValueError("location must be cloud or local.")
    return normalized


def _unique_model_name(conn: sqlite3.Connection, requested: str, type_label: str) -> str:
    base = _required(requested, "name")
    if conn.execute("SELECT 1 FROM models WHERE name = ?", (base,)).fetchone() is None:
        return base
    candidate = f"{base} ({type_label})"
    suffix = 2
    while conn.execute("SELECT 1 FROM models WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base} ({type_label} {suffix})"
        suffix += 1
    return candidate


def _insert_migrated_model(
    conn: sqlite3.Connection,
    row: dict,
    model_type: str,
    type_label: str,
    *,
    provider: str | None = None,
) -> int:
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO models
            (name, model_type, location, model, provider, base_url, api_key,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _unique_model_name(conn, row.get("name") or type_label, type_label),
            model_type,
            row.get("location") or _infer_model_location(
                provider or row.get("provider") or "", row.get("base_url") or ""
            ),
            _required(row.get("model"), "model ID"),
            str(provider if provider is not None else row.get("provider") or "").strip(),
            row.get("base_url") or "",
            row.get("api_key") or "",
            row.get("created_at") or now,
            row.get("updated_at") or row.get("created_at") or now,
        ),
    )
    return int(cur.lastrowid)


def _rebuild_model_reference_tables(
    conn: sqlite3.Connection,
    embedding_ids: dict[int, int],
    voice_ids: dict[int, int],
    *,
    rebuild_builtin: bool,
    rebuild_voice: bool,
) -> None:
    builtin_rows = (
        [dict(row) for row in conn.execute("SELECT * FROM builtin_agent_settings")]
        if rebuild_builtin
        else []
    )
    voice_row = (
        conn.execute("SELECT * FROM voice_settings WHERE id = 1").fetchone()
        if rebuild_voice
        else None
    )
    voice_data = dict(voice_row) if voice_row is not None else None
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        if rebuild_builtin:
            conn.execute("DROP TABLE IF EXISTS builtin_agent_settings_unified_upgrade")
            conn.execute(
                """
                CREATE TABLE builtin_agent_settings_unified_upgrade (
                    agent_key TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    model_id INTEGER REFERENCES models(id),
                    generation_model_id INTEGER REFERENCES models(id),
                    mcp_server_id INTEGER REFERENCES mcp_servers(id) ON DELETE RESTRICT,
                    knowledge_service_status TEXT NOT NULL DEFAULT 'untested'
                        CHECK (knowledge_service_status IN ('untested', 'connected', 'stale', 'failed')),
                    knowledge_service_last_tested_at TEXT,
                    knowledge_service_last_error TEXT NOT NULL DEFAULT '',
                    knowledge_service_tools TEXT NOT NULL DEFAULT '[]',
                    automatic_knowledge_enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (automatic_knowledge_enabled IN (0, 1)),
                    embedding_enabled INTEGER NOT NULL DEFAULT 0
                        CHECK (embedding_enabled IN (0, 1)),
                    embedding_model_id INTEGER
                        REFERENCES embedding_model_details(model_id) ON DELETE RESTRICT,
                    confirm_tools TEXT NOT NULL DEFAULT '[]',
                    connected INTEGER NOT NULL DEFAULT 1 CHECK (connected IN (0, 1)),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    updated_at TEXT NOT NULL
                )
                """
            )
            for row in builtin_rows:
                legacy_embedding_id = row.get("embedding_model_id")
                row["embedding_model_id"] = (
                    embedding_ids.get(int(legacy_embedding_id))
                    if legacy_embedding_id is not None
                    else None
                )
                conn.execute(
                    """
                    INSERT INTO builtin_agent_settings_unified_upgrade
                        (agent_key, model, model_id, generation_model_id, mcp_server_id,
                         knowledge_service_status, knowledge_service_last_tested_at,
                         knowledge_service_last_error, knowledge_service_tools,
                         automatic_knowledge_enabled, embedding_enabled,
                         embedding_model_id, confirm_tools, connected, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        row.get(column)
                        for column in (
                            "agent_key", "model", "model_id", "generation_model_id",
                            "mcp_server_id", "knowledge_service_status",
                            "knowledge_service_last_tested_at",
                            "knowledge_service_last_error", "knowledge_service_tools",
                            "automatic_knowledge_enabled",
                            "embedding_enabled", "embedding_model_id", "confirm_tools",
                            "connected", "enabled", "updated_at",
                        )
                    ),
                )
            conn.execute("DROP TABLE builtin_agent_settings")
            conn.execute(
                "ALTER TABLE builtin_agent_settings_unified_upgrade RENAME TO builtin_agent_settings"
            )
        if rebuild_voice:
            conn.execute("DROP TABLE IF EXISTS voice_settings_unified_upgrade")
            conn.execute(
                """
                CREATE TABLE voice_settings_unified_upgrade (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    stt_provider TEXT NOT NULL,
                    stt_model TEXT NOT NULL,
                    stt_base_url TEXT NOT NULL DEFAULT '',
                    stt_api_key TEXT NOT NULL DEFAULT '',
                    stt_language TEXT NOT NULL DEFAULT 'auto',
                    tts_provider TEXT NOT NULL,
                    tts_model TEXT NOT NULL,
                    tts_voice TEXT NOT NULL DEFAULT '',
                    tts_base_url TEXT NOT NULL DEFAULT '',
                    tts_api_key TEXT NOT NULL DEFAULT '',
                    tts_language TEXT NOT NULL DEFAULT 'en-US',
                    stt_model_id INTEGER
                        REFERENCES transcription_model_details(model_id) ON DELETE RESTRICT,
                    tts_model_id INTEGER
                        REFERENCES speech_model_details(model_id) ON DELETE RESTRICT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            if voice_data is not None:
                stt_id = voice_data.get("stt_model_id")
                tts_id = voice_data.get("tts_model_id")
                voice_data["stt_model_id"] = (
                    voice_ids.get(int(stt_id)) if stt_id is not None else None
                )
                voice_data["tts_model_id"] = (
                    voice_ids.get(int(tts_id)) if tts_id is not None else None
                )
                columns = (
                    "id", "stt_provider", "stt_model", "stt_base_url", "stt_api_key",
                    "stt_language", "tts_provider", "tts_model", "tts_voice",
                    "tts_base_url", "tts_api_key", "tts_language", "stt_model_id",
                    "tts_model_id", "updated_at",
                )
                conn.execute(
                    f"INSERT INTO voice_settings_unified_upgrade ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    tuple(voice_data.get(column) for column in columns),
                )
            conn.execute("DROP TABLE voice_settings")
            conn.execute(
                "ALTER TABLE voice_settings_unified_upgrade RENAME TO voice_settings"
            )
        if _table_exists(conn, "embedding_models"):
            conn.execute("DROP TABLE embedding_models")
        if _table_exists(conn, "voice_models"):
            conn.execute("DROP TABLE voice_models")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    violation = conn.execute("PRAGMA foreign_key_check").fetchone()
    if violation is not None:
        raise sqlite3.IntegrityError(
            f"Foreign key check failed after model migration: {tuple(violation)}"
        )


def _migrate_unified_models(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM app_meta WHERE key = ?", (MODEL_MIGRATION_KEY,)
    ).fetchone():
        return

    for row in conn.execute("SELECT * FROM models WHERE model_type = 'text'").fetchall():
        item = dict(row)
        location = item.get("location") or _infer_model_location(
            item.get("provider") or "", item.get("base_url") or ""
        )
        conn.execute(
            """
            UPDATE models
            SET location = ?, updated_at = COALESCE(updated_at, created_at, ?)
            WHERE id = ?
            """,
            (location, _now(), item["id"]),
        )
        conn.execute(
            "INSERT OR IGNORE INTO text_model_details (model_id) VALUES (?)",
            (item["id"],),
        )

    embedding_ids: dict[int, int] = {}
    had_embedding_table = _table_exists(conn, "embedding_models")
    if had_embedding_table:
        for legacy in conn.execute("SELECT * FROM embedding_models ORDER BY id"):
            row = dict(legacy)
            mapping_key = f"legacy_embedding_model:{int(row['id'])}"
            mapped = conn.execute(
                "SELECT value FROM app_meta WHERE key = ?", (mapping_key,)
            ).fetchone()
            new_id = int(mapped["value"]) if mapped else _insert_migrated_model(
                conn, row, "embedding", "Embeddings", provider=row.get("adapter") or ""
            )
            embedding_ids[int(row["id"])] = new_id
            conn.execute(
                """
                INSERT OR IGNORE INTO embedding_model_details
                    (model_id, adapter, dimensions, connection_status,
                     last_tested_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id, row.get("adapter") or "openai_compatible",
                    row.get("dimensions"), row.get("connection_status") or "untested",
                    row.get("last_tested_at"), row.get("last_error") or "",
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                (mapping_key, str(new_id)),
            )

    voice_ids: dict[int, int] = {}
    had_voice_table = _table_exists(conn, "voice_models")
    if had_voice_table:
        for legacy in conn.execute("SELECT * FROM voice_models ORDER BY id"):
            row = dict(legacy)
            is_tts = row.get("kind") == "tts"
            model_type = "speech" if is_tts else "transcription"
            mapping_key = f"legacy_voice_model:{int(row['id'])}"
            mapped = conn.execute(
                "SELECT value FROM app_meta WHERE key = ?", (mapping_key,)
            ).fetchone()
            new_id = int(mapped["value"]) if mapped else _insert_migrated_model(
                conn, row, model_type, "Speech" if is_tts else "Transcription"
            )
            voice_ids[int(row["id"])] = new_id
            if is_tts:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO speech_model_details (model_id, voice, language)
                    VALUES (?, ?, ?)
                    """,
                    (new_id, row.get("voice") or "", row.get("language") or "auto"),
                )
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO transcription_model_details (model_id, language)
                    VALUES (?, ?)
                    """,
                    (new_id, row.get("language") or "auto"),
                )
            conn.execute(
                "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
                (mapping_key, str(new_id)),
            )

    if had_embedding_table or had_voice_table:
        _rebuild_model_reference_tables(
            conn,
            embedding_ids,
            voice_ids,
            rebuild_builtin=had_embedding_table,
            rebuild_voice=had_voice_table,
        )
    conn.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
        (MODEL_MIGRATION_KEY, _now()),
    )


def _ensure_model_detail_triggers(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS text_model_details_type_guard
        BEFORE INSERT ON text_model_details
        WHEN COALESCE((SELECT model_type FROM models WHERE id = NEW.model_id), '') != 'text'
        BEGIN SELECT RAISE(ABORT, 'text model details require a text model'); END;

        CREATE TRIGGER IF NOT EXISTS embedding_model_details_type_guard
        BEFORE INSERT ON embedding_model_details
        WHEN COALESCE((SELECT model_type FROM models WHERE id = NEW.model_id), '') != 'embedding'
        BEGIN SELECT RAISE(ABORT, 'embedding model details require an embedding model'); END;

        CREATE TRIGGER IF NOT EXISTS speech_model_details_type_guard
        BEFORE INSERT ON speech_model_details
        WHEN COALESCE((SELECT model_type FROM models WHERE id = NEW.model_id), '') != 'speech'
        BEGIN SELECT RAISE(ABORT, 'speech model details require a speech model'); END;

        CREATE TRIGGER IF NOT EXISTS transcription_model_details_type_guard
        BEFORE INSERT ON transcription_model_details
        WHEN COALESCE((SELECT model_type FROM models WHERE id = NEW.model_id), '') != 'transcription'
        BEGIN SELECT RAISE(ABORT, 'transcription model details require a transcription model'); END;

        CREATE TRIGGER IF NOT EXISTS models_type_immutable
        BEFORE UPDATE OF model_type ON models
        WHEN OLD.model_type != NEW.model_type
        BEGIN SELECT RAISE(ABORT, 'a saved model type cannot be changed'); END;
        """
    )


# -----------------------------------------------------------------------------
# Voice configuration
# -----------------------------------------------------------------------------

VOICE_PROVIDERS = {
    "stt": {"local_whisper", "openai_compatible"},
    "tts": {"piper", "moss_onnx", "openai_compatible", "google"},
}

STT_LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.IGNORECASE)

VOICE_PROVIDER_ALIASES = {
    "stt": {
        "local": "local_whisper",
        "groq": "openai_compatible",
        "openai": "openai_compatible",
    },
    "tts": {
        "local": "piper",
        "moss": "moss_onnx",
        "openai": "openai_compatible",
    },
}


def _voice_location(kind: str, provider: str) -> str:
    local = provider == "local_whisper" if kind == "stt" else provider in {"piper", "moss_onnx"}
    return "local" if local else "cloud"


def _validate_voice_model(kind: str, supplied: dict, current: dict | None = None) -> dict:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind not in VOICE_PROVIDERS:
        raise ValueError("voice type is not supported")
    provider = str(supplied.get("provider") or (current or {}).get("provider") or "").strip().lower()
    provider = VOICE_PROVIDER_ALIASES[normalized_kind].get(provider, provider)
    if provider not in VOICE_PROVIDERS[normalized_kind]:
        raise ValueError(f"{normalized_kind.upper()} provider is not supported")
    model = _required(
        supplied.get("model", (current or {}).get("model")),
        f"{normalized_kind.upper()} model",
    )
    language = str(
        supplied.get("language", (current or {}).get("language") or "auto") or "auto"
    ).strip()
    if normalized_kind == "stt":
        language = language.lower()
        if language != "auto" and not STT_LANGUAGE_RE.fullmatch(language):
            raise ValueError("STT language is not supported; use auto or a valid language code")
    elif len(language) > 32:
        raise ValueError("TTS language is too long")
    base_url = str(
        supplied.get("base_url", (current or {}).get("base_url") or "") or ""
    ).strip()
    remote = provider in {"openai_compatible", "google"}
    if remote:
        if not base_url:
            raise ValueError(f"{normalized_kind.upper()} API URL is required for this provider")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"{normalized_kind.upper()} API URL must start with http:// or https://"
            )
        base_url = base_url.rstrip("/")
    else:
        base_url = ""
    voice = str(
        supplied.get("voice", (current or {}).get("voice") or "") or ""
    ).strip()
    if len(voice) > 160:
        raise ValueError("TTS voice is too long")
    if normalized_kind == "tts" and provider in {"openai_compatible", "moss_onnx"} and not voice:
        raise ValueError("TTS voice is required for this provider")
    if normalized_kind != "tts" or provider not in {"openai_compatible", "moss_onnx"}:
        voice = ""
    api_key = supplied.get("api_key", _UNSET)
    if api_key is _UNSET or (current is not None and not str(api_key or "").strip()):
        api_key = (current or {}).get("api_key") or ""
    else:
        api_key = str(api_key or "").strip()
    if provider == "google" and not api_key:
        raise ValueError("TTS API key is required for this provider")
    return {
        "kind": normalized_kind,
        "location": _voice_location(normalized_kind, provider),
        "provider": provider,
        "model": model,
        "voice": voice,
        "base_url": base_url,
        "api_key": api_key,
        "language": language or "auto",
    }


def _voice_settings_snapshot(kind: str, model: dict) -> dict:
    prefix = kind.lower()
    return {
        f"{prefix}_provider": model["provider"],
        f"{prefix}_model": model["model"],
        f"{prefix}_base_url": model.get("base_url") or "",
        f"{prefix}_api_key": model.get("api_key") or "",
        f"{prefix}_language": model.get("language") or "auto",
        **({"tts_voice": model.get("voice") or ""} if prefix == "tts" else {}),
        f"{prefix}_model_id": int(model["id"]),
    }


def _bootstrap_voice_models(conn: sqlite3.Connection) -> None:
    """Promote the two legacy singleton voice configurations to reusable records."""
    row = conn.execute("SELECT * FROM voice_settings WHERE id = 1").fetchone()
    if row is None:
        return
    updates: dict[str, object] = {}
    for kind, base_name in (("stt", "Default transcription"), ("tts", "Default speech")):
        if row[f"{kind}_model_id"] is not None:
            continue
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO models
                (name, model_type, location, provider, model, base_url, api_key,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _unique_model_name(
                    conn, base_name, "Transcription" if kind == "stt" else "Speech"
                ),
                "transcription" if kind == "stt" else "speech",
                _voice_location(kind, row[f"{kind}_provider"]),
                row[f"{kind}_provider"],
                row[f"{kind}_model"],
                row[f"{kind}_base_url"] or "",
                row[f"{kind}_api_key"] or "",
                now,
                now,
            ),
        )
        model_id = int(cur.lastrowid)
        if kind == "tts":
            conn.execute(
                "INSERT INTO speech_model_details (model_id, voice, language) VALUES (?, ?, ?)",
                (model_id, row["tts_voice"] or "", row["tts_language"] or "auto"),
            )
        else:
            conn.execute(
                "INSERT INTO transcription_model_details (model_id, language) VALUES (?, ?)",
                (model_id, row["stt_language"] or "auto"),
            )
        updates[f"{kind}_model_id"] = model_id
    if updates:
        sets = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"UPDATE voice_settings SET {sets}, updated_at = ? WHERE id = 1",
            (*updates.values(), _now()),
        )


def add_voice_model(name: str, kind: str, **kwargs) -> dict:
    fields = _validate_voice_model(kind, kwargs)
    now = _now()
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO models
                    (name, model_type, location, provider, model, base_url, api_key,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required(name, "name"),
                    "speech" if fields["kind"] == "tts" else "transcription",
                    fields["location"], fields["provider"], fields["model"],
                    fields["base_url"], fields["api_key"], now, now,
                ),
            )
            model_id = int(cur.lastrowid)
            if fields["kind"] == "tts":
                conn.execute(
                    "INSERT INTO speech_model_details (model_id, voice, language) VALUES (?, ?, ?)",
                    (model_id, fields["voice"], fields["language"]),
                )
            else:
                conn.execute(
                    "INSERT INTO transcription_model_details (model_id, language) VALUES (?, ?)",
                    (model_id, fields["language"]),
                )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
    return get_voice_model(model_id)


def _voice_model_query() -> str:
    return """
        SELECT m.id, m.name,
               CASE m.model_type WHEN 'speech' THEN 'tts' ELSE 'stt' END AS kind,
               m.location, m.provider, m.model,
               COALESCE(s.voice, '') AS voice,
               m.base_url, m.api_key,
               COALESCE(s.language, t.language, 'auto') AS language,
               m.created_at, m.updated_at
        FROM models m
        LEFT JOIN speech_model_details s ON s.model_id = m.id
        LEFT JOIN transcription_model_details t ON t.model_id = m.id
    """


def _get_voice_model_with_conn(conn: sqlite3.Connection, model_id: int) -> dict | None:
    row = conn.execute(
        _voice_model_query()
        + " WHERE m.id = ? AND m.model_type IN ('speech', 'transcription')",
        (model_id,),
    ).fetchone()
    return dict(row) if row else None


def get_voice_model(model_id: int) -> dict | None:
    with _connect() as conn:
        return _get_voice_model_with_conn(conn, model_id)


def list_voice_models(kind: str | None = None) -> list[dict]:
    with _connect() as conn:
        if kind is None:
            rows = conn.execute(
                _voice_model_query()
                + " WHERE m.model_type IN ('speech', 'transcription') ORDER BY m.name"
            ).fetchall()
        else:
            normalized = str(kind).strip().lower()
            if normalized not in VOICE_PROVIDERS:
                raise ValueError("voice type is not supported")
            model_type = "speech" if normalized == "tts" else "transcription"
            rows = conn.execute(
                _voice_model_query() + " WHERE m.model_type = ? ORDER BY m.name",
                (model_type,),
            ).fetchall()
    return [dict(row) for row in rows]


def update_voice_model(model_id: int, *, _clear_api_key: bool = False, **kwargs) -> dict | None:
    current = get_voice_model(model_id)
    if current is None:
        return None
    requested_kind = str(kwargs.get("kind") or current["kind"]).strip().lower()
    if requested_kind != current["kind"]:
        raise ValueError("A saved voice model's type cannot be changed")
    fields = _validate_voice_model(current["kind"], kwargs, current)
    if _clear_api_key:
        fields["api_key"] = ""
    if kwargs.get("name") is not None:
        fields["name"] = _required(kwargs["name"], "name")
    now = _now()
    with _connect() as conn:
        try:
            base_fields = {
                key: fields[key]
                for key in ("name", "location", "provider", "model", "base_url", "api_key")
                if key in fields
            }
            base_fields["updated_at"] = now
            sets = ", ".join(f"{key} = ?" for key in base_fields)
            conn.execute(
                f"UPDATE models SET {sets} WHERE id = ?",
                (*base_fields.values(), model_id),
            )
            if current["kind"] == "tts":
                conn.execute(
                    "UPDATE speech_model_details SET voice = ?, language = ? WHERE model_id = ?",
                    (fields["voice"], fields["language"], model_id),
                )
            else:
                conn.execute(
                    "UPDATE transcription_model_details SET language = ? WHERE model_id = ?",
                    (fields["language"], model_id),
                )
            refreshed = _get_voice_model_with_conn(conn, model_id)
            if refreshed is not None:
                selected_column = f"{current['kind']}_model_id"
                selected = conn.execute(
                    f"SELECT 1 FROM voice_settings WHERE id = 1 AND {selected_column} = ?",
                    (model_id,),
                ).fetchone()
                if selected:
                    snapshot = _voice_settings_snapshot(current["kind"], refreshed)
                    snapshot.pop(selected_column, None)
                    snapshot["updated_at"] = _now()
                    snapshot_sets = ", ".join(f"{key} = ?" for key in snapshot)
                    conn.execute(
                        f"UPDATE voice_settings SET {snapshot_sets} WHERE id = 1",
                        tuple(snapshot.values()),
                    )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
    return get_voice_model(model_id)


def delete_voice_model_result(model_id: int) -> DeletionResult:
    with _connect() as conn:
        model = _get_voice_model_with_conn(conn, model_id)
        if model is None:
            return DeletionResult("not_found")
        selected = conn.execute(
            f"SELECT 1 FROM voice_settings WHERE id = 1 AND {model['kind']}_model_id = ?",
            (model_id,),
        ).fetchone()
        if selected:
            label = "text-to-speech" if model["kind"] == "tts" else "speech-to-text"
            return DeletionResult("in_use", (f"the active {label} setting",))
        conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        conn.commit()
    return DeletionResult("deleted")


def voice_model_for_api(model: dict | None) -> dict | None:
    if model is None:
        return None
    result = dict(model)
    result["api_key_configured"] = bool(result.pop("api_key", ""))
    return result


def get_voice_settings(*, include_secrets: bool = False) -> dict:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM voice_settings WHERE id = 1").fetchone()
        selected = {
            kind: _get_voice_model_with_conn(conn, int(row[f"{kind}_model_id"]))
            if row is not None and row[f"{kind}_model_id"] is not None
            else None
            for kind in ("stt", "tts")
        }
    if row is None:
        return {"stt": {}, "tts": {}}
    result = {}
    for kind in ("stt", "tts"):
        model = selected[kind]
        secret = (model or {}).get("api_key", row[f"{kind}_api_key"]) or ""
        item = {
            "model_id": int(model["id"]) if model else None,
            "name": model["name"] if model else "",
            "provider": (model or {}).get("provider", row[f"{kind}_provider"]),
            "model": (model or {}).get("model", row[f"{kind}_model"]),
            "base_url": (model or {}).get("base_url", row[f"{kind}_base_url"]) or "",
            "language": (model or {}).get("language", row[f"{kind}_language"]) or "auto",
            "api_key_configured": bool(secret),
        }
        if kind == "tts":
            item["voice"] = (model or {}).get("voice", row["tts_voice"]) or ""
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


def update_voice_settings(*, stt=None, tts=None, stt_model_id=None, tts_model_id=None) -> dict:
    updates: dict[str, object] = {}
    for kind, supplied in (("stt", stt), ("tts", tts)):
        if supplied is None:
            continue
        if not isinstance(supplied, dict):
            raise ValueError(f"{kind.upper()} configuration must be an object")
        selected_id = get_voice_settings().get(kind, {}).get("model_id")
        current_model = get_voice_model(int(selected_id)) if selected_id else None
        if current_model is not None:
            # The legacy update contract used the singleton snapshot as its
            # source of truth. Keep that behavior for callers upgrading in
            # place, while the new selection-only API uses model IDs below.
            with _connect() as conn:
                legacy = conn.execute(
                    f"SELECT {kind}_api_key FROM voice_settings WHERE id = 1"
                ).fetchone()
            if legacy is not None:
                current_model["api_key"] = legacy[f"{kind}_api_key"] or ""
        fields = _validate_voice_model(kind, supplied, current_model)
        if current_model is None:
            current_model = add_voice_model(
                "Default transcription" if kind == "stt" else "Default speech",
                kind,
                **fields,
            )
        else:
            supplied_key = supplied.get("api_key", _UNSET)
            current_model = update_voice_model(
                int(current_model["id"]),
                _clear_api_key=(
                    not bool(current_model.get("api_key"))
                    and (supplied_key is _UNSET or not str(supplied_key or "").strip())
                ),
                **fields,
            )
        updates.update(_voice_settings_snapshot(kind, current_model))

    for kind, requested_id in (("stt", stt_model_id), ("tts", tts_model_id)):
        if requested_id is None:
            continue
        try:
            model = get_voice_model(int(requested_id))
        except (TypeError, ValueError):
            model = None
        if model is None or model["kind"] != kind:
            raise ValueError(f"Select a saved {kind.upper()} model")
        updates.update(_voice_settings_snapshot(kind, model))
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
            "reply_mode": "text",
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
    reply_mode: str | None = None,
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
    if reply_mode is not None:
        reply_mode = str(reply_mode or "").strip().lower()
        if reply_mode not in {"text", "voice"}:
            raise ValueError("Telegram reply mode must be text or voice")
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
        if reply_mode is not None:
            fields["reply_mode"] = reply_mode
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
        if enabled is not None or bot_token is not None:
            active = current["enabled"] if enabled is None else enabled
            resulting_token = bot_token or current["bot_token"]
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
    return {
        "model_id": None,
        "model": defaults["model"] or fallback_model,
        "provider": defaults["provider"],
        "base_url": defaults["base_url"],
        "api_key": "",
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
    ]
    return {
        "model_id": runtime["model_id"],
        "model": runtime["model"],
        "provider": runtime["provider"],
        "model_options": options,
        "skill_ids": [
            int(skill["id"])
            for skill in list_agent_skills("supervisor", "supervisor")
        ],
    }


def update_supervisor_model(model_id: int) -> dict:
    try:
        requested_id = int(model_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("choose a model") from exc
    selected = get_model(requested_id)
    if selected is None:
        raise ValueError("choose a configured model")
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
    fallback_provider: str = "",
) -> dict:
    """Resolve one assigned model record for the universal LLM adapter."""
    key = str(agent_key or "").removeprefix("builtin:").strip()
    try:
        with _connect() as conn:
            selected = conn.execute(
                """
                SELECT m.model, m.provider, m.base_url, m.api_key
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
            "provider": selected["provider"],
            "base_url": selected["base_url"],
            "api_key": _resolve_key(selected["api_key"] or ""),
        }
    return {
        "model": fallback_model,
        "provider": fallback_provider,
        "base_url": fallback_base_url,
        "api_key": fallback_api_key,
    }


def get_builtin_agent_generation_runtime(agent_key: str) -> dict | None:
    """Resolve the optional saved generation model for a built-in specialist."""
    key = str(agent_key or "").removeprefix("builtin:").strip()
    try:
        with _connect() as conn:
            selected = conn.execute(
                """
                SELECT m.model, m.provider, m.base_url, m.api_key
                FROM builtin_agent_settings s
                JOIN models m ON m.id = s.generation_model_id
                WHERE s.agent_key = ?
                """,
                (key,),
            ).fetchone()
    except sqlite3.OperationalError:
        selected = None
    if selected is None:
        return None
    return {
        "model": selected["model"],
        "provider": selected["provider"],
        "base_url": selected["base_url"],
        "api_key": _resolve_key(selected["api_key"] or ""),
    }


def get_knowledge_embedding_runtime() -> dict | None:
    """Resolve the enabled embedding connection without exposing it to the API."""
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT m.id, m.name, m.location, e.adapter, m.model, m.base_url,
                       m.api_key, e.dimensions, e.connection_status,
                       e.last_tested_at, e.last_error, m.created_at, m.updated_at
                FROM builtin_agent_settings s
                JOIN models m ON m.id = s.embedding_model_id
                JOIN embedding_model_details e ON e.model_id = m.id
                WHERE s.agent_key = 'knowledge' AND s.embedding_enabled = 1
                """
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        return None
    result = dict(row)
    result["api_key"] = _resolve_key(result.get("api_key") or "")
    return result


def get_knowledge_service_state() -> dict:
    """Return GBrain discovery state owned by the Knowledge subagent."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT knowledge_service_status, knowledge_service_last_tested_at,
                   knowledge_service_last_error, knowledge_service_tools
            FROM builtin_agent_settings
            WHERE agent_key = 'knowledge'
            """
        ).fetchone()
    if row is None:
        return {
            "status": "untested",
            "last_tested_at": None,
            "last_error": "",
            "tools": [],
        }
    try:
        tools = json.loads(row["knowledge_service_tools"] or "[]")
    except (json.JSONDecodeError, TypeError):
        tools = []
    if not isinstance(tools, list):
        tools = []
    return {
        "status": row["knowledge_service_status"] or "untested",
        "last_tested_at": row["knowledge_service_last_tested_at"],
        "last_error": row["knowledge_service_last_error"] or "",
        "tools": [tool for tool in tools if isinstance(tool, dict)],
    }


def save_knowledge_service_tools(tools: list[dict]) -> dict:
    """Persist a successful GBrain discovery under Knowledge itself."""
    normalized = []
    seen = set()
    for tool in tools:
        name = _required((tool or {}).get("name"), "tool name")
        if name in seen:
            continue
        seen.add(name)
        schema = (tool or {}).get("input_schema") or {}
        normalized.append(
            {
                "name": name,
                "description": str((tool or {}).get("description") or ""),
                "input_schema": schema if isinstance(schema, dict) else {},
            }
        )
    tested_at = _now()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE builtin_agent_settings
            SET knowledge_service_status = 'connected',
                knowledge_service_last_tested_at = ?,
                knowledge_service_last_error = '',
                knowledge_service_tools = ?, updated_at = ?
            WHERE agent_key = 'knowledge'
            """,
            (
                tested_at,
                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                tested_at,
            ),
        )
        conn.commit()
    return get_knowledge_service_state()


def record_knowledge_service_failure(error: str) -> dict:
    """Record a GBrain failure without discarding its last tool snapshot."""
    tested_at = _now()
    detail = " ".join(str(error or "Connection failed").split())[:1000]
    with _connect() as conn:
        conn.execute(
            """
            UPDATE builtin_agent_settings
            SET knowledge_service_status = 'failed',
                knowledge_service_last_tested_at = ?,
                knowledge_service_last_error = ?, updated_at = ?
            WHERE agent_key = 'knowledge'
            """,
            (tested_at, detail, tested_at),
        )
        conn.commit()
    return get_knowledge_service_state()


def get_builtin_confirmation_tools(agent_key: str) -> list[str]:
    """Return the persisted confirmation rules for one built-in specialist."""
    key = str(agent_key or "").removeprefix("builtin:").strip()
    try:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT confirm_tools FROM builtin_agent_settings
                WHERE agent_key = ?
                """,
                (key,),
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is None:
        return builtin_agents.default_confirmation_tools(key)
    try:
        parsed = json.loads(row["confirm_tools"] or "[]")
    except (json.JSONDecodeError, TypeError):
        return builtin_agents.default_confirmation_tools(key)
    return [str(item) for item in parsed if str(item)] if isinstance(parsed, list) else []


def _builtin_capabilities_with_confirmation() -> list[dict]:
    """Overlay saved confirmation policy on the shipped built-in tool catalog."""
    result = builtin_agents.capabilities()
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT agent_key, confirm_tools FROM builtin_agent_settings"
            ).fetchall()
        stored = {str(row["agent_key"]): row["confirm_tools"] for row in rows}
    except sqlite3.OperationalError:
        stored = {}
    for agent in result:
        raw_rules = stored.get(agent["builtin_key"])
        try:
            rules = (
                set(json.loads(raw_rules or "[]"))
                if raw_rules is not None
                else set(builtin_agents.default_confirmation_tools(agent["builtin_key"]))
            )
        except (json.JSONDecodeError, TypeError):
            rules = set(builtin_agents.default_confirmation_tools(agent["builtin_key"]))
        for tool in agent["tools"]:
            tool["requires_confirmation"] = (
                "*" in rules or tool["name"] in rules
            )
        agent["confirm_tools"] = sorted(rules)
    return result


def list_builtin_agents(*, connected_only: bool = False) -> list[dict]:
    capabilities = {
        item["builtin_key"]: item for item in _builtin_capabilities_with_confirmation()
    }
    models = list_models()
    result = []
    for definition in builtin_agents.definitions():
        key = definition["key"]
        options = [
            {
                "id": model["id"],
                "model": model["model"],
                "label": f"{model['name']} — {model['model']}",
            }
            for model in models
        ]
        with _connect() as conn:
            setting = conn.execute(
                """
                SELECT s.model_id, s.generation_model_id,
                       s.automatic_knowledge_enabled,
                       s.embedding_enabled, s.embedding_model_id,
                       s.confirm_tools, s.connected, s.enabled,
                       COALESCE(m.model, s.model) AS model,
                       gm.model AS generation_model
                FROM builtin_agent_settings s
                LEFT JOIN models m ON m.id = s.model_id
                LEFT JOIN models gm ON gm.id = s.generation_model_id
                WHERE s.agent_key = ?
                """,
                (key,),
            ).fetchone()
        capability = capabilities[key]
        default_prompt = builtin_agents.system_prompt(key)
        knowledge_state = get_knowledge_service_state() if key == "knowledge" else None
        protocol_missing: list[str] = []
        advertised_names: set[str] = set()
        exposed_tools = capability["tools"]
        if key == "knowledge" and knowledge_state is not None:
            protocol_missing = knowledge_protocol.missing_tools(
                tool["name"] for tool in knowledge_state["tools"]
            )
            advertised_names = {
                str(tool.get("name") or "") for tool in knowledge_state["tools"]
            }
            exposed_tools = (
                [
                    tool for tool in capability["tools"]
                    if tool["name"] in advertised_names
                ]
                if knowledge_state["status"] == "connected"
                else []
            )
        result.append(
            {
                **definition,
                "system_prompt": default_prompt,
                "model_id": setting["model_id"] if setting else None,
                "model": (
                    setting["model"] if setting else definition["default_model"]
                ),
                "generation_model_id": (
                    setting["generation_model_id"] if setting else None
                ) if key == "media" else None,
                "generation_model": (
                    setting["generation_model"] if setting else None
                ) if key == "media" else None,
                "generation_model_options": options if key == "media" else [],
                "knowledge_service_status": (
                    knowledge_state["status"] if knowledge_state else None
                ),
                "knowledge_service_last_tested_at": (
                    knowledge_state["last_tested_at"] if knowledge_state else None
                ),
                "knowledge_service_last_error": (
                    knowledge_state["last_error"] if knowledge_state else ""
                ),
                "knowledge_protocol": (
                    f"{knowledge_protocol.PROTOCOL_NAME} v{knowledge_protocol.PROTOCOL_VERSION}"
                    if key == "knowledge" else None
                ),
                "knowledge_protocol_compatible": (
                    knowledge_state is not None
                    and knowledge_state["status"] == "connected"
                    and not protocol_missing
                ) if key == "knowledge" else None,
                "knowledge_protocol_missing_tools": (
                    protocol_missing if key == "knowledge" else []
                ),
                "automatic_knowledge_enabled": (
                    bool(setting["automatic_knowledge_enabled"])
                    if setting else True
                ) if key == "knowledge" else None,
                "automatic_knowledge_available": (
                    knowledge_state is not None
                    and knowledge_state["status"] == "connected"
                    and knowledge_protocol.AUTOMATIC_CONTEXT_TOOL in advertised_names
                ) if key == "knowledge" else None,
                "embedding_enabled": (
                    bool(setting["embedding_enabled"]) if setting else False
                ) if key == "knowledge" else None,
                "embedding_model_id": (
                    setting["embedding_model_id"] if setting else None
                ) if key == "knowledge" else None,
                "embedding_model_options": (
                    [
                        {
                            "id": embedding["id"],
                            "label": f"{embedding['name']} — {embedding['model']}",
                            "status": embedding["connection_status"],
                            "dimensions": embedding["dimensions"],
                        }
                        for embedding in list_embedding_models()
                    ]
                    if key == "knowledge" else []
                ),
                "enabled": bool(setting["enabled"]) if setting else True,
                "connected": bool(setting["connected"]) if setting else True,
                "model_options": options,
                "confirm_tools": capability["confirm_tools"],
                "tools": exposed_tools,
                "skill_ids": [
                    int(skill["id"])
                    for skill in list_agent_skills("builtin", key)
                ],
            }
        )
    return [agent for agent in result if agent["connected"]] if connected_only else result


def update_builtin_agent(
    agent_key: str,
    *,
    model_id: int | None | object = _UNSET,
    generation_model_id: int | None | object = _UNSET,
    automatic_knowledge_enabled: bool | None = None,
    embedding_enabled: bool | None = None,
    embedding_model_id: int | None | object = _UNSET,
    confirm_tools: list[str] | str | object = _UNSET,
    connected: bool | None = None,
    enabled: bool | None = None,
    skill_ids: list[int] | None | object = _UNSET,
) -> dict:
    definition = builtin_agents.definition(agent_key)
    if definition is None:
        raise ValueError("built-in specialist was not found")
    if (
        model_id is _UNSET
        and generation_model_id is _UNSET
        and embedding_model_id is _UNSET
        and confirm_tools is _UNSET
        and skill_ids is _UNSET
        and automatic_knowledge_enabled is None
        and embedding_enabled is None
        and connected is None
        and enabled is None
    ):
        raise ValueError("provide a configuration change")
    model_requested = model_id is not _UNSET
    selected = None
    requested_id = None
    if model_requested and model_id is not None:
        try:
            requested_id = int(model_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("choose a model") from exc
        selected = get_model(requested_id)
        if selected is None:
            raise ValueError("choose a configured model")
    generation_requested = generation_model_id is not _UNSET
    requested_generation_id = None
    if generation_requested:
        if definition["key"] != "media":
            raise ValueError("only Files and Media supports a generation model")
        if generation_model_id is not None:
            try:
                requested_generation_id = int(generation_model_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("choose a generation model") from exc
            if get_model(requested_generation_id) is None:
                raise ValueError("choose a configured generation model")
    embedding_requested = embedding_model_id is not _UNSET
    requested_embedding_id = None
    if embedding_requested:
        if definition["key"] != "knowledge":
            raise ValueError("only Knowledge supports an embedding model")
        if embedding_model_id is not None:
            try:
                requested_embedding_id = int(embedding_model_id)
            except (TypeError, ValueError) as exc:
                raise ValueError("choose an embedding model") from exc
            selected_embedding = get_embedding_model(requested_embedding_id)
            if selected_embedding is None:
                raise ValueError("choose a configured embedding model")
    normalized_embedding_enabled = (
        int(_bool(embedding_enabled, "embedding enabled"))
        if embedding_enabled is not None else None
    )
    if normalized_embedding_enabled is not None and definition["key"] != "knowledge":
        raise ValueError("only Knowledge supports embeddings")
    normalized_automatic_knowledge = (
        int(_bool(automatic_knowledge_enabled, "automatic knowledge"))
        if automatic_knowledge_enabled is not None else None
    )
    if (
        normalized_automatic_knowledge is not None
        and definition["key"] != "knowledge"
    ):
        raise ValueError("only Knowledge supports automatic knowledge")
    confirmation_requested = confirm_tools is not _UNSET
    normalized_confirm_tools = None
    if confirmation_requested:
        normalized_confirm_tools = _json_string_list(
            confirm_tools, "confirmation tools"
        )
        rules = set(json.loads(normalized_confirm_tools))
        known_tools = {
            tool["name"]
            for capability in builtin_agents.capabilities()
            if capability["builtin_key"] == definition["key"]
            for tool in capability["tools"]
        }
        unknown = rules - {"*"} - known_tools
        if unknown:
            raise ValueError(
                "one or more confirmation tools are unavailable for this built-in subagent"
            )
    if embedding_requested or normalized_embedding_enabled is not None:
        with _connect() as conn:
            current_embedding = conn.execute(
                """
                SELECT embedding_enabled, embedding_model_id
                FROM builtin_agent_settings WHERE agent_key = 'knowledge'
                """
            ).fetchone()
        effective_enabled = (
            bool(normalized_embedding_enabled)
            if normalized_embedding_enabled is not None
            else bool(current_embedding and current_embedding["embedding_enabled"])
        )
        effective_embedding_id = (
            requested_embedding_id
            if embedding_requested
            else current_embedding["embedding_model_id"] if current_embedding else None
        )
    else:
        effective_enabled = False
        effective_embedding_id = None
    if effective_enabled:
        if effective_embedding_id is None:
            raise ValueError("choose an embedding model before enabling embeddings")
        effective_embedding = get_embedding_model(int(effective_embedding_id))
        if (
            effective_embedding is None
            or effective_embedding["connection_status"] != "connected"
            or not effective_embedding["dimensions"]
        ):
            raise ValueError("test the embedding model before enabling it")
    normalized_enabled = (
        int(_bool(enabled, "enabled")) if enabled is not None else None
    )
    normalized_connected = (
        int(_bool(connected, "connected")) if connected is not None else None
    )
    with _connect() as conn:
        if skill_ids is not _UNSET:
            _replace_agent_skill_assignments(
                conn, "builtin", definition["key"], skill_ids
            )
        if model_requested:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET model = ?, model_id = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (
                    selected["model"] if selected else definition["default_model"],
                    requested_id,
                    _now(),
                    definition["key"],
                ),
            )
        if generation_requested:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET generation_model_id = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (requested_generation_id, _now(), definition["key"]),
            )
        if embedding_requested:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET embedding_model_id = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (requested_embedding_id, _now(), definition["key"]),
            )
        if normalized_automatic_knowledge is not None:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET automatic_knowledge_enabled = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (normalized_automatic_knowledge, _now(), definition["key"]),
            )
        if normalized_embedding_enabled is not None:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET embedding_enabled = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (normalized_embedding_enabled, _now(), definition["key"]),
            )
        if confirmation_requested:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET confirm_tools = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (normalized_confirm_tools, _now(), definition["key"]),
            )
        if normalized_connected is not None:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET connected = ?, updated_at = ?
                WHERE agent_key = ?
                """,
                (normalized_connected, _now(), definition["key"]),
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
            "SELECT enabled, connected FROM builtin_agent_settings WHERE agent_key = ?",
            (key,),
        ).fetchone()
    return bool(row and row["enabled"] and row["connected"])


def is_automatic_knowledge_enabled() -> bool:
    """Return the effective per-turn Knowledge context setting."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT automatic_knowledge_enabled, enabled, connected
            FROM builtin_agent_settings
            WHERE agent_key = 'knowledge'
            """
        ).fetchone()
    return bool(
        row
        and row["automatic_knowledge_enabled"]
        and row["enabled"]
        and row["connected"]
    )


def is_automatic_knowledge_available() -> bool:
    """Return whether Knowledge's service advertises per-turn context."""
    state = get_knowledge_service_state()
    return bool(
        state["status"] == "connected"
        and any(
            tool.get("name") == knowledge_protocol.AUTOMATIC_CONTEXT_TOOL
            for tool in state["tools"]
        )
    )


def enabled_builtin_agent_keys() -> set[str]:
    with _connect() as conn:
        return {
            str(row["agent_key"])
            for row in conn.execute(
                """SELECT agent_key FROM builtin_agent_settings
                   WHERE enabled = 1 AND connected = 1"""
            )
        }


# -----------------------------------------------------------------------------
# Heartbeat configuration
# -----------------------------------------------------------------------------

def _heartbeat_mcp_tools(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    """Return definition-granted cached tools with stable cross-server keys."""
    grouped: dict[int, list[dict]] = {}
    rows = conn.execute(
        """
        SELECT s.id AS subagent_id, source.mcp_server_id,
               source.enabled_tools, server.name AS server_name,
               server.connection_status, tool.name, tool.description,
               tool.position, source.position AS source_position
        FROM subagents s
        JOIN subagent_mcp_sources source ON source.subagent_id = s.id
        JOIN mcp_servers server ON server.id = source.mcp_server_id
        JOIN mcp_server_tools tool
          ON tool.mcp_server_id = source.mcp_server_id
        WHERE s.enabled = 1
        ORDER BY s.name, source.position, tool.position, tool.id
        """
    ).fetchall()
    for row in rows:
        allowlist = None
        if row["enabled_tools"] is not None:
            try:
                allowlist = set(json.loads(row["enabled_tools"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                allowlist = set()
        raw_name = str(row["name"])
        if allowlist is not None and raw_name not in allowlist:
            continue
        server_id = int(row["mcp_server_id"])
        grouped.setdefault(int(row["subagent_id"]), []).append(
            {
                "name": f"{server_id}:{raw_name}",
                "tool_name": raw_name,
                "label": raw_name,
                "server_id": server_id,
                "server_name": row["server_name"],
                "connection_status": row["connection_status"] or "untested",
                "description": row["description"] or "",
            }
        )
    for tools in grouped.values():
        counts: dict[str, int] = {}
        for tool in tools:
            counts[tool["tool_name"]] = counts.get(tool["tool_name"], 0) + 1
        for tool in tools:
            if counts[tool["tool_name"]] == 1:
                tool["name"] = tool["tool_name"]
    return grouped


def _heartbeat_rule_matches(rules: set[str], tool: dict) -> bool:
    return (
        "*" in rules
        or tool["name"] in rules
        or tool["tool_name"] in rules
        or f"{tool['server_id']}:{tool['tool_name']}" in rules
    )


def _canonical_heartbeat_tool(agent: dict, tool_name: str) -> str | None:
    if tool_name in agent["tools"]:
        return tool_name
    matches = agent.get("aliases", {}).get(tool_name, [])
    return matches[0] if len(matches) == 1 else None


def _heartbeat_agent_catalog(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return active agents with valid tools and code-enforced protection rules."""
    catalog: dict[str, dict] = {}
    mcp_tools = _heartbeat_mcp_tools(conn)
    rows = conn.execute(
        """
        SELECT id, name, confirm_tools
        FROM subagents
        WHERE enabled = 1
        ORDER BY name
        """
    ).fetchall()
    for row in rows:
        key = f"mcp:{int(row['id'])}"
        try:
            rules = set(json.loads(row["confirm_tools"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            rules = {"*"}
        tool_items = mcp_tools.get(int(row["id"]), [])
        tools = {item["name"] for item in tool_items}
        aliases: dict[str, list[str]] = {}
        for item in tool_items:
            aliases.setdefault(item["tool_name"], []).append(item["name"])
        catalog[key] = {
            "name": row["name"],
            "tools": tools,
            "aliases": aliases,
            "protected": {
                item["name"] for item in tool_items
                if _heartbeat_rule_matches(rules, item)
            },
        }

    enabled_builtins = enabled_builtin_agent_keys()
    for agent in _builtin_capabilities_with_confirmation():
        if agent["builtin_key"] not in enabled_builtins:
            continue
        catalog[agent["key"]] = {
            "name": agent["name"],
            "tools": {tool["name"] for tool in agent["tools"]},
            "protected": {
                tool["name"]
                for tool in agent["tools"]
                if tool["requires_confirmation"]
            },
        }
    return catalog


def _normalize_heartbeat_selection(
    conn: sqlite3.Connection,
    selected_agents: list[str] | None,
    selected_tools: list[dict] | None,
) -> tuple[list[str], list[tuple[str, str]]]:
    if not isinstance(selected_agents, list):
        raise ValueError("selected_agents must be a list")
    if not isinstance(selected_tools, list):
        raise ValueError("selected_tools must be a list")

    agents = list(dict.fromkeys(str(key or "").strip() for key in selected_agents))
    if any(not key for key in agents):
        raise ValueError("selected_agents contains an invalid agent")

    tools: list[tuple[str, str]] = []
    seen_tools: set[tuple[str, str]] = set()
    for entry in selected_tools:
        if not isinstance(entry, dict):
            raise ValueError("each heartbeat tool selection must be an object")
        agent_key = str(entry.get("agent_key") or "").strip()
        tool_name = str(entry.get("tool_name") or "").strip()
        if not agent_key or not tool_name:
            raise ValueError("heartbeat tool selections require an agent and tool")
        pair = (agent_key, tool_name)
        if pair not in seen_tools:
            seen_tools.add(pair)
            tools.append(pair)

    catalog = _heartbeat_agent_catalog(conn)
    unknown_agents = [key for key in agents if key not in catalog]
    if unknown_agents:
        raise ValueError("one or more selected heartbeat agents are unavailable")
    if any(agent_key not in agents for agent_key, _ in tools):
        raise ValueError("heartbeat tools must belong to a selected agent")

    canonical_tools: list[tuple[str, str]] = []
    for agent_key, tool_name in tools:
        agent = catalog.get(agent_key)
        canonical = (
            _canonical_heartbeat_tool(agent, tool_name)
            if agent is not None else None
        )
        if canonical is None:
            raise ValueError("one or more selected heartbeat tools are unavailable")
        protected = agent["protected"]
        if "*" in protected or canonical in protected:
            raise ValueError(
                "tools that require confirmation cannot run in a heartbeat task"
            )
        canonical_tools.append((agent_key, canonical))
    return agents, canonical_tools


def _heartbeat_task_dict(
    row: sqlite3.Row, agents: list[str], tools: list[tuple[str, str]]
) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "interval_minutes": int(row["interval_minutes"] or 30),
        "execution_limit": int(row["execution_limit"]),
        "remaining_runs": int(row["remaining_runs"]),
        "instructions": row["instructions"],
        "next_run_at": row["next_run_at"],
        "last_run_at": row["last_run_at"],
        "last_status": row["last_status"] or "never",
        "last_message": row["last_message"] or "",
        "last_error": row["last_error"] or "",
        "notify_telegram": bool(row["notify_telegram"]),
        "notify_whatsapp": bool(row["notify_whatsapp"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "selected_agents": agents,
        "selected_tools": [
            {"agent_key": agent_key, "tool_name": tool_name}
            for agent_key, tool_name in tools
        ],
    }


def list_heartbeat_tasks() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM heartbeat_tasks ORDER BY created_at, id"
        ).fetchall()
        agents: dict[int, list[str]] = {}
        for row in conn.execute(
            "SELECT task_id, agent_key FROM heartbeat_task_agents ORDER BY rowid"
        ):
            agents.setdefault(int(row["task_id"]), []).append(row["agent_key"])
        tools: dict[int, list[tuple[str, str]]] = {}
        for row in conn.execute(
            """
            SELECT task_id, agent_key, tool_name FROM heartbeat_task_tools
            ORDER BY rowid
            """
        ):
            tools.setdefault(int(row["task_id"]), []).append(
                (row["agent_key"], row["tool_name"])
            )
    return [
        _heartbeat_task_dict(
            row, agents.get(int(row["id"]), []), tools.get(int(row["id"]), [])
        )
        for row in rows
    ]


def get_heartbeat_task(task_id: int) -> dict | None:
    task_id = int(task_id)
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM heartbeat_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        agents = [
            item["agent_key"]
            for item in conn.execute(
                """
                SELECT agent_key FROM heartbeat_task_agents
                WHERE task_id = ? ORDER BY rowid
                """,
                (task_id,),
            )
        ]
        tools = [
            (item["agent_key"], item["tool_name"])
            for item in conn.execute(
                """
                SELECT agent_key, tool_name FROM heartbeat_task_tools
                WHERE task_id = ? ORDER BY rowid
                """,
                (task_id,),
            )
        ]
    return _heartbeat_task_dict(row, agents, tools)


def create_heartbeat_task(
    *,
    name: str,
    instructions: str,
    enabled: bool = False,
    interval_minutes: int = 30,
    execution_limit: int = -1,
    selected_agents: list[str] | None = None,
    selected_tools: list[dict] | None = None,
    notify_telegram: bool = True,
    notify_whatsapp: bool = False,
) -> dict:
    return _save_heartbeat_task(
        None,
        name=name,
        instructions=instructions,
        enabled=enabled,
        interval_minutes=interval_minutes,
        execution_limit=execution_limit,
        selected_agents=selected_agents or [],
        selected_tools=selected_tools or [],
        notify_telegram=notify_telegram,
        notify_whatsapp=notify_whatsapp,
    )


def update_heartbeat_task(task_id: int, **fields) -> dict | None:
    return _save_heartbeat_task(int(task_id), **fields)


def _save_heartbeat_task(task_id: int | None, **changes) -> dict | None:
    with _connect() as conn:
        current = (
            conn.execute(
                "SELECT * FROM heartbeat_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if task_id is not None
            else None
        )
        if task_id is not None and current is None:
            return None

        def value(name: str, default):
            if name in changes:
                return changes[name]
            return current[name] if current is not None else default

        name = str(value("name", "") or "").strip()
        instructions = str(value("instructions", "") or "").strip()
        enabled = (
            value("enabled", False)
            if "enabled" in changes or current is None
            else bool(current["enabled"])
        )
        interval = value("interval_minutes", 30)
        execution_limit = value("execution_limit", -1)
        previous_execution_limit = (
            int(current["execution_limit"]) if current is not None else None
        )
        remaining_runs = (
            int(current["remaining_runs"])
            if current is not None and execution_limit == previous_execution_limit
            else execution_limit
        )
        notify_telegram = (
            value("notify_telegram", True)
            if "notify_telegram" in changes or current is None
            else bool(current["notify_telegram"])
        )
        notify_whatsapp = (
            value("notify_whatsapp", False)
            if "notify_whatsapp" in changes or current is None
            else bool(current["notify_whatsapp"])
        )

        if not name:
            raise ValueError("heartbeat task name is required")
        if len(name) > 120:
            raise ValueError("heartbeat task name must be 120 characters or fewer")
        if not instructions:
            raise ValueError("heartbeat task prompt is required")
        if len(instructions) > 4000:
            raise ValueError("heartbeat task prompt must be 4000 characters or fewer")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        if isinstance(interval, bool) or not isinstance(interval, int):
            raise ValueError("interval must be a whole number of minutes")
        if not 5 <= interval <= 1440:
            raise ValueError("interval must be between 5 and 1440 minutes")
        if isinstance(execution_limit, bool) or not isinstance(execution_limit, int):
            raise ValueError("execution count must be a whole number")
        if execution_limit != -1 and not 1 <= execution_limit <= 10000:
            raise ValueError(
                "execution count must be always run or between 1 and 10000"
            )
        if not isinstance(notify_telegram, bool) or not isinstance(
            notify_whatsapp, bool
        ):
            raise ValueError("notification settings must be true or false")

        if current is None:
            existing_agents: list[str] = []
            existing_tools: list[dict] = []
        else:
            existing_agents = [
                row["agent_key"]
                for row in conn.execute(
                    "SELECT agent_key FROM heartbeat_task_agents WHERE task_id = ?",
                    (task_id,),
                )
            ]
            existing_tools = [
                {"agent_key": row["agent_key"], "tool_name": row["tool_name"]}
                for row in conn.execute(
                    """
                    SELECT agent_key, tool_name FROM heartbeat_task_tools
                    WHERE task_id = ?
                    """,
                    (task_id,),
                )
            ]
        agents, tools = _normalize_heartbeat_selection(
            conn,
            changes.get("selected_agents", existing_agents),
            changes.get("selected_tools", existing_tools),
        )
        if enabled:
            if remaining_runs == 0:
                raise ValueError(
                    "increase the execution count before enabling this completed task"
                )
            if not agents:
                raise ValueError("select at least one agent before enabling this task")
            missing = [
                agent_key
                for agent_key in agents
                if not any(tool_agent == agent_key for tool_agent, _ in tools)
            ]
            if missing:
                raise ValueError(
                    "each enabled heartbeat agent needs at least one approved tool"
                )

        now = _now()
        next_run = (
            datetime.now(timezone.utc) + timedelta(minutes=interval)
        ).isoformat() if enabled else None
        if current is None:
            cur = conn.execute(
                """
                INSERT INTO heartbeat_tasks (
                    name, enabled, interval_minutes, execution_limit,
                    remaining_runs, instructions, next_run_at,
                    notify_telegram, notify_whatsapp, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    int(enabled),
                    interval,
                    execution_limit,
                    remaining_runs,
                    instructions,
                    next_run,
                    int(notify_telegram),
                    int(notify_whatsapp),
                    now,
                    now,
                ),
            )
            task_id = int(cur.lastrowid)
        else:
            conn.execute(
                """
                UPDATE heartbeat_tasks
                SET name = ?, enabled = ?, interval_minutes = ?, execution_limit = ?,
                    remaining_runs = ?, instructions = ?, next_run_at = ?,
                    notify_telegram = ?, notify_whatsapp = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    int(enabled),
                    interval,
                    execution_limit,
                    remaining_runs,
                    instructions,
                    next_run,
                    int(notify_telegram),
                    int(notify_whatsapp),
                    now,
                    task_id,
                ),
            )
            conn.execute("DELETE FROM heartbeat_task_agents WHERE task_id = ?", (task_id,))
        conn.executemany(
            """
            INSERT INTO heartbeat_task_agents (task_id, agent_key, created_at)
            VALUES (?, ?, ?)
            """,
            [(task_id, agent_key, now) for agent_key in agents],
        )
        conn.executemany(
            """
            INSERT INTO heartbeat_task_tools
                (task_id, agent_key, tool_name, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [(task_id, agent_key, tool_name, now) for agent_key, tool_name in tools],
        )
        conn.commit()
    return get_heartbeat_task(int(task_id))


def delete_heartbeat_task(task_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM heartbeat_tasks WHERE id = ?", (int(task_id),))
        conn.commit()
        return cur.rowcount > 0


def get_heartbeat_capabilities() -> list[dict]:
    """Return built-in and cached MCP tools grouped for heartbeat configuration."""
    with _connect() as conn:
        agents = conn.execute(
            """
            SELECT s.id, s.name, s.description, s.confirm_tools
            FROM subagents s
            WHERE s.enabled = 1
            ORDER BY s.name
            """
        ).fetchall()
        grouped = _heartbeat_mcp_tools(conn)
    result = []
    for row in agents:
        try:
            protected = set(json.loads(row["confirm_tools"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            protected = {"*"}
        tools = grouped.get(int(row["id"]), [])
        agent_key = f"mcp:{int(row['id'])}"
        for tool in tools:
            tool["requires_confirmation"] = _heartbeat_rule_matches(protected, tool)
        statuses = {tool["connection_status"] for tool in tools}
        result.append(
            {
                "id": int(row["id"]),
                "key": agent_key,
                "kind": "mcp",
                "name": row["name"],
                "description": row["description"] or (
                    "Prompt-only subagent" if not tools else "MCP subagent"
                ),
                "connection_status": (
                    "connected" if statuses == {"connected"}
                    else "failed" if "failed" in statuses
                    else "untested"
                ),
                "tools": tools,
            }
        )
    active_builtin_keys = enabled_builtin_agent_keys()
    builtins = [
        agent for agent in _builtin_capabilities_with_confirmation()
        if agent["builtin_key"] in active_builtin_keys
    ]
    return [*builtins, *result]


def get_heartbeat_targets(task_id: int) -> list[dict]:
    """Return resolved built-in and MCP specs with selected safe tools only."""
    selected: dict[int, list[str]] = {}
    selected_builtins: dict[str, list[str]] = {}
    resolved_dynamic: dict[int, dict[str, tuple[int, str]]] = {}
    with _connect() as conn:
        dynamic_rows = conn.execute(
            """
            SELECT CAST(substr(htt.agent_key, 5) AS INTEGER) AS subagent_id,
                   htt.tool_name
            FROM heartbeat_task_tools htt
            JOIN subagents s
              ON s.id = CAST(substr(htt.agent_key, 5) AS INTEGER)
            WHERE htt.task_id = ? AND htt.agent_key LIKE 'mcp:%'
              AND s.enabled = 1
            ORDER BY htt.agent_key, htt.tool_name
            """,
            (int(task_id),),
        )
        builtin_rows = conn.execute(
            """
            SELECT agent_key, tool_name
            FROM heartbeat_task_tools
            WHERE task_id = ? AND agent_key LIKE 'builtin:%'
            ORDER BY agent_key, tool_name
            """,
            (int(task_id),),
        )
        for row in dynamic_rows:
            selected.setdefault(int(row["subagent_id"]), []).append(row["tool_name"])
        for row in builtin_rows:
            selected_builtins.setdefault(row["agent_key"], []).append(row["tool_name"])
        for agent_id, tools in _heartbeat_mcp_tools(conn).items():
            lookup = resolved_dynamic.setdefault(agent_id, {})
            for tool in tools:
                resolved = (int(tool["server_id"]), tool["tool_name"])
                lookup[tool["name"]] = resolved
                lookup[f"{tool['server_id']}:{tool['tool_name']}"] = resolved
    targets = []
    active_builtin_keys = enabled_builtin_agent_keys()
    for agent in _builtin_capabilities_with_confirmation():
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
        safe_sources = []
        safe_keys: list[str] = []
        for source in spec.get("mcp_sources") or []:
            server_id = int(source["mcp_server_id"])
            source_allowlist = source.get("allowed_tools")
            source_allowed = (
                None if source_allowlist is None else set(source_allowlist)
            )
            safe_names = []
            for selected_name in chosen:
                resolved = resolved_dynamic.get(int(spec["id"]), {}).get(
                    str(selected_name)
                )
                if resolved is None or resolved[0] != server_id:
                    continue
                raw_name = resolved[1]
                canonical = f"{server_id}:{raw_name}"
                if source_allowed is not None and raw_name not in source_allowed:
                    continue
                if (
                    "*" in protected
                    or raw_name in protected
                    or canonical in protected
                ):
                    continue
                safe_names.append(raw_name)
                safe_keys.append(str(selected_name))
            if safe_names:
                safe_sources.append({**source, "allowed_tools": safe_names})
        if safe_sources:
            target = dict(spec)
            target["kind"] = "mcp"
            target["mcp_sources"] = safe_sources
            target["allowed_tools"] = safe_keys
            targets.append(target)
    return targets


def get_heartbeat_task_agent_report(task_id: int, agent_key: str) -> str:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT last_report FROM heartbeat_task_agent_state
            WHERE task_id = ? AND agent_key = ?
            """,
            (int(task_id), str(agent_key)),
        ).fetchone()
    return (row["last_report"] or "") if row else ""


def set_heartbeat_task_agent_report(
    task_id: int, agent_key: str, report: str
) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO heartbeat_task_agent_state
                (task_id, agent_key, last_report, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(task_id, agent_key) DO UPDATE SET
                last_report = excluded.last_report,
                updated_at = excluded.updated_at
            """,
            (
                int(task_id),
                str(agent_key),
                str(report or "").strip()[:8000],
                _now(),
            ),
        )
        conn.commit()


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
            UPDATE heartbeat_tasks
            SET last_status = 'error', last_error = ?
            WHERE id IN (
                SELECT heartbeat_task_id FROM heartbeat_runs
                WHERE status = 'running' AND heartbeat_task_id IS NOT NULL
            )
            """,
            (error,),
        )
        conn.execute(
            """
            UPDATE heartbeat_runs
            SET finished_at = ?, status = 'error', error = ?
            WHERE status = 'running'
            """,
            (finished, error),
        )
        conn.commit()


def begin_heartbeat_task_run(task_id: int, trigger: str) -> int:
    task = get_heartbeat_task(task_id)
    if task is None:
        raise ValueError("heartbeat task not found")
    if task["remaining_runs"] == 0:
        raise RuntimeError("This heartbeat task has no remaining executions.")
    started = _now()
    next_run = (
        datetime.now(timezone.utc)
        + timedelta(minutes=int(task["interval_minutes"]))
    ).isoformat()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO heartbeat_runs (
                heartbeat_task_id, heartbeat_task_name, trigger, started_at, status
            ) VALUES (?, ?, ?, ?, 'running')
            """,
            (int(task_id), task["name"], trigger, started),
        )
        conn.execute(
            """
            UPDATE heartbeat_tasks
            SET last_run_at = ?, last_status = 'running', last_message = '',
                last_error = '', next_run_at = ?
            WHERE id = ?
            """,
            (started, next_run, int(task_id)),
        )
        conn.commit()
        return int(cur.lastrowid)


def finish_heartbeat_task_run(
    task_id: int,
    run_id: int,
    *,
    status: str,
    message: str = "",
    error: str = "",
) -> dict:
    if status not in {"quiet", "alert", "error", "skipped"}:
        raise ValueError("invalid heartbeat run status")
    finished = _now()
    message = str(message or "").strip()[:8000]
    error = " ".join(str(error or "").split())[:2000]
    with _connect() as conn:
        finished_run = conn.execute(
            """
            UPDATE heartbeat_runs
            SET finished_at = ?, status = ?, message = ?, error = ?
            WHERE id = ? AND heartbeat_task_id = ? AND status = 'running'
            """,
            (finished, status, message, error, int(run_id), int(task_id)),
        ).rowcount
        if finished_run:
            conn.execute(
                """
                UPDATE heartbeat_tasks
                SET last_status = ?, last_message = ?, last_error = ?,
                    remaining_runs = CASE
                        WHEN remaining_runs > 0 THEN remaining_runs - 1
                        ELSE remaining_runs
                    END,
                    enabled = CASE WHEN remaining_runs = 1 THEN 0 ELSE enabled END,
                    next_run_at = CASE
                        WHEN remaining_runs = 1 THEN NULL ELSE next_run_at
                    END
                WHERE id = ?
                """,
                (status, message, error, int(task_id)),
            )
        conn.execute(
            """
            DELETE FROM heartbeat_runs
            WHERE heartbeat_task_id = ? AND id NOT IN (
                SELECT id FROM heartbeat_runs
                WHERE heartbeat_task_id = ?
                ORDER BY id DESC LIMIT 100
            )
            """,
            (int(task_id), int(task_id)),
        )
        conn.commit()
    task = get_heartbeat_task(task_id)
    if task is None:
        raise ValueError("heartbeat task not found")
    return task


def list_heartbeat_task_runs(task_id: int, limit: int = 10) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM heartbeat_runs
            WHERE heartbeat_task_id = ?
            ORDER BY id DESC LIMIT ?
            """,
            (int(task_id), limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_heartbeat_notifications(
    limit: int = 25, *, unread_only: bool = False
) -> list[dict]:
    """Return persisted heartbeat alerts, optionally limited to unread items."""
    limit = max(1, min(int(limit), 100))
    unread_clause = "AND notification_read_at IS NULL" if unread_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, heartbeat_task_id, heartbeat_task_name, trigger,
                   started_at AS created_at, finished_at, message,
                   notification_read_at AS read_at
            FROM heartbeat_runs
            WHERE status = 'alert' AND TRIM(message) != ''
              {unread_clause}
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def mark_heartbeat_notification_read(notification_id: int) -> bool:
    """Archive one persisted alert by marking it read."""
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE heartbeat_runs
            SET notification_read_at = COALESCE(notification_read_at, ?)
            WHERE id = ? AND status = 'alert' AND TRIM(message) != ''
            """,
            (_now(), int(notification_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_heartbeat_notification(notification_id: int) -> bool:
    """Permanently remove one persisted heartbeat alert."""
    with _connect() as conn:
        cur = conn.execute(
            """
            DELETE FROM heartbeat_runs
            WHERE id = ? AND status = 'alert' AND TRIM(message) != ''
            """,
            (int(notification_id),),
        )
        conn.commit()
        return cur.rowcount > 0


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
    location: str = "",
) -> int:
    now = _now()
    try:
        cur = conn.execute(
            """
            INSERT INTO models
                (name, model_type, location, model, provider, base_url, api_key,
                 created_at, updated_at)
            VALUES (?, 'text', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _required(name, "name"),
                _normalize_model_location(location, provider, base_url),
                _required(model, "model ID"),
                (provider or "").strip(),
                _normalize_model_base_url(base_url),
                api_key or "",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO text_model_details (model_id) VALUES (?)",
            (int(cur.lastrowid),),
        )
    except sqlite3.IntegrityError as exc:
        raise _friendly_integrity_error(exc) from exc
    conn.commit()
    return cur.lastrowid


def _get_model_by_name(conn: sqlite3.Connection, name: str) -> dict | None:
    cur = conn.execute(
        "SELECT * FROM models WHERE name = ? AND model_type = 'text'", (name.strip(),)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def add_model(
    name: str,
    model: str,
    provider: str,
    base_url: str,
    api_key: str,
    location: str = "",
) -> dict:
    with _connect() as conn:
        mid = _add_model(conn, name, model, provider, base_url, api_key, location)
        return get_model(mid)


def get_model(model_id: int) -> dict | None:
    with _connect() as conn:
        cur = conn.execute(
            "SELECT * FROM models WHERE id = ? AND model_type = 'text'", (model_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_model_runtime(model_id: int) -> dict | None:
    """Return one configured model with environment-backed secrets resolved."""
    model = get_model(model_id)
    if model is None:
        return None
    return {
        "model_id": int(model["id"]),
        "model": model["model"],
        "provider": model.get("provider") or "OpenAI compatible",
        "base_url": model["base_url"],
        "api_key": _resolve_key(model.get("api_key") or ""),
    }


def get_model_by_name(name: str) -> dict | None:
    with _connect() as conn:
        return _get_model_by_name(conn, name)


def list_models() -> list[dict]:
    with _connect() as conn:
        cur = conn.execute("SELECT * FROM models WHERE model_type = 'text' ORDER BY name")
        return [dict(r) for r in cur.fetchall()]


def list_model_catalog() -> list[dict]:
    """Return every model type from the canonical registry in one collection."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT m.*,
                   e.adapter, e.dimensions, e.connection_status,
                   e.last_tested_at, e.last_error,
                   s.voice AS speech_voice, s.language AS speech_language,
                   t.language AS transcription_language
            FROM models m
            LEFT JOIN embedding_model_details e ON e.model_id = m.id
            LEFT JOIN speech_model_details s ON s.model_id = m.id
            LEFT JOIN transcription_model_details t ON t.model_id = m.id
            ORDER BY m.name
            """
        ).fetchall()
    result = []
    for stored in rows:
        row = dict(stored)
        model_type = row["model_type"]
        item = {
            key: row[key]
            for key in (
                "id", "name", "model", "provider", "location", "base_url",
                "api_key", "created_at", "updated_at",
            )
        }
        if model_type == "embedding":
            item.update(
                adapter=row["adapter"],
                dimensions=row["dimensions"],
                connection_status=row["connection_status"],
                last_tested_at=row["last_tested_at"],
                last_error=row["last_error"] or "",
            )
        elif model_type in {"speech", "transcription"}:
            item.update(
                kind="tts" if model_type == "speech" else "stt",
                voice=row["speech_voice"] or "" if model_type == "speech" else "",
                language=(
                    row["speech_language"]
                    if model_type == "speech"
                    else row["transcription_language"]
                ) or "auto",
            )
        result.append(item)
    return result


def update_model(model_id: int, **kwargs) -> dict | None:
    allowed = {"name", "location", "model", "provider", "base_url", "api_key"}
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
        if current["model_type"] != "text":
            return None
        if "location" in fields:
            fields["location"] = _normalize_model_location(
                fields["location"],
                fields.get("provider", current["provider"]),
                fields.get("base_url", current["base_url"]),
            )
        elif "provider" in fields or "base_url" in fields:
            fields["location"] = _infer_model_location(
                fields.get("provider", current["provider"]),
                fields.get("base_url", current["base_url"]),
            )
        fields["updated_at"] = _now()
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
            "SELECT 1 FROM models WHERE id = ? AND model_type = 'text'", (model_id,)
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
        generation_rows = conn.execute(
            """
            SELECT agent_key FROM builtin_agent_settings
            WHERE generation_model_id = ? ORDER BY agent_key
            """,
            (model_id,),
        ).fetchall()
        for row in generation_rows:
            definition = builtin_agents.definition(row["agent_key"])
            dependencies.append(
                f"the {definition['name']} built-in agent's generation capability"
                if definition
                else f"the {row['agent_key']} built-in agent's generation capability"
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
# Embedding models
# -----------------------------------------------------------------------------

EMBEDDING_ADAPTERS = {"openai_compatible", "ollama"}
EMBEDDING_LOCATIONS = {"cloud", "local"}


def _normalize_embedding_connection(
    location,
    adapter,
    base_url,
) -> tuple[str, str, str]:
    normalized_location = str(location or "cloud").strip().lower()
    if normalized_location not in EMBEDDING_LOCATIONS:
        raise ValueError("location must be cloud or local.")
    normalized_adapter = str(adapter or "openai_compatible").strip().lower()
    if normalized_adapter not in EMBEDDING_ADAPTERS:
        raise ValueError("adapter must be OpenAI-compatible or Ollama.")
    normalized_url = _normalize_model_base_url(base_url)
    if not normalized_url:
        raise ValueError("base URL is required.")
    if normalized_url.endswith("/embeddings"):
        normalized_url = normalized_url.removesuffix("/embeddings")
    return normalized_location, normalized_adapter, normalized_url


def add_embedding_model(
    name: str,
    location: str,
    adapter: str,
    model: str,
    base_url: str,
    api_key: str = "",
) -> dict:
    normalized = _normalize_embedding_connection(location, adapter, base_url)
    now = _now()
    with _connect() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO models
                    (name, model_type, location, provider, model, base_url, api_key,
                     created_at, updated_at)
                VALUES (?, 'embedding', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required(name, "name"),
                    normalized[0],
                    normalized[1],
                    _required(model, "model ID"),
                    normalized[2],
                    api_key or "",
                    now,
                    now,
                ),
            )
            model_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT INTO embedding_model_details
                    (model_id, adapter, connection_status, last_error)
                VALUES (?, ?, 'untested', '')
                """,
                (model_id, normalized[1]),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
        return get_embedding_model(model_id)


def _embedding_model_query() -> str:
    return """
        SELECT m.id, m.name, m.location, e.adapter, m.model, m.base_url, m.api_key,
               e.dimensions, e.connection_status, e.last_tested_at, e.last_error,
               m.created_at, m.updated_at
        FROM models m
        JOIN embedding_model_details e ON e.model_id = m.id
    """


def get_embedding_model(model_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            _embedding_model_query() + " WHERE m.id = ? AND m.model_type = 'embedding'",
            (model_id,),
        ).fetchone()
    return dict(row) if row else None


def get_embedding_model_runtime(model_id: int) -> dict | None:
    model = get_embedding_model(model_id)
    if model is None:
        return None
    return {
        **model,
        "api_key": _resolve_key(model.get("api_key") or ""),
    }


def list_embedding_models() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            _embedding_model_query() + " WHERE m.model_type = 'embedding' ORDER BY m.name"
        ).fetchall()
    return [dict(row) for row in rows]


def update_embedding_model(model_id: int, **kwargs) -> dict | None:
    allowed = {"name", "location", "adapter", "model", "base_url", "api_key"}
    fields = {key: value for key, value in kwargs.items() if key in allowed and value is not None}
    with _connect() as conn:
        current = conn.execute(
            _embedding_model_query() + " WHERE m.id = ? AND m.model_type = 'embedding'",
            (model_id,),
        ).fetchone()
        if current is None:
            return None
        if not fields:
            return dict(current)
        if "name" in fields:
            fields["name"] = _required(fields["name"], "name")
        if "model" in fields:
            fields["model"] = _required(fields["model"], "model ID")
        location, adapter, base_url = _normalize_embedding_connection(
            fields.get("location", current["location"]),
            fields.get("adapter", current["adapter"]),
            fields.get("base_url", current["base_url"]),
        )
        fields.update(location=location, adapter=adapter, base_url=base_url)
        connection_changed = any(
            fields.get(key, current[key]) != current[key]
            for key in ("location", "adapter", "model", "base_url", "api_key")
        )
        if connection_changed:
            active = conn.execute(
                """
                SELECT 1 FROM builtin_agent_settings
                WHERE agent_key = 'knowledge' AND embedding_enabled = 1
                  AND embedding_model_id = ?
                """,
                (model_id,),
            ).fetchone()
            if active:
                raise ValueError(
                    "Disable Knowledge embeddings before changing this connection."
                )
            fields.update(
                dimensions=None,
                connection_status="stale",
                last_tested_at=None,
                last_error="",
            )
        now = _now()
        try:
            base_fields = {
                key: fields[key]
                for key in ("name", "location", "model", "base_url", "api_key")
                if key in fields
            }
            base_fields["provider"] = adapter
            base_fields["updated_at"] = now
            sets = ", ".join(f"{key} = ?" for key in base_fields)
            conn.execute(
                f"UPDATE models SET {sets} WHERE id = ?",
                (*base_fields.values(), model_id),
            )
            detail_fields = {"adapter": adapter}
            if connection_changed:
                detail_fields.update(
                    dimensions=None,
                    connection_status="stale",
                    last_tested_at=None,
                    last_error="",
                )
            detail_sets = ", ".join(f"{key} = ?" for key in detail_fields)
            conn.execute(
                f"UPDATE embedding_model_details SET {detail_sets} WHERE model_id = ?",
                (*detail_fields.values(), model_id),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
    return get_embedding_model(model_id)


def save_embedding_test(
    model_id: int,
    *,
    dimensions: int | None = None,
    error: str = "",
) -> dict | None:
    status = "connected" if dimensions else "failed"
    with _connect() as conn:
        conn.execute(
            """
            UPDATE embedding_model_details
            SET dimensions = COALESCE(?, dimensions), connection_status = ?, last_tested_at = ?,
                last_error = ?
            WHERE model_id = ?
            """,
            (dimensions, status, _now(), str(error or "")[:2000], model_id),
        )
        conn.execute("UPDATE models SET updated_at = ? WHERE id = ?", (_now(), model_id))
        conn.commit()
    return get_embedding_model(model_id)


def delete_embedding_model_result(model_id: int) -> DeletionResult:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM models WHERE id = ? AND model_type = 'embedding'", (model_id,)
        ).fetchone() is None:
            conn.rollback()
            return DeletionResult("not_found")
        knowledge = conn.execute(
            """
            SELECT 1 FROM builtin_agent_settings
            WHERE agent_key = 'knowledge' AND embedding_model_id = ?
              AND embedding_enabled = 1
            """,
            (model_id,),
        ).fetchone()
        if knowledge:
            conn.rollback()
            return DeletionResult("in_use", ("the Knowledge built-in agent",))
        try:
            conn.execute(
                """
                UPDATE builtin_agent_settings
                SET embedding_model_id = NULL, updated_at = ?
                WHERE agent_key = 'knowledge' AND embedding_model_id = ?
                """,
                (_now(), model_id),
            )
            conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
            conn.commit()
            return DeletionResult("deleted")
        except sqlite3.IntegrityError:
            conn.rollback()
            return DeletionResult("in_use", ("another saved configuration",))


def embedding_model_for_api(model: dict | None) -> dict | None:
    if model is None:
        return None
    result = dict(model)
    result["api_key_configured"] = bool(result.pop("api_key", ""))
    return result


# -----------------------------------------------------------------------------
# MCP servers
# -----------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _credential_dir(server_id: int) -> Path:
    return DB_PATH.parent / "mcp_credentials" / f"server-{int(server_id)}"


def _clear_materialized_server_files(server_id: int) -> None:
    directory = _credential_dir(server_id)
    if not directory.exists():
        return
    for path in directory.iterdir():
        if path.is_file():
            path.unlink(missing_ok=True)
    try:
        directory.rmdir()
        directory.parent.rmdir()
    except OSError:
        pass


def list_server_files(server_id: int) -> list[dict]:
    """Return only safe metadata for private files attached to one server."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT env_var, filename
            FROM mcp_server_files
            WHERE mcp_server_id = ?
            ORDER BY env_var
            """,
            (int(server_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_server_files(
    server_id: int,
    uploads: list[dict] | None = None,
    removals: list[str] | None = None,
) -> list[dict]:
    """Apply explicit file replacements/removals without exposing stored bytes."""
    if get_server(server_id) is None:
        raise ValueError("Server not found.")
    server_environment = json.loads(get_server(server_id).get("env") or "{}")
    normalized_uploads: list[tuple[str, str, bytes]] = []
    seen: set[str] = set()
    for upload in uploads or []:
        env_var = str((upload or {}).get("env_var") or "").strip()
        filename = Path(str((upload or {}).get("filename") or "credential")).name
        content = (upload or {}).get("content")
        if not _ENV_VAR_RE.fullmatch(env_var):
            raise ValueError(f"Invalid environment variable name: {env_var or 'empty'}.")
        if env_var in seen:
            raise ValueError(f"Credential file {env_var} is duplicated.")
        if env_var in server_environment:
            raise ValueError(f"Environment variable {env_var} is already configured.")
        if not isinstance(content, bytes) or not content:
            raise ValueError(f"Choose a credential file for {env_var}.")
        if len(content) > MCP_CREDENTIAL_FILE_LIMIT:
            raise ValueError("Each credential file must be 2 MB or smaller.")
        seen.add(env_var)
        normalized_uploads.append((env_var, filename[:255] or "credential", content))
    normalized_removals = {
        str(value).strip() for value in (removals or []) if str(value).strip()
    }
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for env_var in normalized_removals:
            conn.execute(
                "DELETE FROM mcp_server_files WHERE mcp_server_id = ? AND env_var = ?",
                (server_id, env_var),
            )
        for env_var, filename, content in normalized_uploads:
            conn.execute(
                """
                INSERT INTO mcp_server_files
                    (mcp_server_id, env_var, filename, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(mcp_server_id, env_var) DO UPDATE SET
                    filename = excluded.filename,
                    content = excluded.content,
                    created_at = excluded.created_at
                """,
                (server_id, env_var, filename, content, _now()),
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM mcp_server_files WHERE mcp_server_id = ?",
            (server_id,),
        ).fetchone()[0]
        if count > MCP_CREDENTIAL_FILE_COUNT:
            raise ValueError(
                f"A server can have at most {MCP_CREDENTIAL_FILE_COUNT} credential files."
            )
        if normalized_uploads or normalized_removals:
            conn.execute(
                """
                UPDATE mcp_servers
                SET connection_status = 'stale', last_error = ''
                WHERE id = ?
                """,
                (server_id,),
            )
        conn.commit()
    _clear_materialized_server_files(server_id)
    return list_server_files(server_id)


def _materialized_server_file_env(server_id: int) -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT env_var, filename, content
            FROM mcp_server_files
            WHERE mcp_server_id = ?
            ORDER BY env_var
            """,
            (server_id,),
        ).fetchall()
    if not rows:
        return {}
    directory = _credential_dir(server_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    result: dict[str, str] = {}
    for row in rows:
        suffix = Path(row["filename"]).suffix
        if len(suffix) > 16 or not re.fullmatch(r"\.[A-Za-z0-9]+", suffix or ""):
            suffix = ""
        digest = hashlib.sha256(row["env_var"].encode()).hexdigest()[:12]
        path = directory / f"credential-{digest}{suffix}"
        path.write_bytes(bytes(row["content"]))
        path.chmod(0o600)
        result[row["env_var"]] = str(path)
    return result


def get_server_oauth_state(server_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT oauth_tokens, oauth_token_expires_at, oauth_client_info,
                   oauth_redirect_uri
            FROM mcp_servers WHERE id = ?
            """,
            (server_id,),
        ).fetchone()
    return dict(row) if row else None


def save_server_oauth_state(
    server_id: int,
    *,
    tokens: str | None = None,
    token_expires_at: float | None = None,
    client_info: str | None = None,
) -> None:
    fields = {}
    if tokens is not None:
        fields["oauth_tokens"] = tokens
    if token_expires_at is not None:
        fields["oauth_token_expires_at"] = float(token_expires_at)
    if client_info is not None:
        fields["oauth_client_info"] = client_info
    if not fields:
        return
    with _connect() as conn:
        sets = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(
            f"UPDATE mcp_servers SET {sets} WHERE id = ?",
            (*fields.values(), server_id),
        )
        conn.commit()


def prepare_server_oauth(server_id: int, redirect_uri: str) -> None:
    current = get_server_oauth_state(server_id)
    if current is None:
        raise ValueError("Server not found.")
    changed = current.get("oauth_redirect_uri") not in {"", redirect_uri}
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mcp_servers
            SET oauth_redirect_uri = ?,
                oauth_tokens = CASE WHEN ? THEN '' ELSE oauth_tokens END,
                oauth_token_expires_at = CASE WHEN ? THEN 0 ELSE oauth_token_expires_at END,
                oauth_client_info = CASE WHEN ? THEN '' ELSE oauth_client_info END
            WHERE id = ?
            """,
            (redirect_uri, int(changed), int(changed), int(changed), server_id),
        )
        conn.commit()


def clear_server_oauth(server_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mcp_servers
            SET oauth_tokens = '', oauth_token_expires_at = 0,
                oauth_client_info = '',
                connection_status = 'stale', last_error = ''
            WHERE id = ?
            """,
            (server_id,),
        )
        conn.commit()


def clear_server_oauth_tokens(server_id: int) -> None:
    """Force a fresh authorization while retaining registered client metadata."""
    with _connect() as conn:
        conn.execute(
            """
            UPDATE mcp_servers
            SET oauth_tokens = '', oauth_token_expires_at = 0,
                connection_status = 'stale', last_error = ''
            WHERE id = ?
            """,
            (server_id,),
        )
        conn.commit()


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
    setup_command: str = "",
    source_type: str = "manual",
    source_name: str = "",
    source_ref: str = "",
    source_version: str = "",
    source_url: str = "",
) -> int:
    transport, connection = _validate_transport(transport, connection)
    auth_scheme = str(auth_scheme or "").strip()
    if auth_scheme not in {"", "none", "bearer", "header", "custom", "oauth"}:
        raise ValueError("authentication method is not supported")
    if transport == "stdio" and auth_scheme == "oauth":
        raise ValueError("OAuth is available only for remote MCP servers.")
    setup_command = str(setup_command or "").strip()
    if transport != "stdio" and setup_command:
        raise ValueError("Setup commands are available only for local MCP servers.")
    source_type = str(source_type or "manual").strip().lower()
    if source_type not in {"manual", "registry"}:
        raise ValueError("MCP server source type is not supported.")
    try:
        cur = conn.execute(
            """
            INSERT INTO mcp_servers
                (name, description, setup_type, transport, connection, headers, env,
                 auth_scheme, setup_command, source_type, source_name, source_ref,
                 source_version, source_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                setup_command,
                source_type,
                str(source_name or "").strip(),
                str(source_ref or "").strip(),
                str(source_version or "").strip(),
                str(source_url or "").strip(),
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
    setup_command: str = "",
    source_type: str = "manual",
    source_name: str = "",
    source_ref: str = "",
    source_version: str = "",
    source_url: str = "",
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
            setup_command=setup_command,
            source_type=source_type,
            source_name=source_name,
            source_ref=source_ref,
            source_version=source_version,
            source_url=source_url,
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
        "setup_command",
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
        if fields["auth_scheme"] not in {
            "", "none", "bearer", "header", "custom", "oauth"
        }:
            raise ValueError("authentication method is not supported")
    effective_auth = fields.get("auth_scheme", current.get("auth_scheme") or "")
    if transport == "stdio" and effective_auth == "oauth":
        raise ValueError("OAuth is available only for remote MCP servers.")
    if "setup_command" in fields:
        fields["setup_command"] = str(fields["setup_command"] or "").strip()
    effective_setup = fields.get("setup_command", current.get("setup_command") or "")
    if transport != "stdio" and effective_setup:
        raise ValueError("Setup commands are available only for local MCP servers.")
    effective_env = (
        json.loads(fields["env"])
        if "env" in fields
        else json.loads(current.get("env") or "{}")
    )
    file_env = {item["env_var"] for item in list_server_files(server_id)}
    duplicate_env = sorted(file_env.intersection(effective_env))
    if duplicate_env:
        raise ValueError(
            f"Environment variable {duplicate_env[0]} is already used by a credential file."
        )
    oauth_changed = (
        connection != current["connection"]
        or effective_auth != (current.get("auth_scheme") or "")
    )
    if oauth_changed:
        fields["oauth_tokens"] = ""
        fields["oauth_token_expires_at"] = 0
        fields["oauth_client_info"] = ""
    connection_fields = {
        "transport", "connection", "headers", "env", "auth_scheme"
    }
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
    result: DeletionResult
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute(
            "SELECT 1 FROM mcp_servers WHERE id = ?", (server_id,)
        ).fetchone() is None:
            conn.rollback()
            return DeletionResult("not_found")
        agent_rows = conn.execute(
            """
            SELECT DISTINCT subagents.name
            FROM subagent_mcp_sources source
            JOIN subagents ON subagents.id = source.subagent_id
            WHERE source.mcp_server_id = ?
            ORDER BY subagents.name
            """,
            (server_id,),
        ).fetchall()
        dependencies = tuple(f"the {row['name']} subagent" for row in agent_rows)
        if dependencies:
            conn.rollback()
            return DeletionResult("in_use", dependencies)
        try:
            cur = conn.execute("DELETE FROM mcp_servers WHERE id = ?", (server_id,))
            conn.commit()
            result = DeletionResult("deleted" if cur.rowcount else "not_found")
        except sqlite3.IntegrityError:
            conn.rollback()
            return DeletionResult("in_use", ("another saved configuration",))
    if result.deleted:
        _clear_materialized_server_files(server_id)
    return result


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
# Workflow definitions and reusable workflow nodes
# -----------------------------------------------------------------------------

_WORKFLOW_SELECT = """
    SELECT w.id, w.name, w.description, w.system_prompt, w.model_id,
           w.execution_mode, w.created_at, w.updated_at,
           m.name AS model_name, m.model,
           (SELECT COUNT(*) FROM subagent_nodes n
            WHERE n.workflow_id = w.id) +
           (SELECT COUNT(*) FROM workflow_graph_nodes wn
            WHERE wn.owner_workflow_id = w.id) AS node_count
    FROM workflows w
    LEFT JOIN models m ON m.id = w.model_id
"""


def _workflow_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    item["id"] = int(item["id"])
    item["model_id"] = int(item["model_id"]) if item["model_id"] is not None else None
    item["node_count"] = int(item.get("node_count") or 0)
    return item


def list_workflows() -> list[dict]:
    with _connect() as conn:
        return [
            _workflow_dict(row)
            for row in conn.execute(f"{_WORKFLOW_SELECT} ORDER BY w.updated_at DESC, w.id DESC")
        ]


def get_workflow(workflow_id: int) -> dict | None:
    with _connect() as conn:
        return _workflow_dict(
            conn.execute(f"{_WORKFLOW_SELECT} WHERE w.id = ?", (workflow_id,)).fetchone()
        )


def _workflow_mode(value) -> str:
    mode = str(value or "agentic").strip().lower()
    if mode not in {"agentic", "direct"}:
        raise ValueError("Execution mode must be agentic or direct.")
    return mode


def _optional_model_id(conn: sqlite3.Connection, value) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        model_id = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select an existing model.") from exc
    if conn.execute("SELECT 1 FROM models WHERE id = ?", (model_id,)).fetchone() is None:
        raise ValueError("Select an existing model.")
    return model_id


def create_workflow(
    *, name: str, description: str = "", system_prompt: str = "",
    model_id=None, execution_mode: str = "agentic",
) -> dict:
    with _connect() as conn:
        now = _now()
        mode = _workflow_mode(execution_mode)
        try:
            cur = conn.execute(
                """
                INSERT INTO workflows
                    (name, description, system_prompt, model_id,
                     execution_mode, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _required(name, "name"), str(description or "").strip(),
                    "" if mode == "direct" else str(system_prompt or "").strip(),
                    None if mode == "direct" else _optional_model_id(conn, model_id),
                    mode, now, now,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
        workflow_id = int(cur.lastrowid)
    return get_workflow(workflow_id)


def update_workflow(workflow_id: int, **fields) -> dict | None:
    allowed = {"name", "description", "system_prompt", "model_id", "execution_mode"}
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unsupported workflow field: {sorted(unknown)[0]}")
    with _connect() as conn:
        existing = conn.execute("SELECT * FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if existing is None:
            return None
        values = {
            "name": _required(fields.get("name", existing["name"]), "name"),
            "description": str(fields.get("description", existing["description"]) or "").strip(),
            "system_prompt": str(fields.get("system_prompt", existing["system_prompt"]) or "").strip(),
            "model_id": _optional_model_id(conn, fields.get("model_id", existing["model_id"])),
            "execution_mode": _workflow_mode(fields.get("execution_mode", existing["execution_mode"])),
        }
        if values["execution_mode"] == "direct":
            values["model_id"] = None
            values["system_prompt"] = ""
            nested = conn.execute(
                "SELECT 1 FROM subagent_nodes WHERE workflow_id = ? AND parent_node_id IS NOT NULL LIMIT 1",
                (workflow_id,),
            ).fetchone()
            if nested:
                raise ValueError("Move nested subagents to the workflow root before switching to direct mode.")
        try:
            conn.execute(
                """
                UPDATE workflows SET name = ?, description = ?, system_prompt = ?,
                    model_id = ?, execution_mode = ?, updated_at = ? WHERE id = ?
                """,
                (*values.values(), _now(), workflow_id),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
    return get_workflow(workflow_id)


def delete_workflow(workflow_id: int) -> DeletionResult:
    with _connect() as conn:
        row = conn.execute("SELECT name FROM workflows WHERE id = ?", (workflow_id,)).fetchone()
        if row is None:
            return DeletionResult("not_found")
        references = int(conn.execute(
            "SELECT COUNT(*) FROM workflow_graph_nodes WHERE child_workflow_id = ?",
            (workflow_id,),
        ).fetchone()[0])
        if references:
            return DeletionResult(
                "restricted",
                (f"{references} workflow placement{'s' if references != 1 else ''}",),
            )
        conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        conn.commit()
        return DeletionResult("deleted")


def _workflow_would_cycle(
    conn: sqlite3.Connection, owner_workflow_id: int, child_workflow_id: int
) -> bool:
    pending = [child_workflow_id]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current == owner_workflow_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(
            int(row[0]) for row in conn.execute(
                "SELECT child_workflow_id FROM workflow_graph_nodes WHERE owner_workflow_id = ?",
                (current,),
            )
        )
    return False


def _next_graph_position(
    conn: sqlite3.Connection, owner_workflow_id: int | None
) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(position), -1) + 1 FROM (
            SELECT position FROM subagent_nodes
            WHERE ((workflow_id IS NULL AND ? IS NULL) OR workflow_id = ?)
            UNION ALL
            SELECT position FROM workflow_graph_nodes
            WHERE ((owner_workflow_id IS NULL AND ? IS NULL) OR owner_workflow_id = ?)
        )
        """,
        (
            owner_workflow_id, owner_workflow_id,
            owner_workflow_id, owner_workflow_id,
        ),
    ).fetchone()
    return int(row[0])


def _reserve_graph_position(
    conn: sqlite3.Connection,
    owner_workflow_id: int,
    requested_position,
) -> int:
    """Open one deterministic sequence slot across both direct-node tables."""
    next_position = _next_graph_position(conn, owner_workflow_id)
    if requested_position in (None, ""):
        return next_position
    try:
        position = int(requested_position)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select a valid workflow insertion point.") from exc
    if position < 0 or position > next_position:
        raise ValueError("Select a valid workflow insertion point.")
    conn.execute(
        """
        UPDATE subagent_nodes SET position = position + 1
        WHERE workflow_id = ? AND position >= ?
        """,
        (owner_workflow_id, position),
    )
    conn.execute(
        """
        UPDATE workflow_graph_nodes SET position = position + 1
        WHERE owner_workflow_id = ? AND position >= ?
        """,
        (owner_workflow_id, position),
    )
    return position


def list_workflow_nodes(owner_workflow_id: int | None = None) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT n.id, n.owner_workflow_id, n.child_workflow_id,
                   n.parent_node_id, n.position, n.created_at,
                   w.name, w.description, w.execution_mode
            FROM workflow_graph_nodes n
            JOIN workflows w ON w.id = n.child_workflow_id
            WHERE ((n.owner_workflow_id IS NULL AND ? IS NULL)
                   OR n.owner_workflow_id = ?)
            ORDER BY n.position, n.created_at, n.id
            """,
            (owner_workflow_id, owner_workflow_id),
        ).fetchall()
        return [
            {
                **dict(row),
                "id": int(row["id"]),
                "owner_workflow_id": (
                    int(row["owner_workflow_id"])
                    if row["owner_workflow_id"] is not None else None
                ),
                "child_workflow_id": int(row["child_workflow_id"]),
                "parent_node_id": (
                    int(row["parent_node_id"])
                    if row["parent_node_id"] is not None else None
                ),
                "position": int(row["position"] or 0),
            }
            for row in rows
        ]


def add_workflow_node(
    child_workflow_id: int, parent_node_id: int | None = None,
    owner_workflow_id: int | None = None,
    position=None,
) -> dict:
    owner = None if owner_workflow_id in (None, "") else int(owner_workflow_id)
    parent_id = None if parent_node_id in (None, "") else int(parent_node_id)
    child = int(child_workflow_id)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM workflows WHERE id = ?", (child,)).fetchone() is None:
            raise ValueError("Select an existing workflow.")
        direct = False
        if owner is not None:
            owner_row = conn.execute(
                "SELECT execution_mode FROM workflows WHERE id = ?", (owner,)
            ).fetchone()
            if owner_row is None:
                raise ValueError("Select an existing workflow overview.")
            direct = owner_row["execution_mode"] == "direct"
            if direct and parent_id is not None:
                raise ValueError("Direct workflow steps cannot have parent nodes.")
            if _workflow_would_cycle(conn, owner, child):
                raise ValueError("A workflow cannot contain itself, directly or indirectly.")
        if parent_id is not None:
            parent = conn.execute(
                "SELECT workflow_id FROM subagent_nodes WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent is None or parent["workflow_id"] != owner:
                raise ValueError("The parent node belongs to a different workflow.")
        duplicate = conn.execute(
            """
            SELECT 1 FROM workflow_graph_nodes
            WHERE child_workflow_id = ?
              AND ((owner_workflow_id IS NULL AND ? IS NULL) OR owner_workflow_id = ?)
              AND ((parent_node_id IS NULL AND ? IS NULL) OR parent_node_id = ?)
            """,
            (child, owner, owner, parent_id, parent_id),
        ).fetchone()
        if duplicate and not direct:
            raise ValueError("This workflow is already connected under this parent.")
        selected_position = (
            _reserve_graph_position(conn, owner, position)
            if direct and owner is not None
            else _next_graph_position(conn, owner)
        )
        cur = conn.execute(
            """
            INSERT INTO workflow_graph_nodes
                (owner_workflow_id, child_workflow_id, parent_node_id, position, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (owner, child, parent_id, selected_position, _now()),
        )
        conn.commit()
        node_id = int(cur.lastrowid)
    return next(item for item in list_workflow_nodes(owner) if item["id"] == node_id)


def remove_workflow_node(node_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM workflow_graph_nodes WHERE id = ?", (node_id,))
        conn.commit()
        return cur.rowcount > 0


# -----------------------------------------------------------------------------
# Subagents
# -----------------------------------------------------------------------------

def _normalize_subagent_sources(
    conn: sqlite3.Connection, value
) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("MCP sources must be provided as a list.")
    known_servers = {
        int(row["id"]) for row in conn.execute("SELECT id FROM mcp_servers")
    }
    normalized: list[dict] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Every MCP source must be an object.")
        try:
            server_id = int(item.get("mcp_server_id"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Select an existing MCP server.") from exc
        if server_id not in known_servers:
            raise ValueError("Select an existing MCP server.")
        if server_id in seen:
            raise ValueError("Each MCP server can be selected only once.")
        seen.add(server_id)
        selected = item.get("enabled_tools")
        enabled_tools = (
            None
            if selected is None
            else _json_string_list(selected, "enabled MCP tools")
        )
        parsed = None if enabled_tools is None else json.loads(enabled_tools)
        # An explicit empty selection is equivalent to not granting the server.
        if parsed == []:
            continue
        normalized.append(
            {"mcp_server_id": server_id, "enabled_tools": enabled_tools}
        )
    if len(normalized) > 32:
        raise ValueError("A subagent can use at most 32 MCP servers.")
    return normalized


def _replace_subagent_sources(
    conn: sqlite3.Connection, subagent_id: int, sources
) -> list[dict]:
    normalized = _normalize_subagent_sources(conn, sources)
    current = [
        (int(row["mcp_server_id"]), row["enabled_tools"])
        for row in conn.execute(
            """
            SELECT mcp_server_id, enabled_tools
            FROM subagent_mcp_sources
            WHERE subagent_id = ?
            ORDER BY position, mcp_server_id
            """,
            (subagent_id,),
        )
    ]
    requested = [
        (source["mcp_server_id"], source["enabled_tools"])
        for source in normalized
    ]
    conn.execute(
        "DELETE FROM subagent_mcp_sources WHERE subagent_id = ?", (subagent_id,)
    )
    conn.executemany(
        """
        INSERT INTO subagent_mcp_sources (
            subagent_id, mcp_server_id, enabled_tools, position, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                subagent_id,
                source["mcp_server_id"],
                source["enabled_tools"],
                position,
                _now(),
            )
            for position, source in enumerate(normalized)
        ],
    )
    primary = normalized[0] if normalized else None
    conn.execute(
        """
        UPDATE subagents SET mcp_server_id = ?, enabled_tools = ? WHERE id = ?
        """,
        (
            primary["mcp_server_id"] if primary else None,
            primary["enabled_tools"] if primary else None,
            subagent_id,
        ),
    )
    if current != requested:
        # Source grants are definition-level. A placement allowlist may refer
        # to capabilities that were removed, so inherit the revised grants.
        conn.execute(
            "UPDATE subagent_nodes SET enabled_tools = NULL WHERE agent_id = ?",
            (subagent_id,),
        )
    return normalized


def _subagent_sources_for(
    conn: sqlite3.Connection, subagent_ids: set[int]
) -> dict[int, list[dict]]:
    if not subagent_ids:
        return {}
    placeholders = ",".join("?" for _ in subagent_ids)
    rows = conn.execute(
        f"""
        SELECT source.subagent_id, source.mcp_server_id, source.enabled_tools,
               source.position, server.name AS mcp_server_name,
               server.description AS mcp_server_description,
               server.connection_status
        FROM subagent_mcp_sources source
        JOIN mcp_servers server ON server.id = source.mcp_server_id
        WHERE source.subagent_id IN ({placeholders})
        ORDER BY source.subagent_id, source.position, server.name
        """,
        tuple(sorted(subagent_ids)),
    ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["subagent_id"]), []).append(
            {
                "mcp_server_id": int(row["mcp_server_id"]),
                "mcp_server_name": row["mcp_server_name"],
                "mcp_server_description": row["mcp_server_description"] or "",
                "connection_status": row["connection_status"] or "untested",
                "enabled_tools": (
                    json.loads(
                        _json_string_list(row["enabled_tools"], "enabled MCP tools")
                    )
                    if row["enabled_tools"] is not None
                    else None
                ),
            }
        )
    return grouped


def list_subagent_mcp_sources(subagent_id: int) -> list[dict]:
    with _connect() as conn:
        return _subagent_sources_for(conn, {int(subagent_id)}).get(
            int(subagent_id), []
        )

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
    workflow_id: int | None = None,
    position=None,
) -> int:
    agent = conn.execute(
        "SELECT enabled_tools FROM subagents WHERE id = ?", (agent_id,)
    ).fetchone()
    if agent is None:
        raise ValueError("Select an existing subagent.")
    direct = False
    if workflow_id is not None:
        workflow = conn.execute(
            "SELECT execution_mode FROM workflows WHERE id = ?", (workflow_id,)
        ).fetchone()
        if workflow is None:
            raise ValueError("Select an existing workflow.")
        direct = workflow["execution_mode"] == "direct"
        if direct and parent_node_id is not None:
            raise ValueError("Direct workflow steps cannot have parent nodes.")
    if parent_node_id is not None:
        parent = conn.execute(
            "SELECT agent_id, parent_node_id, workflow_id FROM subagent_nodes WHERE id = ?",
            (parent_node_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("Select an existing parent node.")
        if parent["workflow_id"] != workflow_id:
            raise ValueError("The parent node belongs to a different workflow.")
        current = parent
        visited: set[int] = set()
        while current is not None:
            if int(current["agent_id"]) == int(agent_id):
                raise ValueError(
                    "This subagent is already present in the selected branch."
                )
            ancestor_id = current["parent_node_id"]
            if ancestor_id is None:
                break
            if int(ancestor_id) in visited:
                raise ValueError("The saved subagent hierarchy contains a cycle.")
            visited.add(int(ancestor_id))
            current = conn.execute(
                "SELECT agent_id, parent_node_id FROM subagent_nodes WHERE id = ?",
                (int(ancestor_id),),
            ).fetchone()
        if _node_depth(conn, parent_node_id) >= MAX_SUBAGENT_DEPTH:
            raise ValueError(
                f"Subagents can be nested at most {MAX_SUBAGENT_DEPTH} levels below Mounir."
            )
    existing = conn.execute(
        """
        SELECT id FROM subagent_nodes
        WHERE agent_id = ?
          AND ((workflow_id IS NULL AND ? IS NULL) OR workflow_id = ?)
          AND (
            (parent_node_id IS NULL AND ? IS NULL) OR parent_node_id = ?
        )
        """,
        (agent_id, workflow_id, workflow_id, parent_node_id, parent_node_id),
    ).fetchone()
    if existing and not direct:
        raise ValueError("This subagent is already connected under this parent.")
    selected_position = (
        _reserve_graph_position(conn, workflow_id, position)
        if direct and workflow_id is not None
        else _next_graph_position(conn, workflow_id)
    )
    cur = conn.execute(
        """
        INSERT INTO subagent_nodes (
            agent_id, parent_node_id, enabled_tools, workflow_id, position, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            agent_id, parent_node_id, None, workflow_id,
            selected_position, _now(),
        ),
    )
    return int(cur.lastrowid)


def add_subagent_node(
    agent_id: int,
    parent_node_id: int | None = None,
    workflow_id: int | None = None,
    position=None,
) -> dict:
    """Place an existing reusable subagent in one workflow branch."""
    normalized_parent = None if parent_node_id in (None, "") else int(parent_node_id)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            node_id = _create_subagent_node(
                conn, int(agent_id), normalized_parent,
                None if workflow_id in (None, "") else int(workflow_id),
                position,
            )
            if workflow_id in (None, ""):
                _sync_legacy_connections(conn)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
    return get_subagent_node(node_id)


def _node_subtree_height(conn: sqlite3.Connection, node_id: int) -> int:
    children = [
        int(row["id"])
        for row in conn.execute(
            "SELECT id FROM subagent_nodes WHERE parent_node_id = ?", (node_id,)
        )
    ]
    return 1 + max((_node_subtree_height(conn, child) for child in children), default=0)


def _node_descendants(conn: sqlite3.Connection, node_id: int) -> set[int]:
    descendants: set[int] = set()
    pending = [int(node_id)]
    while pending:
        current = pending.pop()
        if current in descendants:
            raise ValueError("The saved subagent hierarchy contains a cycle.")
        descendants.add(current)
        pending.extend(
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM subagent_nodes WHERE parent_node_id = ?", (current,)
            )
        )
    return descendants


def _move_subagent_node(
    conn: sqlite3.Connection,
    subagent_id: int,
    parent_node_id: int | None,
) -> int:
    """Move the only placement of a legacy single-placement subagent."""
    placements = conn.execute(
        "SELECT id FROM subagent_nodes WHERE agent_id = ? AND workflow_id IS NULL ORDER BY id LIMIT 2",
        (subagent_id,),
    ).fetchall()
    if not placements:
        raise ValueError("Select an existing subagent.")
    if len(placements) > 1:
        raise ValueError(
            "This subagent has multiple workflow placements; move a specific node instead."
        )
    node_id = int(placements[0]["id"])
    parent_agent_id = None
    if parent_node_id is not None:
        parent = conn.execute(
            "SELECT agent_id FROM subagent_nodes WHERE id = ?", (parent_node_id,)
        ).fetchone()
        if parent is None:
            raise ValueError("Select an existing parent subagent.")
        if int(parent_node_id) in _node_descendants(conn, node_id):
            raise ValueError("A subagent cannot be moved beneath its own subtree.")
        if (
            _node_depth(conn, int(parent_node_id))
            + _node_subtree_height(conn, node_id)
            > MAX_SUBAGENT_DEPTH
        ):
            raise ValueError(
                f"Subagents can be nested at most {MAX_SUBAGENT_DEPTH} levels below Mounir."
            )
        parent_agent_id = int(parent["agent_id"])
    conn.execute(
        "UPDATE subagent_nodes SET parent_node_id = ? WHERE id = ?",
        (parent_node_id, node_id),
    )
    conn.execute(
        "UPDATE subagents SET parent_agent_id = ? WHERE id = ?",
        (parent_agent_id, subagent_id),
    )
    return node_id


def _canonical_parent_node(conn: sqlite3.Connection, agent_id: int) -> int:
    rows = conn.execute(
        """
        SELECT id FROM subagent_nodes WHERE agent_id = ? AND workflow_id IS NULL
        ORDER BY parent_node_id IS NOT NULL, created_at, id LIMIT 2
        """,
        (agent_id,),
    ).fetchall()
    if not rows:
        raise ValueError("Select an existing parent subagent.")
    if len(rows) > 1:
        raise ValueError(
            "This parent has multiple workflow placements; select a specific parent node."
        )
    return int(rows[0]["id"])


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
        WHERE child.workflow_id IS NULL
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
              AND child_node.workflow_id IS NULL
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
    mcp_server_id: int | None,
    confirm_tool_calls: bool = True,
    parent_agent_id: int | None = None,
    confirm_tools=None,
    icon_data: bytes = b"",
    icon_mime: str = "",
    dedupe_tools=None,
    enabled: bool = True,
    enabled_tools=None,
    parent_node_id: int | None = None,
    connect_to_workflow: bool = True,
    workflow_id: int | None = None,
    position=None,
    mcp_sources=None,
    skill_ids=None,
) -> int:
    try:
        selected_model_id = int(model_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Select a model before creating the subagent.") from exc
    selected_server_id = (
        None if mcp_server_id in (None, "", 0, "0") else int(mcp_server_id)
    )
    if selected_model_id <= 0 or conn.execute(
        "SELECT 1 FROM models WHERE id = ?", (selected_model_id,)
    ).fetchone() is None:
        raise ValueError("Select an existing model before creating the subagent.")
    if selected_server_id is not None and conn.execute(
        "SELECT 1 FROM mcp_servers WHERE id = ?", (selected_server_id,)
    ).fetchone() is None:
        raise ValueError("Select an existing MCP server before creating the subagent.")
    if confirm_tools is None:
        confirm_tools = ["*"] if _bool(confirm_tool_calls, "confirm_tool_calls") else []
    confirm_tools_json = _json_string_list(confirm_tools, "confirmation tools")
    dedupe_tools_json = _json_string_list(dedupe_tools or [], "duplicate protection tools")
    has_confirmations = bool(json.loads(confirm_tools_json))
    selected_workflow_id = (
        None if workflow_id in (None, "") else int(workflow_id)
    )
    if selected_workflow_id is not None and conn.execute(
        "SELECT 1 FROM workflows WHERE id = ?", (selected_workflow_id,)
    ).fetchone() is None:
        raise ValueError("Select an existing workflow.")
    selected_parent_id = _subagent_parent_id(parent_agent_id)
    existing_agent_ids = {
        int(row["id"]) for row in conn.execute("SELECT id FROM subagents")
    }
    if (
        selected_parent_id is not None
        and selected_parent_id not in existing_agent_ids
    ):
        raise ValueError("Select an existing subagent as the parent.")
    if parent_node_id not in (None, ""):
        try:
            selected_parent_node_id = int(parent_node_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("Select an existing parent node.") from exc
        parent = conn.execute(
            "SELECT agent_id, workflow_id FROM subagent_nodes WHERE id = ?",
            (selected_parent_node_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("Select an existing parent node.")
        if parent["workflow_id"] != selected_workflow_id:
            raise ValueError("The parent node belongs to a different workflow.")
        selected_parent_id = int(parent["agent_id"])
    else:
        selected_parent_node_id = (
            None
            if selected_parent_id is None or selected_workflow_id is not None
            else _canonical_parent_node(conn, selected_parent_id)
        )
    enabled_tools_json = (
        None
        if enabled_tools is None
        else _json_string_list(enabled_tools, "enabled tools")
    )
    try:
        cur = conn.execute(
            """
            INSERT INTO subagents
                (name, description, system_prompt, icon_data, icon_mime,
                 model_id, mcp_server_id, confirm_tool_calls, confirm_tools,
                 dedupe_tools, enabled, enabled_tools, parent_agent_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                enabled_tools_json,
                selected_parent_id if selected_workflow_id is None else None,
                _now(),
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise _friendly_integrity_error(exc) from exc
    agent_id = int(cur.lastrowid)
    requested_sources = (
        mcp_sources
        if mcp_sources is not None
        else (
            [{"mcp_server_id": selected_server_id, "enabled_tools": enabled_tools}]
            if selected_server_id is not None
            else []
        )
    )
    _replace_subagent_sources(conn, agent_id, requested_sources)
    _replace_subagent_skill_assignments(conn, agent_id, skill_ids or [])
    if _bool(connect_to_workflow, "connect_to_workflow"):
        _create_subagent_node(
            conn, agent_id, selected_parent_node_id, selected_workflow_id, position
        )
    if selected_workflow_id is None:
        _sync_legacy_connections(conn)
    conn.commit()
    return agent_id


def add_subagent(
    name: str,
    description: str,
    system_prompt: str,
    model_id: int,
    mcp_server_id: int | None = None,
    confirm_tool_calls: bool = True,
    parent_agent_id: int | None = None,
    confirm_tools=None,
    icon_data: bytes = b"",
    icon_mime: str = "",
    dedupe_tools=None,
    enabled: bool = True,
    enabled_tools=None,
    parent_node_id: int | None = None,
    connect_to_workflow: bool = True,
    workflow_id: int | None = None,
    position=None,
    mcp_sources=None,
    skill_ids=None,
) -> dict:
    with _connect() as conn:
        aid = _add_subagent(
            conn, name, description, system_prompt, model_id, mcp_server_id,
            confirm_tool_calls, parent_agent_id, confirm_tools, icon_data, icon_mime,
            dedupe_tools, enabled, enabled_tools, parent_node_id,
            connect_to_workflow, workflow_id, position, mcp_sources, skill_ids,
        )
        return get_subagent(aid)


_SUBAGENT_SELECT = """
    SELECT s.id, s.name, s.description, s.system_prompt,
           s.model_id, s.mcp_server_id, s.confirm_tool_calls, s.confirm_tools,
           s.dedupe_tools, s.enabled, s.enabled_tools, s.parent_agent_id,
           s.created_at,
           CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon,
           m.name AS model_name, m.model, m.provider, m.base_url, m.api_key,
           srv.name AS server_name, srv.transport, srv.connection,
           srv.headers, srv.env, srv.auth_scheme,
           srv.oauth_redirect_uri, srv.setup_command
    FROM subagents s
    JOIN models m ON s.model_id = m.id
    LEFT JOIN mcp_servers srv ON s.mcp_server_id = srv.id
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
    workflow_names = {
        int(item["id"]): item["name"]
        for item in conn.execute("SELECT id, name FROM workflows")
    }
    sources_by_agent = _subagent_sources_for(
        conn, {int(row["id"]) for row in rows}
    )
    skills_by_agent: dict[int, list[int]] = {}
    for assignment in conn.execute(
        """
        SELECT agent_key, skill_id FROM skill_assignments
        WHERE agent_type = 'subagent' AND enabled = 1
        ORDER BY skill_id
        """
    ):
        try:
            agent_id = int(assignment["agent_key"])
        except (TypeError, ValueError):
            continue
        skills_by_agent.setdefault(agent_id, []).append(int(assignment["skill_id"]))
    nodes = [
        dict(node)
        for node in conn.execute(
            """
            SELECT id, agent_id, parent_node_id, workflow_id, enabled_tools, created_at
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
        row["mcp_sources"] = sources_by_agent.get(agent_id, [])
        row["skill_ids"] = skills_by_agent.get(agent_id, [])
        row["mcp_server_count"] = len(row["mcp_sources"])
        placements = sorted(
            (item for item in nodes if int(item["agent_id"]) == agent_id),
            key=lambda item: (
                item["workflow_id"] is not None,
                item["created_at"] or "",
                int(item["id"]),
            ),
        )
        row["placement_count"] = len(placements)
        node = placements[0] if placements else None
        if node is None:
            row["node_id"] = None
            row["parent_node_id"] = None
            row["parent_agent_id"] = None
            row["parent_name"] = "Mounir"
            row["connected_to_supervisor"] = False
            row["child_agent_ids"] = []
            row["child_count"] = 0
            row["depth"] = 0
            row["path_names"] = []
            row["path_label"] = "Not connected"
            row["enabled_tools"] = (
                json.loads(_json_string_list(row["enabled_tools"], "enabled tools"))
                if row.get("enabled_tools") is not None
                else None
            )
            continue
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
        if node["workflow_id"] is not None:
            path_names[0] = workflow_names.get(int(node["workflow_id"]), "Workflow")
        parent_agent_id = int(parent_node["agent_id"]) if parent_node else None
        row["node_id"] = int(node["id"])
        row["parent_node_id"] = (
            int(node["parent_node_id"])
            if node["parent_node_id"] is not None
            else None
        )
        row["parent_agent_id"] = parent_agent_id
        row["parent_name"] = (
            names.get(parent_agent_id, "Mounir")
            if parent_agent_id is not None
            else "Mounir"
        )
        row["connected_to_supervisor"] = (
            parent_node is None and node["workflow_id"] is None
        )
        row["child_agent_ids"] = [
            int(child["agent_id"]) for child in direct_children
        ]
        row["child_count"] = len(direct_children)
        row["depth"] = len(path_names) - 1
        row["path_names"] = path_names
        row["path_label"] = " / ".join(path_names)
        row["enabled_tools"] = (
            json.loads(_json_string_list(row["enabled_tools"], "enabled tools"))
            if row.get("enabled_tools") is not None
            else None
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


def list_subagent_nodes(workflow_id: int | None = None) -> list[dict]:
    """Return placements from the global overview or one saved workflow."""
    with _connect() as conn:
        nodes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT n.id, n.agent_id, n.parent_node_id, n.workflow_id,
                       n.position, n.created_at,
                       s.name, s.description, s.enabled,
                       n.enabled_tools AS enabled_tools,
                       s.model_id, s.mcp_server_id,
                       CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon,
                       m.name AS model_name, m.model,
                       srv.name AS mcp_server_name
                FROM subagent_nodes n
                JOIN subagents s ON s.id = n.agent_id
                JOIN models m ON m.id = s.model_id
                LEFT JOIN mcp_servers srv ON srv.id = s.mcp_server_id
                WHERE ((n.workflow_id IS NULL AND ? IS NULL) OR n.workflow_id = ?)
                ORDER BY n.position, n.created_at, n.id
                """,
                (workflow_id, workflow_id),
            )
        ]
    by_id = {int(node["id"]): node for node in nodes}
    names = {int(node["agent_id"]): node["name"] for node in nodes}
    result = []
    for node in nodes:
        parent = (
            by_id.get(int(node["parent_node_id"]))
            if node["parent_node_id"] is not None
            else None
        )
        path_names = _subagent_node_path(node, by_id, names)
        result.append(
            {
                "id": int(node["id"]),
                "node_id": int(node["id"]),
                "subagent_id": int(node["agent_id"]),
                "parent_node_id": (
                    int(node["parent_node_id"])
                    if node["parent_node_id"] is not None
                    else None
                ),
                "parent_agent_id": int(parent["agent_id"]) if parent else None,
                "workflow_id": (
                    int(node["workflow_id"])
                    if node["workflow_id"] is not None else None
                ),
                "position": int(node["position"] or 0),
                "name": node["name"],
                "description": node["description"],
                "enabled": bool(node["enabled"]),
                "enabled_tools": (
                    json.loads(
                        _json_string_list(node["enabled_tools"], "enabled tools")
                    )
                    if node["enabled_tools"] is not None
                    else None
                ),
                "model_id": int(node["model_id"]),
                "model_name": node["model_name"],
                "model": node["model"],
                "mcp_server_id": (
                    int(node["mcp_server_id"])
                    if node["mcp_server_id"] is not None else None
                ),
                "mcp_server_name": node["mcp_server_name"] or "",
                "has_icon": bool(node["has_icon"]),
                "depth": len(path_names) - 1,
                "path_names": path_names,
                "path_label": " / ".join(path_names),
                "created_at": node["created_at"],
            }
        )
    return result


def get_subagent_node(node_id: int) -> dict | None:
    """Return one placement and its direct relations, separate from its subagent."""
    with _connect() as conn:
        node = conn.execute(
            """
            SELECT n.id, n.agent_id, n.parent_node_id,
                   n.enabled_tools AS enabled_tools, n.created_at,
                   n.workflow_id, n.position,
                   s.name, s.description, s.model_id, s.mcp_server_id, s.enabled,
                   CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon,
                   m.name AS model_name, m.model,
                   srv.name AS mcp_server_name
            FROM subagent_nodes n
            JOIN subagents s ON s.id = n.agent_id
            JOIN models m ON m.id = s.model_id
            LEFT JOIN mcp_servers srv ON srv.id = s.mcp_server_id
            WHERE n.id = ?
            """,
            (node_id,),
        ).fetchone()
        if node is None:
            return None

        scope_id = node["workflow_id"]

        nodes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT n.id, n.agent_id, n.parent_node_id, n.workflow_id,
                       n.position, n.created_at,
                       s.name, s.enabled,
                       CASE WHEN length(s.icon_data) > 0 THEN 1 ELSE 0 END AS has_icon
                FROM subagent_nodes n
                JOIN subagents s ON s.id = n.agent_id
                WHERE ((n.workflow_id IS NULL AND ? IS NULL) OR n.workflow_id = ?)
                """,
                (scope_id, scope_id),
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
            "workflow_id": int(scope_id) if scope_id is not None else None,
            "position": int(node_data["position"] or 0),
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
                "mcp_server_id": (
                    int(node_data["mcp_server_id"])
                    if node_data["mcp_server_id"] is not None else None
                ),
                "mcp_server_name": node_data["mcp_server_name"] or "",
                "mcp_sources": list_subagent_mcp_sources(
                    int(node_data["agent_id"])
                ),
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
    """Update shared tool access through any placement of the subagent."""
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
        node = conn.execute(
            "SELECT agent_id FROM subagent_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if node is None:
            conn.rollback()
            return None
        conn.execute(
            "UPDATE subagent_nodes SET enabled_tools = ? WHERE agent_id = ?",
            (normalized, int(node["agent_id"])),
        )
        conn.execute(
            "UPDATE subagents SET enabled_tools = ? WHERE id = ?",
            (normalized, int(node["agent_id"])),
        )
        conn.commit()
    return get_subagent_node(node_id)


def remove_subagent_node(node_id: int) -> dict | None:
    """Disconnect one placement branch without deleting saved subagents."""
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        node = conn.execute(
            "SELECT id, agent_id, parent_node_id FROM subagent_nodes WHERE id = ?",
            (node_id,),
        ).fetchone()
        if node is None:
            conn.rollback()
            return None
        descendants: list[tuple[int, int]] = []
        pending = [int(node_id)]
        visited: set[int] = set()
        while pending:
            current_id = pending.pop()
            if current_id in visited:
                raise ValueError("The saved node hierarchy contains a cycle.")
            visited.add(current_id)
            current = conn.execute(
                "SELECT agent_id FROM subagent_nodes WHERE id = ?", (current_id,)
            ).fetchone()
            if current is None:
                continue
            descendants.append((current_id, int(current["agent_id"])))
            pending.extend(
                int(row["id"])
                for row in conn.execute(
                    "SELECT id FROM subagent_nodes WHERE parent_node_id = ?",
                    (current_id,),
                )
            )

        for descendant_node_id, descendant_agent_id in reversed(descendants):
            conn.execute(
                "DELETE FROM subagent_nodes WHERE id = ?", (descendant_node_id,)
            )
        _sync_legacy_connections(conn)
        conn.commit()
        return {
            "ok": True,
            "subagent_id": int(node["agent_id"]),
            "parent_node_id": (
                int(node["parent_node_id"])
                if node["parent_node_id"] is not None
                else None
            ),
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
        _move_subagent_node(conn, child_agent_id, None)
    for child_agent_id in child_agent_ids - current.keys():
        _move_subagent_node(conn, child_agent_id, parent_node_id)


def update_subagent(subagent_id: int, **kwargs) -> dict | None:
    obsolete_relationship_fields = {
        "parent_agent_ids", "parent_node_ids", "placement_children"
    } & kwargs.keys()
    if obsolete_relationship_fields:
        raise ValueError(
            "Workflow connections are placement-specific; manage a subagent node instead."
        )
    parent_selection_supplied = "parent_agent_id" in kwargs
    selected_parent_id = (
        _subagent_parent_id(kwargs.pop("parent_agent_id"))
        if parent_selection_supplied
        else None
    )
    child_selection_supplied = "child_agent_ids" in kwargs
    selected_child_ids = (
        _child_agent_ids(kwargs.pop("child_agent_ids"))
        if child_selection_supplied
        else set()
    )
    sources_supplied = "mcp_sources" in kwargs
    requested_sources = kwargs.pop("mcp_sources", None)
    skills_supplied = "skill_ids" in kwargs
    requested_skill_ids = kwargs.pop("skill_ids", None)
    allowed = {
        "name", "description", "system_prompt", "model_id",
        "mcp_server_id", "confirm_tool_calls", "confirm_tools",
        "icon_data", "icon_mime", "dedupe_tools", "enabled", "enabled_tools",
    }
    fields = {
        k: v
        for k, v in kwargs.items()
        if k in allowed and v is not None
    }
    for nullable_field in ("mcp_server_id", "enabled_tools"):
        if nullable_field in kwargs:
            fields[nullable_field] = kwargs[nullable_field]
    if (
        not fields
        and not parent_selection_supplied
        and not child_selection_supplied
        and not sources_supplied
        and not skills_supplied
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
            fields["mcp_server_id"] = (
                None
                if fields["mcp_server_id"] in (None, "", 0, "0")
                else int(fields["mcp_server_id"])
            )
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
    if "enabled_tools" in fields:
        fields["enabled_tools"] = (
            None
            if fields["enabled_tools"] is None
            else _json_string_list(fields["enabled_tools"], "enabled tools")
        )
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        agent_ids = {
            int(row["id"]) for row in conn.execute("SELECT id FROM subagents")
        }
        if subagent_id not in agent_ids:
            return None
        if parent_selection_supplied:
            if selected_parent_id is not None and selected_parent_id not in agent_ids:
                raise ValueError("Select an existing parent subagent.")
            _move_subagent_node(
                conn,
                subagent_id,
                (
                    None
                    if selected_parent_id is None
                    else _canonical_parent_node(conn, selected_parent_id)
                ),
            )
        if "model_id" in fields and conn.execute(
            "SELECT 1 FROM models WHERE id = ?", (fields["model_id"],)
        ).fetchone() is None:
            raise ValueError("Select an existing model for the subagent.")
        if (
            "mcp_server_id" in fields
            and fields["mcp_server_id"] is not None
            and conn.execute(
            "SELECT 1 FROM mcp_servers WHERE id = ?",
            (fields["mcp_server_id"],),
            ).fetchone() is None
        ):
            raise ValueError("Select an existing MCP server for the subagent.")
        legacy_sources = None
        if not sources_supplied and "mcp_server_id" in fields:
            legacy_sources = (
                [
                    {
                        "mcp_server_id": fields["mcp_server_id"],
                        "enabled_tools": (
                            kwargs.get("enabled_tools")
                            if "enabled_tools" in kwargs
                            else None
                        ),
                    }
                ]
                if fields["mcp_server_id"] is not None
                else []
            )
        sets = ", ".join(f"{k} = ?" for k in fields)
        try:
            if fields:
                conn.execute(
                    f"UPDATE subagents SET {sets} WHERE id = ?",
                    (*fields.values(), subagent_id),
                )
                if "enabled_tools" in fields:
                    conn.execute(
                        "UPDATE subagent_nodes SET enabled_tools = ? WHERE agent_id = ?",
                        (fields["enabled_tools"], subagent_id),
                    )
            if sources_supplied or legacy_sources is not None:
                _replace_subagent_sources(
                    conn,
                    subagent_id,
                    requested_sources if sources_supplied else legacy_sources,
                )
            if skills_supplied:
                _replace_subagent_skill_assignments(
                    conn, subagent_id, requested_skill_ids or []
                )
            if child_selection_supplied:
                primary_node_id = _canonical_parent_node(conn, subagent_id)
                _set_node_children(conn, primary_node_id, selected_child_ids)
            if parent_selection_supplied or child_selection_supplied:
                _sync_legacy_connections(conn)
        except sqlite3.IntegrityError as exc:
            raise _friendly_integrity_error(exc) from exc
        conn.commit()
        return get_subagent(subagent_id)


def delete_subagent(subagent_id: int) -> bool:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        exists = conn.execute(
            "SELECT 1 FROM subagents WHERE id = ?", (subagent_id,)
        ).fetchone()
        if exists is None:
            conn.rollback()
            return False
        placements = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM subagent_nodes WHERE agent_id = ?", (subagent_id,)
            )
        ]
        disconnected: set[int] = set()
        for node_id in placements:
            disconnected.update(_node_descendants(conn, node_id))
        for node_id in sorted(disconnected, reverse=True):
            conn.execute("DELETE FROM subagent_nodes WHERE id = ?", (node_id,))
        # Clear the legacy single-parent projection before deleting the shared
        # definition; nested agents remain saved even though their placements
        # in the removed branches were disconnected.
        _sync_legacy_connections(conn)
        cur = conn.execute("DELETE FROM subagents WHERE id = ?", (subagent_id,))
        if cur.rowcount:
            conn.execute(
                "DELETE FROM skill_assignments WHERE agent_type = 'subagent' AND agent_key = ?",
                (str(subagent_id),),
            )
            _sync_legacy_connections(conn)
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
    env = _resolved_json_object(server.get("env") or "{}")
    env.update(_materialized_server_file_env(server_id))
    return {
        "server_id": server_id,
        "name": server["name"],
        "transport": server.get("transport") or "stdio",
        "connection": server["connection"],
        "headers": _resolved_json_object(server.get("headers") or "{}"),
        "env": env,
        "auth_scheme": server.get("auth_scheme") or "",
        "oauth_redirect_uri": server.get("oauth_redirect_uri") or "",
        "setup_command": server.get("setup_command") or "",
    }


def build_knowledge_service_spec() -> dict:
    """Build the internal GBrain connection owned by Knowledge."""
    env = {"GBRAIN_HOME": str(knowledge_protocol.local_home_parent())}
    embedding = get_knowledge_embedding_runtime()
    if embedding is not None:
        from .embedding_models import gbrain_provider_environment

        env.update(gbrain_provider_environment(embedding))
    return {
        "server_id": "builtin:knowledge",
        "name": knowledge_protocol.BUILTIN_SERVER_NAME,
        "server_name": knowledge_protocol.BUILTIN_SERVER_NAME,
        "transport": "stdio",
        "connection": knowledge_protocol.BUILTIN_SERVER_COMMAND,
        "headers": {},
        "env": env,
        "auth_scheme": "",
        "oauth_redirect_uri": "",
        "setup_command": knowledge_protocol.BUILTIN_SETUP_COMMAND,
    }


def get_builtin_agent_server_spec(agent_key: str) -> dict | None:
    """Resolve an internal service owned by a built-in specialist."""
    key = str(agent_key or "").removeprefix("builtin:").strip()
    if key != "knowledge":
        return None
    return build_knowledge_service_spec()


def build_specs(workflow_id: int | None = None) -> list[dict]:
    """Return active subagent specs for the global or a saved-workflow scope."""
    specs = []
    agents = {int(agent["id"]): agent for agent in list_subagents()}
    placements = list_subagent_nodes(workflow_id)
    children: dict[int, list[int]] = {}
    for placement in placements:
        if placement["parent_node_id"] is not None:
            children.setdefault(int(placement["parent_node_id"]), []).append(
                int(placement["subagent_id"])
            )
    for placement in placements:
        s = agents.get(int(placement["subagent_id"]))
        if s is None:
            continue
        if not s.get("enabled"):
            continue
        source_specs = []
        node_allowlist = placement.get("enabled_tools")
        for source in s.get("mcp_sources") or []:
            server = build_server_spec(int(source["mcp_server_id"]))
            if server is None:
                continue
            source_allowlist = source.get("enabled_tools")
            if node_allowlist is not None:
                server_prefix = f"{int(source['mcp_server_id'])}:"
                node_names = {
                    (
                        name[len(server_prefix):]
                        if str(name).startswith(server_prefix)
                        else str(name)
                    )
                    for name in node_allowlist
                    if ":" not in str(name) or str(name).startswith(server_prefix)
                }
                source_allowlist = (
                    sorted(node_names)
                    if source_allowlist is None
                    else [name for name in source_allowlist if name in node_names]
                )
            source_specs.append(
                {
                    **server,
                    "mcp_server_id": int(source["mcp_server_id"]),
                    "server_name": source["mcp_server_name"],
                    "allowed_tools": source_allowlist,
                }
            )
        primary = source_specs[0] if source_specs else {}
        specs.append({
            "id": s["id"],
            "node_id": placement["node_id"],
            "parent_node_id": placement.get("parent_node_id"),
            "workflow_id": placement.get("workflow_id"),
            "mcp_server_id": primary.get("mcp_server_id"),
            "mcp_sources": source_specs,
            "name": s["name"],
            "description": s.get("description")
            or (
                f"Uses {len(source_specs)} configured MCP source(s)."
                if source_specs else "Prompt-only subagent without MCP tools."
            ),
            "prompt": s["system_prompt"],

            "transport": primary.get("transport") or "stdio",
            "connection": primary.get("connection") or "",

            "headers": dict(primary.get("headers") or {}),
            "env": dict(primary.get("env") or {}),
            "auth_scheme": primary.get("auth_scheme") or "",
            "oauth_redirect_uri": primary.get("oauth_redirect_uri") or "",

            "parent_agent_id": placement.get("parent_agent_id"),
            "parent_name": (
                placement["path_names"][-2]
                if len(placement.get("path_names") or []) > 1
                else "Mounir"
            ),
            "connected_to_supervisor": (
                placement.get("workflow_id") is None
                and placement.get("parent_node_id") is None
            ),
            "child_agent_ids": children.get(int(placement["node_id"]), []),
            "allowed_tools": placement.get("enabled_tools"),

            "model": (s.get("model") or "").strip() or s["model_name"],
            "provider": s.get("provider") or "OpenAI compatible",
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
