from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Keep tests away from the owner's real ~/.mounir database.
_IMPORT_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["MOUNIR_DATA_DIR"] = _IMPORT_DATA_DIR.name

from mounir import db, mcp_agents
from mounir.specialists.mcp_agent import _mcp_session, discover_tools


class TemporaryDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temp_dir.cleanup()


class DatabaseTests(TemporaryDatabaseTest):
    def test_existing_database_is_migrated(self):
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.executescript(
                """
                CREATE TABLE models (
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE, provider TEXT,
                    base_url TEXT NOT NULL, api_key TEXT, created_at TEXT
                );
                CREATE TABLE mcp_servers (
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                    connection TEXT NOT NULL, created_at TEXT
                );
                CREATE TABLE subagents (
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE,
                    system_prompt TEXT NOT NULL, model_id INTEGER NOT NULL,
                    mcp_server_id INTEGER NOT NULL, parent TEXT, created_at TEXT
                );
                INSERT INTO mcp_servers (id, name, connection)
                VALUES (1, 'Old remote', 'https://example.test/mcp');
                INSERT INTO models (id, name, base_url)
                VALUES (1, 'qwen3:4b', 'http://localhost:11434/api/chat');
                """
            )

        db.init()

        with db._connect() as conn:
            server_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(mcp_servers)")
            }
            agent_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(subagents)")
            }
        self.assertTrue({"transport", "headers", "env"} <= server_columns)
        self.assertTrue({"description", "confirm_tool_calls"} <= agent_columns)
        self.assertEqual(db.get_server(1)["transport"], "streamable_http")
        self.assertEqual(db.get_model(1)["model"], "qwen3:4b")
        self.assertEqual(db.get_model(1)["base_url"], "http://localhost:11434/v1")
        self.assertEqual(stat.S_IMODE(db.DB_PATH.stat().st_mode), 0o600)

    def test_resolved_spec_preserves_transport_model_and_permissions(self):
        db.init()
        os.environ["MOUNIR_TEST_MCP_TOKEN"] = "secret-token"
        self.addCleanup(os.environ.pop, "MOUNIR_TEST_MCP_TOKEN", None)

        model = db.add_model(
            "Local preset", "qwen3:4b", "Ollama", "http://localhost:11434/v1", ""
        )
        server = db.add_server(
            "Remote test",
            "https://example.test/mcp",
            transport="streamable_http",
            headers={"Authorization": "Bearer $MOUNIR_TEST_MCP_TOKEN"},
        )
        agent = db.add_subagent(
            "Remote helper",
            "Handles remote test tasks.",
            "",
            model["id"],
            server["id"],
        )

        spec = db.build_specs()[0]
        self.assertEqual(spec["model"], "qwen3:4b")
        self.assertEqual(spec["transport"], "streamable_http")
        self.assertEqual(spec["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(spec["confirm_tools"], ["*"])
        self.assertEqual(agent["confirm_tool_calls"], 1)

    def test_foreign_keys_prevent_deleting_in_use_presets(self):
        db.init()
        model = db.add_model(
            "Model", "model-id", "Local", "http://localhost:11434/v1", ""
        )
        server = db.add_server("Server", f"{sys.executable} server.py")
        db.add_subagent(
            "Helper", "Handles helper tasks.", "", model["id"], server["id"]
        )
        self.assertFalse(db.delete_model(model["id"]))
        self.assertFalse(db.delete_server(server["id"]))

    def test_transport_and_json_are_validated(self):
        db.init()
        with self.assertRaisesRegex(ValueError, "local command"):
            db.add_server("Wrong", "https://example.test/mcp", transport="stdio")
        with self.assertRaisesRegex(ValueError, "valid JSON object"):
            db.add_server("Wrong JSON", "run-server", env="not-json")

    def test_partial_update_preserves_server_transport_and_connection(self):
        db.init()
        server = db.add_server(
            "Remote",
            "https://example.test/mcp",
            transport="streamable_http",
        )
        updated = db.update_server(server["id"], name="Renamed")
        self.assertEqual(updated["transport"], "streamable_http")
        self.assertEqual(updated["connection"], "https://example.test/mcp")

    def test_delegate_slug_collisions_are_rejected(self):
        db.init()
        model = db.add_model(
            "Model", "model-id", "Local", "http://localhost:11434/v1", ""
        )
        server = db.add_server("Server", f"{sys.executable} server.py")
        db.add_subagent(
            "Web Search", "Searches the web.", "", model["id"], server["id"]
        )
        with self.assertRaisesRegex(ValueError, "same delegate name"):
            mcp_agents._validate_agent_name("web-search")


class TransportTests(unittest.TestCase):
    def test_stdio_server_initializes_and_advertises_tools(self):
        fixture = Path(__file__).parent / "fixtures" / "echo_mcp_server.py"
        tools = asyncio.run(
            discover_tools(
                {
                    "transport": "stdio",
                    "connection": f'{sys.executable} "{fixture}"',
                    "env": {},
                }
            )
        )
        self.assertEqual([tool["name"] for tool in tools], ["echo"])

    def test_streamable_http_uses_sdk_three_value_transport_and_headers(self):
        calls = {}

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                calls["http_kwargs"] = kwargs

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

        class FakeTransport:
            async def __aenter__(self):
                return ("read", "write", lambda: "session-id")

            async def __aexit__(self, *_):
                return False

        def fake_streamable(url, **kwargs):
            calls["url"] = url
            calls["transport_kwargs"] = kwargs
            return FakeTransport()

        class FakeSession:
            def __init__(self, read, write):
                calls["streams"] = (read, write)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def initialize(self):
                calls["initialized"] = True

        async def connect():
            async with _mcp_session(
                {
                    "transport": "streamable_http",
                    "connection": "https://example.test/mcp",
                    "headers": {"Authorization": "Bearer token"},
                }
            ):
                pass

        with (
            patch("httpx.AsyncClient", FakeHTTPClient),
            patch(
                "mcp.client.streamable_http.streamable_http_client",
                fake_streamable,
            ),
            patch("mcp.ClientSession", FakeSession),
        ):
            asyncio.run(connect())

        self.assertEqual(calls["url"], "https://example.test/mcp")
        self.assertEqual(
            calls["http_kwargs"]["headers"], {"Authorization": "Bearer token"}
        )
        self.assertEqual(calls["streams"], ("read", "write"))
        self.assertTrue(calls["initialized"])


class AdminApiTests(TemporaryDatabaseTest):
    def test_admin_flow_persists_transport_and_tests_connection(self):
        import httpx
        import server as web_server

        db.init()
        fixture = Path(__file__).parent / "fixtures" / "echo_mcp_server.py"

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                model_response = await client.post(
                    "/api/models",
                    json={
                        "name": "Local",
                        "model": "qwen3:4b",
                        "provider": "Ollama",
                        "base_url": "http://localhost:11434/v1",
                        "api_key": "",
                    },
                )
                self.assertEqual(model_response.status_code, 200)
                server_response = await client.post(
                    "/api/mcp-servers",
                    json={
                        "name": "Echo",
                        "transport": "stdio",
                        "connection": f'{sys.executable} "{fixture}"',
                        "headers": "{}",
                        "env": '{"ECHO_TOKEN":"pasted-in-the-interface"}',
                    },
                )
                self.assertEqual(server_response.status_code, 200)
                self.assertEqual(server_response.json()["transport"], "stdio")
                self.assertEqual(
                    db.build_server_spec(server_response.json()["id"])["env"],
                    {"ECHO_TOKEN": "pasted-in-the-interface"},
                )
                agent_response = await client.post(
                    "/api/subagents",
                    json={
                        "name": "Echo helper",
                        "description": "Echoes test data.",
                        "system_prompt": "",
                        "model_id": model_response.json()["id"],
                        "mcp_server_id": server_response.json()["id"],
                        "confirm_tool_calls": True,
                    },
                )
                self.assertEqual(agent_response.status_code, 200)
                test_response = await client.post(
                    f"/api/mcp-servers/{server_response.json()['id']}/test"
                )
                self.assertEqual(test_response.status_code, 200)
                self.assertEqual(
                    [tool["name"] for tool in test_response.json()["tools"]],
                    ["echo"],
                )
                overview_response = await client.get("/api/agent-overview")
                self.assertEqual(overview_response.status_code, 200)
                overview = overview_response.json()
                self.assertEqual(overview["supervisor"]["name"], "Mounir")
                self.assertEqual(len(overview["builtins"]), 5)
                self.assertTrue(overview["supervisor"]["model"])

                web_server.agent.conversation.reset()
                web_server.agent.conversation.add_user("Keep this conversation")
                web_server.agent.conversation.add_message(
                    {
                        "role": "assistant",
                        "content": "Internal delegation text",
                        "tool_calls": [{"id": "call_1"}],
                    }
                )
                web_server.agent.conversation.add_assistant("Welcome back")
                history_response = await client.get("/api/conversation")
                self.assertEqual(history_response.status_code, 200)
                self.assertEqual(
                    history_response.json()["messages"],
                    [
                        {"role": "user", "content": "Keep this conversation"},
                        {"role": "assistant", "content": "Welcome back"},
                    ],
                )
                web_server.agent.conversation.reset()

        asyncio.run(exercise_api())


if __name__ == "__main__":
    unittest.main()
