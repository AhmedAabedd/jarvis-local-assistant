from __future__ import annotations

import asyncio
import base64
import json
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

from mounir import browser_control, config, db, mcp_agents, tools as mounir_tools
from mounir.specialists import mcp_agent
from mounir.specialists.mcp_agent import _call, _mcp_session, _system_prompt, discover_tools


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
    def test_profile_is_persisted_and_builds_runtime_prompts(self):
        db.init()
        profile = db.update_profile(
            user_name="Lina",
            assistant_name="Atlas",
            location="Paris, France",
            preferred_language="fr",
        )

        self.assertEqual(db.get_profile(), profile)
        self.assertEqual(profile["assistant_name"], "Atlas")
        self.assertIn("You are Atlas", config.build_system_prompt(profile))
        self.assertIn("User: Lina", config.build_context_message(profile))
        self.assertIn("Reply in French", config.build_system_prompt(profile))
        self.assertIn("Location: Paris, France", config.profile_instruction(profile))

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
                    mcp_server_id INTEGER NOT NULL,
                    confirm_tool_calls INTEGER NOT NULL DEFAULT 1,
                    parent TEXT, created_at TEXT
                );
                INSERT INTO mcp_servers (id, name, connection)
                VALUES (1, 'Old remote', 'https://example.test/mcp');
                INSERT INTO models (id, name, base_url)
                VALUES (1, 'qwen3:4b', 'http://localhost:11434/api/chat');
                INSERT INTO subagents
                    (id, name, system_prompt, model_id, mcp_server_id, confirm_tool_calls)
                VALUES (1, 'Old helper', '', 1, 1, 0);
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
        self.assertTrue(
            {"description", "setup_type", "transport", "headers", "env"}
            <= server_columns
        )
        self.assertTrue(
            {
                "description", "icon_data", "icon_mime",
                "confirm_tool_calls", "confirm_tools", "dedupe_tools",
            }
            <= agent_columns
        )
        self.assertEqual(db.get_server(1)["transport"], "streamable_http")
        self.assertEqual(db.get_model(1)["model"], "qwen3:4b")
        self.assertEqual(db.get_model(1)["base_url"], "http://localhost:11434/v1")
        self.assertEqual(json.loads(db.get_subagent(1)["confirm_tools"]), [])
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
            confirm_tools=["dangerous_action"],
            dedupe_tools=["dangerous_action"],
        )

        spec = next(item for item in db.build_specs() if item["name"] == "Remote helper")
        self.assertEqual(spec["model"], "qwen3:4b")
        self.assertEqual(spec["transport"], "streamable_http")
        self.assertEqual(spec["headers"]["Authorization"], "Bearer secret-token")
        self.assertEqual(spec["confirm_tools"], ["dangerous_action"])
        self.assertEqual(spec["dedupe_tools"], ["dangerous_action"])
        self.assertEqual(agent["confirm_tool_calls"], 1)
        self.assertEqual(agent["confirm_tools"], '["dangerous_action"]')

    def test_builtin_email_is_migrated_once_to_dynamic_registry(self):
        db.init()
        email = db.get_subagent_by_name("Email")
        self.assertIsNotNone(email)
        self.assertEqual(email["server_name"], "Gmail MCP")
        email_server = db.get_server(email["mcp_server_id"])
        self.assertEqual(email_server["setup_type"], "gmail_oauth")
        self.assertTrue(email_server["description"])
        self.assertEqual(
            json.loads(email["confirm_tools"]),
            ["send_email", "delete_email", "batch_delete_emails"],
        )
        self.assertEqual(json.loads(email["dedupe_tools"]), ["send_email"])
        email_spec = next(spec for spec in db.build_specs() if spec["name"] == "Email")
        self.assertEqual(
            mcp_agents.delegate_schema(email_spec)["function"]["name"],
            "delegate_to_email",
        )
        builtin_names = {
            schema["function"]["name"] for schema in mounir_tools.SCHEMAS
        }
        self.assertNotIn("delegate_to_email", builtin_names)
        self.assertTrue(db.delete_subagent(email["id"]))
        db.init()
        self.assertIsNone(db.get_subagent_by_name("Email"))

    def test_builtin_researcher_is_migrated_once_to_playwright(self):
        db.init()
        researcher = db.get_subagent_by_name("Researcher")
        self.assertIsNotNone(researcher)
        researcher_server = db.get_server(researcher["mcp_server_id"])
        self.assertTrue(researcher_server["description"])
        self.assertEqual(researcher_server["setup_type"], "")
        self.assertEqual(researcher["server_name"], "Playwright Web")
        self.assertIn("@playwright/mcp@0.0.78", researcher["connection"])
        self.assertIn("--headless", researcher["connection"])
        self.assertIn("--isolated", researcher["connection"])
        self.assertEqual(
            json.loads(researcher["confirm_tools"]),
            [
                "browser_click",
                "browser_type",
                "browser_fill_form",
                "browser_press_key",
                "browser_select_option",
                "browser_handle_dialog",
                "browser_file_upload",
                "browser_drop",
                "browser_run_code_unsafe",
            ],
        )
        researcher_spec = next(
            spec for spec in db.build_specs() if spec["name"] == "Researcher"
        )
        self.assertEqual(
            mcp_agents.delegate_schema(researcher_spec)["function"]["name"],
            "delegate_to_researcher",
        )
        builtin_names = {
            schema["function"]["name"] for schema in mounir_tools.SCHEMAS
        }
        self.assertNotIn("delegate_to_researcher", builtin_names)
        self.assertTrue(db.delete_subagent(researcher["id"]))
        db.init()
        self.assertIsNone(db.get_subagent_by_name("Researcher"))

    def test_dynamic_default_agents_compile_into_the_graph(self):
        from mounir.langgraph_agent import build_graph

        db.init()
        graph = build_graph()
        node_names = set(graph.get_graph().nodes)
        self.assertIn("mcp_email", node_names)
        self.assertIn("mcp_researcher", node_names)
        self.assertNotIn("email", node_names)
        self.assertNotIn("researcher", node_names)

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
    def test_mcp_tool_timeout_reports_unknown_external_state(self):
        class SlowSession:
            async def call_tool(self, name, args):
                await asyncio.sleep(0.05)

        result, executed = asyncio.run(
            _call(
                SlowSession(),
                "slow_action",
                {},
                set(),
                tool_timeout_seconds=0.001,
            )
        )

        self.assertTrue(executed)
        self.assertIn("timed out", result)
        self.assertIn("state is unknown", result)
        self.assertIn("do not retry", result)

    def test_whole_mcp_agent_has_a_deadline(self):
        async def slow_agent(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            return "late"

        spec = {
            "name": "Slow helper",
            "connection": "fake-server",
            "api_key": "",
        }
        with (
            patch.object(mcp_agent, "_run_async", slow_agent),
            patch.object(mcp_agent, "MCP_AGENT_TIMEOUT_SECONDS", 0.001),
        ):
            result = mcp_agent.run("wait", spec)

        self.assertIn("Slow helper agent timed out", result)
        self.assertIn("do not retry", result)

    def test_all_specialists_share_the_capability_boundary(self):
        profile = {
            "user_name": "Lina",
            "assistant_name": "Atlas",
            "preferred_language": "auto",
        }
        dynamic_prompt = _system_prompt("Use the echo tool.", profile)
        builtin_prompt = config.specialist_system_prompt("You are a tester.", profile)

        for prompt in (dynamic_prompt, builtin_prompt):
            self.assertIn(config.SUBAGENT_CAPABILITY_PROMPT.strip(), prompt)
            self.assertIn("I can't complete this request", prompt)
            self.assertIn("Assistant name: Atlas", prompt)

    def test_only_selected_tools_require_confirmation(self):
        calls = []

        class Session:
            async def call_tool(self, name, args):
                calls.append((name, args))
                return type("Result", (), {"content": [], "isError": False})()

        async def immediate_thread_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with (
            patch("asyncio.to_thread", immediate_thread_call),
            patch("mounir.tools.confirm_fn", return_value=False) as confirm,
        ):
            declined, declined_executed = asyncio.run(
                _call(Session(), "send_email", {"to": "x"}, {"send_email"})
            )
            allowed, allowed_executed = asyncio.run(
                _call(Session(), "search_emails", {"q": "x"}, {"send_email"})
            )

        self.assertIn("declined", declined.lower())
        self.assertFalse(declined_executed)
        self.assertEqual(allowed, "(empty result)")
        self.assertTrue(allowed_executed)
        confirm.assert_called_once()
        self.assertEqual(calls, [("search_emails", {"q": "x"})])

    def test_duplicate_protected_action_is_blocked_for_the_whole_turn(self):
        calls = []
        protected_attempts = set()

        class Session:
            async def call_tool(self, name, args):
                calls.append((name, args))
                return type("Result", (), {"content": [], "isError": False})()

        async def immediate_thread_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with (
            patch("asyncio.to_thread", immediate_thread_call),
            patch("mounir.tools.confirm_fn", return_value=True) as confirm,
        ):
            first, first_executed = asyncio.run(
                _call(
                    Session(),
                    "send_email",
                    {"to": "x", "subject": "Hello"},
                    {"send_email"},
                    protected_attempts,
                    "gmail-server",
                    {"send_email"},
                )
            )
            duplicate, duplicate_executed = asyncio.run(
                _call(
                    Session(),
                    "send_email",
                    {"subject": "Hello", "to": "x"},
                    {"send_email"},
                    protected_attempts,
                    "gmail-server",
                    {"send_email"},
                )
            )
            different, different_executed = asyncio.run(
                _call(
                    Session(),
                    "send_email",
                    {"to": "y", "subject": "Hello"},
                    {"send_email"},
                    protected_attempts,
                    "gmail-server",
                    {"send_email"},
                )
            )

        self.assertEqual(first, "(empty result)")
        self.assertTrue(first_executed)
        self.assertIn("duplicate protected action blocked", duplicate.lower())
        self.assertFalse(duplicate_executed)
        self.assertEqual(different, "(empty result)")
        self.assertTrue(different_executed)
        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(
            calls,
            [
                ("send_email", {"to": "x", "subject": "Hello"}),
                ("send_email", {"to": "y", "subject": "Hello"}),
            ],
        )

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
        web_server.GMAIL_AUTH_DIR = Path(self.temp_dir.name) / ".gmail-mcp"
        web_server.GMAIL_OAUTH_KEYS = web_server.GMAIL_AUTH_DIR / "gcp-oauth.keys.json"
        web_server.GMAIL_CREDENTIALS = web_server.GMAIL_AUTH_DIR / "credentials.json"
        fixture = Path(__file__).parent / "fixtures" / "echo_mcp_server.py"

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                gmail = db.get_subagent_by_name("Email")
                # Setup capability is saved metadata, not inferred from a known
                # npm package string in the editable connection command.
                db.update_server(
                    gmail["mcp_server_id"],
                    connection="npx -y user-selected-email-server",
                )
                gmail_status = await client.get(
                    f"/api/mcp-servers/{gmail['mcp_server_id']}/setup"
                )
                self.assertEqual(gmail_status.status_code, 200)
                self.assertEqual(gmail_status.json()["title"], "Gmail account")
                self.assertEqual(
                    gmail_status.json()["status"]["text"], "OAuth file required"
                )
                oauth_upload = await client.post(
                    f"/api/mcp-servers/{gmail['mcp_server_id']}/setup/files/oauth_file",
                    files={
                        "file": (
                            "oauth.json",
                            json.dumps(
                                {
                                    "installed": {
                                        "client_id": "test-client",
                                        "client_secret": "test-secret",
                                    }
                                }
                            ),
                            "application/json",
                        )
                    },
                )
                self.assertEqual(oauth_upload.status_code, 200)
                self.assertTrue(web_server.GMAIL_OAUTH_KEYS.exists())

                class FakeAuthProcess:
                    returncode = 0

                    async def communicate(self):
                        web_server.GMAIL_CREDENTIALS.write_text(
                            json.dumps({"refresh_token": "test-refresh-token"})
                        )
                        return b"connected", b""

                async def fake_auth_process(*args, **kwargs):
                    return FakeAuthProcess()

                with patch("asyncio.create_subprocess_exec", fake_auth_process):
                    gmail_connect = await client.post(
                        f"/api/mcp-servers/{gmail['mcp_server_id']}/setup/actions/connect"
                    )
                self.assertEqual(gmail_connect.status_code, 200)
                self.assertEqual(
                    gmail_connect.json()["setup"]["status"],
                    {"text": "Ready", "kind": "ok"},
                )
                self.assertEqual(
                    stat.S_IMODE(web_server.GMAIL_CREDENTIALS.stat().st_mode), 0o600
                )

                web_server.GMAIL_CREDENTIALS.write_text(
                    json.dumps(
                        {
                            "refresh_token": "expired-test-token",
                            "refresh_token_expires_in": 1,
                        }
                    )
                )
                expired_time = web_server.GMAIL_CREDENTIALS.stat().st_mtime - 10
                os.utime(
                    web_server.GMAIL_CREDENTIALS,
                    (expired_time, expired_time),
                )
                expired_status = await client.get(
                    f"/api/mcp-servers/{gmail['mcp_server_id']}/setup"
                )
                self.assertEqual(
                    expired_status.json()["status"],
                    {"text": "Authorization expired", "kind": "error"},
                )

                with patch("asyncio.create_subprocess_exec", fake_auth_process):
                    gmail_reconnect = await client.post(
                        f"/api/mcp-servers/{gmail['mcp_server_id']}/setup/actions/connect"
                    )
                self.assertEqual(gmail_reconnect.status_code, 200)
                self.assertEqual(
                    gmail_reconnect.json()["setup"]["status"]["text"], "Ready"
                )
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
                        "description": "A generic MCP server used by tests.",
                        "transport": "stdio",
                        "connection": f'{sys.executable} "{fixture}"',
                        "headers": "{}",
                        "env": '{"ECHO_TOKEN":"pasted-in-the-interface"}',
                    },
                )
                self.assertEqual(server_response.status_code, 200)
                self.assertEqual(server_response.json()["transport"], "stdio")
                self.assertEqual(
                    server_response.json()["description"],
                    "A generic MCP server used by tests.",
                )
                generic_setup = await client.get(
                    f"/api/mcp-servers/{server_response.json()['id']}/setup"
                )
                self.assertEqual(generic_setup.status_code, 404)
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
                        "confirm_tools": ["echo"],
                        "dedupe_tools": ["echo"],
                        "icon_data": (
                            "data:image/png;base64,"
                            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                            "AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                        ),
                    },
                )
                self.assertEqual(agent_response.status_code, 200)
                self.assertEqual(json.loads(agent_response.json()["confirm_tools"]), ["echo"])
                self.assertEqual(json.loads(agent_response.json()["dedupe_tools"]), ["echo"])
                self.assertEqual(agent_response.json()["has_icon"], 1)
                icon_response = await client.get(
                    f"/api/subagents/{agent_response.json()['id']}/icon"
                )
                self.assertEqual(icon_response.status_code, 200)
                self.assertEqual(icon_response.headers["content-type"], "image/png")
                self.assertTrue(
                    icon_response.content.startswith(base64.b64decode("iVBORw0KGgo="))
                )
                test_response = await client.post(
                    f"/api/mcp-servers/{server_response.json()['id']}/test"
                )
                self.assertEqual(test_response.status_code, 200)
                self.assertEqual(
                    test_response.json()["tools"],
                    [
                        {
                            "name": "echo",
                            "description": "Return the supplied text.",
                        }
                    ],
                )
                overview_response = await client.get("/api/agent-overview")
                self.assertEqual(overview_response.status_code, 200)
                overview = overview_response.json()
                self.assertEqual(overview["supervisor"]["name"], "Mounir")
                self.assertEqual(len(overview["builtins"]), 3)
                self.assertTrue(overview["supervisor"]["model"])

                profile_response = await client.put(
                    "/api/profile",
                    json={
                        "user_name": "Lina",
                        "assistant_name": "Atlas",
                        "location": "Paris, France",
                        "preferred_language": "fr",
                    },
                )
                self.assertEqual(profile_response.status_code, 200)
                self.assertEqual(profile_response.json()["assistant_name"], "Atlas")
                updated_overview = await client.get("/api/agent-overview")
                self.assertEqual(
                    updated_overview.json()["supervisor"]["name"], "Atlas"
                )

                remove_icon = await client.put(
                    f"/api/subagents/{agent_response.json()['id']}",
                    json={"icon_data": ""},
                )
                self.assertEqual(remove_icon.status_code, 200)
                self.assertEqual(remove_icon.json()["has_icon"], 0)
                missing_icon = await client.get(
                    f"/api/subagents/{agent_response.json()['id']}/icon"
                )
                self.assertEqual(missing_icon.status_code, 404)

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


class BrowserToolTests(unittest.TestCase):
    def test_open_uses_the_operating_system_default_browser(self):
        with patch.object(browser_control, "open_default", return_value=True) as opened:
            result = mounir_tools.open_browser("example.com")

        opened.assert_called_once_with("https://example.com")
        self.assertIn("default browser", result)

    def test_close_requires_confirmation_and_never_closes_when_declined(self):
        app = browser_control.BrowserApp("Firefox", executable="/usr/bin/firefox")
        with (
            patch.object(browser_control, "default_browser", return_value=app),
            patch.object(browser_control, "close_default") as close,
            patch.object(mounir_tools, "confirm_fn", return_value=False),
        ):
            result = mounir_tools.close_browser()

        self.assertEqual(result, mounir_tools.USER_DECLINED)
        close.assert_not_called()

    def test_close_delegates_to_the_portable_browser_adapter(self):
        app = browser_control.BrowserApp("Firefox", executable="/usr/bin/firefox")
        with (
            patch.object(browser_control, "default_browser", return_value=app),
            patch.object(browser_control, "close_default", return_value=(True, "Closed Firefox.")) as close,
            patch.object(mounir_tools, "confirm_fn", return_value=True),
        ):
            result = mounir_tools.close_browser()

        self.assertEqual(result, "Closed Firefox.")
        close.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
