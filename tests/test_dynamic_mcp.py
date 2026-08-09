from __future__ import annotations

import asyncio
import base64
import json
import os
import sqlite3
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

# Keep tests away from the owner's real ~/.mounir database.
_IMPORT_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["MOUNIR_DATA_DIR"] = _IMPORT_DATA_DIR.name
os.environ["MOUNIR_TELEGRAM_ENABLED"] = "false"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)

from mounir import (
    browser_control,
    config,
    db,
    heartbeat as heartbeat_mod,
    langgraph_agent,
    llm as llm_mod,
    mcp_agents,
    tools as mounir_tools,
)
from mounir.agent import Agent
from mounir.memory import Conversation
from mounir.specialists import mcp_agent
from mounir.specialists import system as system_agent
from mounir.specialists.mcp_agent import _call, _mcp_session, _system_prompt, discover_tools
from mounir.telegram_bridge import TelegramBridge


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
    def _create_email_fixture(self):
        model = db.add_model(
            "Email model", "email-test", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Gmail MCP", "npx -y test-gmail-mcp")
        return db.add_subagent(
            "Email", "Handles test email.", "", model["id"], server["id"],
            confirm_tools=["send_email", "delete_email"],
            dedupe_tools=["send_email"],
        )

    def test_telegram_settings_migrate_env_and_never_expose_token(self):
        with (
            patch.object(config, "TELEGRAM_BOT_TOKEN", "123:secret"),
            patch.object(config, "TELEGRAM_CHAT_ID", "42"),
            patch.object(config, "TELEGRAM_ENABLED", True),
        ):
            db.init()

        public = db.get_telegram_settings()
        private = db.get_telegram_settings(include_secret=True)
        self.assertTrue(public["enabled"])
        self.assertTrue(public["token_configured"])
        self.assertTrue(public["paired"])
        self.assertNotIn("bot_token", public)
        self.assertNotIn("chat_id", public)
        self.assertEqual(private["bot_token"], "123:secret")
        self.assertEqual(private["chat_id"], "42")
        self.assertEqual(stat.S_IMODE(db.DB_PATH.stat().st_mode), 0o600)

    def test_voice_configuration_is_persisted_without_exposing_credentials(self):
        db.init()
        saved = db.update_voice_settings(
            stt={
                "provider": "groq",
                "model": "whisper-large-v3-turbo",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "groq-voice-key",
                "language": "auto",
            },
            tts={
                "provider": "google",
                "model": "en-US-Neural2-D",
                "base_url": "https://texttospeech.googleapis.com/v1",
                "api_key": "google-voice-key",
                "language": "en-US",
            },
        )

        self.assertNotIn("api_key", saved["stt"])
        self.assertNotIn("api_key", saved["tts"])
        self.assertTrue(saved["stt"]["api_key_configured"])
        self.assertTrue(saved["tts"]["api_key_configured"])
        self.assertEqual(db.get_voice_runtime("stt")["api_key"], "groq-voice-key")
        self.assertEqual(db.get_voice_runtime("tts")["api_key"], "google-voice-key")

        db.update_voice_settings(
            stt={
                "provider": "groq",
                "model": "whisper-large-v3-turbo",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "",
                "language": "fr",
            }
        )
        self.assertEqual(db.get_voice_runtime("stt")["api_key"], "groq-voice-key")
        self.assertEqual(db.get_voice_runtime("stt")["language"], "fr")

    def test_voice_runtime_dispatches_selected_stt_and_tts_providers(self):
        db.init()
        db.update_voice_settings(
            stt={
                "provider": "groq",
                "model": "whisper-test",
                "base_url": "https://speech.example.test/v1",
                "api_key": "speech-key",
                "language": "en",
            },
            tts={
                "provider": "google",
                "model": "voice-test",
                "base_url": "https://voice.example.test/v1",
                "api_key": "voice-key",
                "language": "en-US",
            },
        )
        from mounir import stt as stt_mod, tts as tts_mod

        with patch.object(
            stt_mod, "_transcribe_groq", return_value=("hello", "en")
        ) as transcribe:
            self.assertEqual(stt_mod.transcribe([0.1]), ("hello", "en"))
        self.assertEqual(transcribe.call_args.args[2]["model"], "whisper-test")
        self.assertEqual(transcribe.call_args.args[2]["api_key"], "speech-key")

        with patch.object(
            tts_mod, "_synthesize_google_wav", return_value=b"wav"
        ) as synthesize:
            self.assertEqual(tts_mod.synthesize_wav("hello"), b"wav")
        self.assertEqual(synthesize.call_args.args[1]["model"], "voice-test")
        self.assertEqual(synthesize.call_args.args[1]["api_key"], "voice-key")

    def test_telegram_token_replacement_and_pairing_are_persisted(self):
        db.init()
        with self.assertRaisesRegex(ValueError, "bot token"):
            db.update_telegram_settings(enabled=True)

        saved = db.update_telegram_settings(bot_token="123:first", enabled=True)
        self.assertTrue(saved["enabled"])
        self.assertFalse(saved["paired"])
        paired = db.pair_telegram_chat(42, "Mounir Owner", "owner")
        self.assertTrue(paired["paired"])
        self.assertEqual(paired["chat_name"], "Mounir Owner")

        replaced = db.update_telegram_settings(bot_token="456:second")
        self.assertFalse(replaced["paired"])
        self.assertEqual(
            db.get_telegram_settings(include_secret=True)["bot_token"],
            "456:second",
        )
        removed = db.update_telegram_settings(clear_token=True)
        self.assertFalse(removed["enabled"])
        self.assertFalse(removed["token_configured"])

    def test_heartbeat_setting_is_disabled_by_default_and_persisted(self):
        db.init()
        initial = db.get_heartbeat_settings()
        self.assertEqual(initial["enabled"], False)
        self.assertEqual(initial["interval_minutes"], 30)
        self.assertEqual(initial["last_status"], "never")

        updated = db.update_heartbeat_settings(
            enabled=True,
            interval_minutes=60,
            instructions="Check for important updates.",
        )

        self.assertEqual(updated["enabled"], True)
        self.assertEqual(updated["interval_minutes"], 60)
        self.assertIsNotNone(updated["next_run_at"])
        self.assertEqual(db.get_heartbeat_settings()["enabled"], True)
        with self.assertRaisesRegex(ValueError, "true or false"):
            db.update_heartbeat_settings(enabled="yes")
        with self.assertRaisesRegex(ValueError, "between 5 and 1440"):
            db.update_heartbeat_settings(interval_minutes=2)

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

    def test_voice_turn_instruction_is_mandatory_and_not_saved_in_history(self):
        db.init()
        observed = {}

        def compile_graph(stream_q, *_args, **_kwargs):
            class Graph:
                def invoke(self, state):
                    observed["user"] = next(
                        message["content"]
                        for message in reversed(state["messages"])
                        if message["role"] == "user"
                    )
                    stream_q.put("plain reply")
                    return {
                        "messages": state["messages"]
                        + [{"role": "assistant", "content": "plain reply"}]
                    }

            return Graph()

        conversation = Conversation(system_prompt="test")
        voice_agent = Agent(conversation=conversation)
        with patch.object(
            langgraph_agent, "_compile_graph", side_effect=compile_graph
        ):
            self.assertEqual(
                "".join(voice_agent.respond("tell me the time", voice=True)),
                "plain reply",
            )

        self.assertIn("MANDATORY VOICE MODE", observed["user"])
        self.assertIn("Never use Markdown", observed["user"])
        visible = conversation.display_messages()
        self.assertEqual(visible[0]["content"], "tell me the time")
        self.assertNotIn("VOICE MODE", visible[0]["content"])

    def test_heartbeat_uses_only_selected_noninteractive_cached_tools(self):
        db.init()
        email = self._create_email_fixture()
        db.save_server_tools(
            email["mcp_server_id"],
            [
                {"name": "search_emails", "description": "Read email", "input_schema": {}},
                {"name": "send_email", "description": "Send email", "input_schema": {}},
            ],
        )

        capabilities = db.get_heartbeat_capabilities()
        email_tools = {
            tool["name"]: tool
            for agent in capabilities
            if agent["id"] == email["id"]
            for tool in agent["tools"]
        }
        self.assertFalse(email_tools["search_emails"]["requires_confirmation"])
        self.assertTrue(email_tools["send_email"]["requires_confirmation"])

        with self.assertRaisesRegex(ValueError, "require confirmation"):
            db.update_heartbeat_settings(
                selected_tools=[
                    {"subagent_id": email["id"], "tool_name": "send_email"}
                ]
            )
        db.update_heartbeat_settings(
            selected_tools=[
                {"subagent_id": email["id"], "tool_name": "search_emails"}
            ]
        )
        target = next(
            item for item in db.get_heartbeat_targets() if item["id"] == email["id"]
        )
        self.assertEqual(target["allowed_tools"], ["search_emails"])

    def test_heartbeat_includes_builtins_and_defaults_to_their_safe_tools(self):
        db.init()
        capabilities = {
            agent["key"]: agent for agent in db.get_heartbeat_capabilities()
        }

        self.assertTrue(
            {"builtin:media", "builtin:knowledge", "builtin:system"}
            <= capabilities.keys()
        )
        system_tools = {
            tool["name"]: tool
            for tool in capabilities["builtin:system"]["tools"]
        }
        self.assertTrue(system_tools["system_status"]["selected"])
        self.assertFalse(system_tools["system_status"]["requires_confirmation"])
        self.assertFalse(system_tools["set_volume"]["selected"])
        self.assertTrue(system_tools["set_volume"]["requires_confirmation"])

        with self.assertRaisesRegex(ValueError, "require confirmation"):
            db.update_heartbeat_settings(
                selected_tools=[
                    {"agent_key": "builtin:system", "tool_name": "set_volume"}
                ]
            )
        db.update_heartbeat_settings(
            selected_tools=[
                {"agent_key": "builtin:system", "tool_name": "system_status"}
            ]
        )
        target = next(
            item
            for item in db.get_heartbeat_targets()
            if item["id"] == "builtin:system"
        )
        self.assertEqual(target["allowed_tools"], ["system_status"])
        saved_capabilities = {
            agent["key"]: agent for agent in db.get_heartbeat_capabilities()
        }
        self.assertFalse(
            any(
                tool["selected"]
                for tool in saved_capabilities["builtin:media"]["tools"]
            )
        )

    def test_builtin_agent_model_selection_is_persisted_and_provider_scoped(self):
        db.init()
        nvidia = db.add_model(
            "NVIDIA System",
            "nvidia/test-system-model",
            "NVIDIA",
            "https://integrate.api.nvidia.com/v1",
            "preset-key",
        )
        ollama = db.add_model(
            "Local model",
            "qwen-local",
            "Ollama",
            "http://localhost:11434/v1",
            "",
        )

        system = next(
            agent for agent in db.list_builtin_agents() if agent["key"] == "system"
        )
        options = {option["id"] for option in system["model_options"]}
        self.assertIn(nvidia["id"], options)
        self.assertNotIn(ollama["id"], options)

        updated = db.update_builtin_agent_model("system", nvidia["id"])
        self.assertEqual(updated["model"], nvidia["model"])
        self.assertEqual(updated["model_id"], nvidia["id"])
        self.assertEqual(
            db.get_builtin_agent_model("system", "fallback"), nvidia["model"]
        )
        runtime = db.get_builtin_agent_runtime(
            "system",
            fallback_model="fallback",
            fallback_base_url="https://fallback.test/v1",
            fallback_api_key="fallback-key",
        )
        self.assertEqual(runtime["base_url"], nvidia["base_url"])
        self.assertEqual(runtime["api_key"], "preset-key")
        with self.assertRaisesRegex(ValueError, "configured NVIDIA"):
            db.update_builtin_agent_model("system", ollama["id"])

    def test_fresh_database_keeps_user_registry_empty(self):
        db.init()
        self.assertEqual(db.list_models(), [])
        self.assertEqual(db.list_servers(), [])
        self.assertEqual(db.list_subagents(), [])
        self.assertIsNone(db.get_supervisor_config()["model_id"])
        self.assertEqual(db.get_supervisor_config()["model_options"], [])
        self.assertTrue(
            all(agent["model_id"] is None for agent in db.list_builtin_agents())
        )
        with self.assertRaisesRegex(ValueError, "Select a model"):
            db.add_subagent("Helper", "Test helper", "", None, None)
        model = db.add_model(
            "User model", "user/model", "OpenAI",
            "https://models.example.test/v1", "key",
        )
        with self.assertRaisesRegex(ValueError, "Select an MCP server"):
            db.add_subagent("Helper", "Test helper", "", model["id"], None)

        # A later application restart must not reinterpret this as an upgrade
        # and populate the registry.
        db.init()
        self.assertEqual(
            [item["name"] for item in db.list_models()], ["User model"]
        )
        self.assertEqual(db.list_servers(), [])
        self.assertEqual(db.list_subagents(), [])

    def test_existing_database_keeps_one_time_builtin_agent_upgrade(self):
        # Simulate a database made by the release where Email and Researcher
        # were still hard-coded and had not yet been converted.
        with db._connect() as conn:
            db._init_schema(conn)

        db.init()
        self.assertIsNotNone(db.get_subagent_by_name("Email"))
        self.assertIsNotNone(db.get_subagent_by_name("Researcher"))

        initial_agents = [item["id"] for item in db.list_subagents()]
        db.init()
        self.assertEqual(
            [item["id"] for item in db.list_subagents()], initial_agents
        )

    def test_assigned_builtin_model_is_managed_through_models_registry(self):
        db.init()
        selected = db.add_model(
            "System model", "nvidia/system", "NVIDIA",
            "https://integrate.api.nvidia.com/v1", "initial-key",
        )
        db.update_builtin_agent_model("system", selected["id"])
        system = next(
            agent for agent in db.list_builtin_agents() if agent["key"] == "system"
        )

        updated = db.update_model(
            system["model_id"],
            model="nvidia/replacement-system-model",
            base_url="https://models.example.test/v1",
            api_key="replacement-key",
        )
        self.assertEqual(updated["model"], "nvidia/replacement-system-model")
        self.assertEqual(
            db.get_builtin_agent_model("system"),
            "nvidia/replacement-system-model",
        )
        runtime = db.get_builtin_agent_runtime(
            "system",
            fallback_model="fallback",
            fallback_base_url="https://fallback.test/v1",
            fallback_api_key="fallback-key",
        )
        self.assertEqual(runtime["base_url"], "https://models.example.test/v1")
        self.assertEqual(runtime["api_key"], "replacement-key")
        self.assertFalse(db.delete_model(system["model_id"]))
        with self.assertRaisesRegex(ValueError, "must remain a NVIDIA model"):
            db.update_model(system["model_id"], provider="Ollama")

    def test_supervisor_model_is_user_created_selectable_and_used_at_runtime(self):
        db.init()
        supervisor = db.get_supervisor_config()
        self.assertIsNone(supervisor["model_id"])
        self.assertEqual(supervisor["model_options"], [])

        alternate = db.add_model(
            "Mistral Large",
            "mistral-large-latest",
            "Mistral",
            "https://mistral.example.test/v1",
            "alternate-key",
        )
        updated = db.update_supervisor_model(alternate["id"])
        self.assertEqual(updated["model_id"], alternate["id"])
        runtime = db.get_supervisor_runtime("fallback")
        self.assertEqual(runtime["model"], "mistral-large-latest")
        self.assertEqual(runtime["base_url"], "https://mistral.example.test/v1")
        self.assertEqual(runtime["api_key"], "alternate-key")
        self.assertFalse(db.delete_model(alternate["id"]))
        with self.assertRaisesRegex(ValueError, "assigned to Mounir"):
            db.update_model(alternate["id"], provider="NVIDIA")

    def test_supervisor_chat_dispatch_uses_selected_mistral_record(self):
        with patch.object(
            llm_mod, "_mistral_stream", return_value=iter(["ready"])
        ) as mistral_stream:
            output = list(
                llm_mod.chat_stream(
                    [{"role": "user", "content": "hello"}],
                    model="mistral-test",
                    provider="Mistral",
                    base_url="https://mistral.example.test/v1",
                    api_key="test-key",
                )
            )

        self.assertEqual(output, ["ready"])
        self.assertEqual(mistral_stream.call_args.kwargs["model"], "mistral-test")
        self.assertEqual(
            mistral_stream.call_args.kwargs["base_url"],
            "https://mistral.example.test/v1",
        )
        self.assertEqual(mistral_stream.call_args.kwargs["api_key"], "test-key")

        with patch("mistralai.client.Mistral") as mistral_client:
            mistral_client.return_value.chat.stream.return_value = []
            self.assertEqual(
                list(
                    llm_mod.chat_stream(
                        [{"role": "user", "content": "hello"}],
                        model="mistral-test",
                        provider="Mistral",
                        base_url="https://api.mistral.ai/v1",
                        api_key="test-key",
                    )
                ),
                [],
            )
        self.assertEqual(
            mistral_client.call_args.kwargs["server_url"],
            "https://api.mistral.ai",
        )

        with patch.object(
            llm_mod, "_ollama_stream", return_value=iter(["cloud-ready"])
        ) as ollama_stream:
            output = list(
                llm_mod.chat_stream(
                    [{"role": "user", "content": "hello"}],
                    model="cloud-model",
                    provider="Ollama Cloud",
                    base_url="https://ollama.com/v1",
                    api_key="ollama-key",
                )
            )
        self.assertEqual(output, ["cloud-ready"])
        self.assertEqual(ollama_stream.call_args.kwargs["api_key"], "ollama-key")

    def test_builtin_specialist_enforces_heartbeat_tool_allowlist(self):
        calls = []
        replies = iter(
            [
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "set_volume",
                                "arguments": '{"action":"mute"}',
                            },
                        }
                    ],
                },
                {"content": "Nothing needs attention.", "tool_calls": []},
            ]
        )

        def fake_chat(messages, tools=None, model=None, **kwargs):
            calls.append({"tools": tools, "model": model, **kwargs})
            return next(replies)

        with (
            patch.object(config, "NVIDIA_API_KEY", "test-key"),
            patch.object(system_agent, "_context", return_value="test device"),
            patch.object(system_agent.llm, "nvidia_chat", side_effect=fake_chat),
            patch.object(system_agent, "_dispatch") as dispatch,
        ):
            report = system_agent.run(
                "Check the computer.", allowed_tools=["system_status"]
            )

        self.assertEqual(report, "Nothing needs attention.")
        self.assertEqual(
            [schema["function"]["name"] for schema in calls[0]["tools"]],
            ["system_status"],
        )
        self.assertEqual(calls[0]["model"], config.SYSTEM_MODEL)
        dispatch.assert_not_called()

    def test_heartbeat_runner_dispatches_selected_builtin(self):
        db.init()
        db.update_heartbeat_settings(
            selected_tools=[
                {"agent_key": "builtin:media", "tool_name": "find_media"}
            ]
        )
        with patch.object(
            heartbeat_mod.builtin_agents, "run", return_value="HEARTBEAT_OK"
        ) as run_builtin:
            self.assertEqual(heartbeat_mod.run_once(), ("quiet", ""))

        run_builtin.assert_called_once()
        self.assertEqual(run_builtin.call_args.args[0], "media")
        self.assertEqual(run_builtin.call_args.args[2], ["find_media"])

    def test_heartbeat_notifications_only_include_alerts(self):
        db.init()
        quiet_run = db.begin_heartbeat_run("scheduled")
        db.finish_heartbeat_run(quiet_run, status="quiet")
        alert_run = db.begin_heartbeat_run("scheduled")
        db.finish_heartbeat_run(
            alert_run,
            status="alert",
            message="An important update is available.",
        )
        error_run = db.begin_heartbeat_run("manual")
        db.finish_heartbeat_run(error_run, status="error", error="Unavailable")

        notifications = db.list_heartbeat_notifications()

        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["id"], alert_run)
        self.assertEqual(
            notifications[0]["message"], "An important update is available."
        )

    def test_heartbeat_runner_suppresses_quiet_checks_and_persists_alerts(self):
        db.init()
        email = self._create_email_fixture()
        db.save_server_tools(
            email["mcp_server_id"],
            [{"name": "search_emails", "description": "Read email", "input_schema": {}}],
        )
        db.update_heartbeat_settings(
            instructions="Tell me about urgent unread email.",
            selected_tools=[
                {"subagent_id": email["id"], "tool_name": "search_emails"}
            ],
        )

        with patch.object(heartbeat_mod, "run_mcp_agent", return_value="HEARTBEAT_OK"):
            self.assertEqual(heartbeat_mod.run_once(), ("quiet", ""))
        with patch.object(
            heartbeat_mod,
            "run_mcp_agent",
            return_value="An urgent unread message arrived from Lina.",
        ):
            status, message = heartbeat_mod.run_once()
        self.assertEqual(status, "alert")
        self.assertIn("Email: An urgent unread message", message)
        with patch.object(
            heartbeat_mod,
            "run_mcp_agent",
            return_value="An urgent unread message arrived from Lina.",
        ):
            self.assertEqual(heartbeat_mod.run_once(), ("quiet", ""))

        notices = []

        async def exercise_service():
            async def notify(text):
                notices.append(text)

            service = heartbeat_mod.HeartbeatService(
                notify, runner=lambda: ("alert", "Important update")
            )
            state = await service.run_now()
            self.assertEqual(state["last_status"], "alert")

        asyncio.run(exercise_service())
        self.assertEqual(notices, ["Important update"])
        self.assertEqual(db.list_heartbeat_runs()[0]["status"], "alert")

        db.update_heartbeat_settings(enabled=True, interval_minutes=30)
        with db._connect() as conn:
            conn.execute(
                "UPDATE heartbeat_settings SET next_run_at = ? WHERE id = 1",
                ("2000-01-01T00:00:00+00:00",),
            )
            conn.commit()
        scheduled_calls = []

        async def exercise_scheduler():
            async def notify(_text):
                return None

            service = heartbeat_mod.HeartbeatService(
                notify,
                runner=lambda: (scheduled_calls.append(True) or ("quiet", "")),
            )
            await service.start()
            for _ in range(20):
                if scheduled_calls:
                    break
                await asyncio.sleep(0.01)
            await service.stop()

        asyncio.run(exercise_scheduler())
        self.assertEqual(scheduled_calls, [True])
        self.assertEqual(db.list_heartbeat_runs()[0]["trigger"], "scheduled")

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
                CREATE TABLE heartbeat_settings (
                    id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT
                );
                INSERT INTO heartbeat_settings (id, enabled) VALUES (1, 0);
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
            tool_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'mcp_server_tools'"
            ).fetchone()
            builtin_settings_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'builtin_agent_settings'"
            ).fetchone()
            supervisor_settings_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'supervisor_settings'"
            ).fetchone()
            voice_settings_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'voice_settings'"
            ).fetchone()
            heartbeat_tables = {
                row["name"]
                for row in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name LIKE 'heartbeat_%'
                    """
                )
            }
            heartbeat_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(heartbeat_settings)")
            }
        self.assertTrue(
            {
                "description", "setup_type", "transport", "headers", "env",
                "auth_scheme",
            }
            <= server_columns
        )
        self.assertTrue(
            {
                "description", "icon_data", "icon_mime",
                "confirm_tool_calls", "confirm_tools", "dedupe_tools", "enabled",
            }
            <= agent_columns
        )
        self.assertIsNotNone(tool_table)
        self.assertIsNotNone(builtin_settings_table)
        self.assertIsNotNone(supervisor_settings_table)
        self.assertIsNotNone(voice_settings_table)
        self.assertTrue(
            {
                "heartbeat_settings", "heartbeat_tools", "heartbeat_runs",
                "heartbeat_agent_state", "heartbeat_builtin_tools",
                "heartbeat_builtin_agent_state", "heartbeat_agent_preferences",
            }
            <= heartbeat_tables
        )
        self.assertTrue(
            {
                "interval_minutes", "instructions", "next_run_at",
                "last_run_at", "last_status", "last_message", "last_error",
            }
            <= heartbeat_columns
        )
        self.assertTrue(
            {"connection_status", "last_tested_at", "last_error"}
            <= server_columns
        )
        self.assertEqual(db.get_server(1)["transport"], "streamable_http")
        self.assertEqual(db.get_model(1)["model"], "qwen3:4b")
        self.assertEqual(db.get_model(1)["base_url"], "http://localhost:11434/v1")
        self.assertEqual(json.loads(db.get_subagent(1)["confirm_tools"]), [])
        self.assertEqual(db.get_subagent(1)["enabled"], 1)
        self.assertTrue(
            all(agent["enabled"] for agent in db.list_builtin_agents())
        )
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

    def test_dynamic_subagent_activation_controls_runtime_and_mcp_connection(self):
        db.init()
        model = db.add_model(
            "Activation model", "activation/model", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Activation server", "fake-mcp-server")
        active = db.add_subagent(
            "Active helper", "Handles active tasks.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        disabled = db.add_subagent(
            "Disabled helper", "Handles disabled tasks.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        stale_spec = next(
            spec for spec in db.build_specs() if spec["id"] == disabled["id"]
        )

        updated = db.update_subagent(disabled["id"], enabled=False)
        self.assertEqual(updated["enabled"], 0)
        self.assertFalse(db.is_subagent_enabled(disabled["id"]))
        self.assertEqual(
            [spec["id"] for spec in db.build_specs()], [active["id"]]
        )

        graph_nodes = set(langgraph_agent.build_graph().get_graph().nodes)
        self.assertIn("mcp_active_helper", graph_nodes)
        self.assertNotIn("mcp_disabled_helper", graph_nodes)
        with patch.object(mcp_agent, "_run_async") as connect:
            result = mcp_agent.run("Do the task", stale_spec)
        self.assertIn("inactive", result)
        connect.assert_not_called()

        restored = db.update_subagent(disabled["id"], enabled=True)
        self.assertEqual(restored["enabled"], 1)
        self.assertTrue(db.is_subagent_enabled(disabled["id"]))
        self.assertIn(
            "mcp_disabled_helper",
            set(langgraph_agent.build_graph().get_graph().nodes),
        )

    def test_builtin_activation_removes_delegate_and_blocks_direct_runs(self):
        db.init()
        updated = db.update_builtin_agent("media", enabled=False)
        self.assertFalse(updated["enabled"])
        self.assertFalse(db.is_builtin_agent_enabled("media"))
        self.assertNotIn(
            "media", set(langgraph_agent.build_graph().get_graph().nodes)
        )

        advertised = []

        def fake_chat_stream(messages, tools=None, **kwargs):
            advertised.extend(
                schema["function"]["name"] for schema in (tools or [])
            )
            yield "ok"

        with (
            patch.object(langgraph_agent.llm, "chat_stream", fake_chat_stream),
            patch.object(langgraph_agent, "run_media") as run_media,
        ):
            list(langgraph_agent.Agent().respond("What tools are available?"))
            self.assertIn("inactive", mounir_tools.delegate_to_media("read x"))
        self.assertNotIn("delegate_to_media", advertised)
        run_media.assert_not_called()
        with self.assertRaisesRegex(ValueError, "inactive"):
            heartbeat_mod.builtin_agents.run(
                "media", "Inspect media", ["find_media"]
            )
        stale_state = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "stale_media",
                            "function": {
                                "name": "delegate_to_media",
                                "arguments": '{"task":"Inspect media"}',
                            },
                        }
                    ],
                }
            ]
        }
        with patch.object(langgraph_agent, "run_media") as stale_media:
            langgraph_agent._media(stale_state)
        stale_media.assert_not_called()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            langgraph_agent.db.update_heartbeat_settings(
                selected_tools=[
                    {"agent_key": "builtin:media", "tool_name": "find_media"}
                ]
            )

        db.update_builtin_agent("media", enabled=True)
        self.assertTrue(db.is_builtin_agent_enabled("media"))
        self.assertIn("media", set(langgraph_agent.build_graph().get_graph().nodes))

    def test_inactive_dynamic_subagent_is_excluded_from_heartbeat(self):
        db.init()
        agent = self._create_email_fixture()
        db.save_server_tools(
            agent["mcp_server_id"],
            [{"name": "search_emails", "description": "Read email", "input_schema": {}}],
        )
        selection = [
            {"subagent_id": agent["id"], "tool_name": "search_emails"}
        ]
        db.update_heartbeat_settings(selected_tools=selection)
        self.assertEqual(len(db.get_heartbeat_targets()), 1)

        db.update_subagent(agent["id"], enabled=False)
        self.assertFalse(
            any(
                item["key"] == f"mcp:{agent['id']}"
                for item in db.get_heartbeat_capabilities()
            )
        )
        self.assertEqual(db.get_heartbeat_targets(), [])
        with self.assertRaisesRegex(ValueError, "unavailable"):
            db.update_heartbeat_settings(selected_tools=selection)

        db.update_subagent(agent["id"], enabled=True)
        self.assertEqual(len(db.get_heartbeat_targets()), 1)

    def test_fresh_graph_has_no_automatic_dynamic_agents(self):
        from mounir.langgraph_agent import build_graph

        db.init()
        graph = build_graph()
        node_names = set(graph.get_graph().nodes)
        self.assertNotIn("mcp_email", node_names)
        self.assertNotIn("mcp_researcher", node_names)
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
            headers={"Authorization": "Bearer token"},
            auth_scheme="bearer",
        )
        updated = db.update_server(server["id"], name="Renamed")
        self.assertEqual(updated["transport"], "streamable_http")
        self.assertEqual(updated["connection"], "https://example.test/mcp")
        self.assertEqual(updated["auth_scheme"], "bearer")

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
    def test_restricted_mcp_run_hides_and_blocks_unselected_tools(self):
        called = []

        class Tool:
            def __init__(self, name):
                self.name = name
                self.description = name
                self.inputSchema = {"type": "object", "properties": {}}

        class Session:
            async def list_tools(self, cursor=None):
                return type(
                    "Page",
                    (),
                    {"tools": [Tool("safe_read"), Tool("dangerous_write")], "nextCursor": None},
                )()

            async def call_tool(self, name, args):
                called.append((name, args))
                return type("Result", (), {"content": [], "isError": False})()

        @asynccontextmanager
        async def fake_session(_spec):
            yield Session()

        responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_0",
                        "function": {
                            "name": "dangerous_write",
                            "arguments": "{}",
                        },
                    }
                ],
            },
            {"content": "HEARTBEAT_OK", "tool_calls": []},
        ]
        spec = {
            "name": "Restricted helper",
            "prompt": "",
            "model": "test-model",
            "base_url": "http://localhost/v1",
            "connection": "fake",
            "confirm_tools": [],
            "dedupe_tools": [],
            "allowed_tools": ["safe_read"],
        }
        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.llm, "openai_chat", side_effect=responses) as chat,
        ):
            result = asyncio.run(mcp_agent._run_async("check", spec, ""))

        self.assertEqual(result, "HEARTBEAT_OK")
        self.assertEqual(called, [])
        advertised = chat.call_args_list[0].kwargs["tools"]
        self.assertEqual(
            [tool["function"]["name"] for tool in advertised], ["safe_read"]
        )

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
    def test_channel_histories_are_isolated_and_receive_selected_heartbeat(self):
        import server as web_server

        db.init()
        db.update_telegram_settings(enabled=True, bot_token="123:secret")
        db.pair_telegram_chat(42, "Ada", "ada")
        db.update_whatsapp_settings(
            enabled=True,
            access_token="whatsapp-token",
            phone_number_id="phone-id",
            business_account_id="business-id",
            app_secret="app-secret",
        )
        db.pair_whatsapp_phone("21611111111", "Ada")
        db.update_heartbeat_settings(notify_telegram=True, notify_whatsapp=True)
        web_server.agent.conversation.reset()
        web_server.telegram_agent.conversation.reset()
        web_server.whatsapp_agent.conversation.reset()

        web_server.agent.conversation.add_user("Web-only context")
        self.assertEqual(len(web_server.agent.conversation), 1)
        self.assertEqual(len(web_server.telegram_agent.conversation), 0)
        self.assertEqual(len(web_server.whatsapp_agent.conversation), 0)
        self.assertIsNot(web_server.agent, web_server.telegram_service.agent)
        self.assertIsNot(web_server.agent, web_server.whatsapp_service.agent)
        self.assertIsNot(
            web_server.telegram_service.agent, web_server.whatsapp_service.agent
        )

        web_server.agent.conversation.reset()
        async def run_inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        with (
            patch.object(
                web_server.telegram_service,
                "send_notification",
                return_value=True,
            ) as send_notification,
            patch.object(
                web_server.whatsapp_service,
                "send_notification",
                return_value=True,
            ) as send_whatsapp_notification,
            patch.object(web_server.asyncio, "to_thread", side_effect=run_inline),
        ):
            asyncio.run(web_server._deliver_heartbeat_alert("Important update"))

        send_notification.assert_called_once_with(
            "Heartbeat update\n\nImportant update"
        )
        send_whatsapp_notification.assert_called_once_with(
            "Heartbeat update\n\nImportant update"
        )
        expected = [{
            "role": "assistant",
            "content": "Heartbeat update\n\nImportant update",
        }]
        self.assertEqual(web_server.agent.conversation.display_messages(), expected)
        self.assertEqual(
            web_server.telegram_agent.conversation.display_messages(), expected
        )
        self.assertEqual(
            web_server.whatsapp_agent.conversation.display_messages(), expected
        )
        web_server.agent.conversation.reset()
        web_server.telegram_agent.conversation.reset()
        web_server.whatsapp_agent.conversation.reset()

    def test_heartbeat_notifications_endpoint_returns_saved_alerts(self):
        import httpx
        import server as web_server

        db.init()
        run_id = db.begin_heartbeat_run("scheduled")
        db.finish_heartbeat_run(
            run_id,
            status="alert",
            message="A saved heartbeat notification.",
        )

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                response = await client.get("/api/heartbeat/notifications")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["notifications"][0]["message"],
                    "A saved heartbeat notification.",
                )

        asyncio.run(exercise_api())

    def test_admin_flow_persists_transport_and_tests_connection(self):
        import httpx
        import server as web_server

        db.init()
        with db._connect() as conn:
            email_model_id = db._add_model(
                conn, "Email test model", "email-test", "Ollama",
                "http://localhost:11434/v1", "",
            )
            email_server_id = db._add_server(
                conn,
                "Gmail MCP",
                "npx -y test-gmail-mcp",
                description="Test Gmail server",
                setup_type="gmail_oauth",
            )
            db._add_subagent(
                conn, "Email", "Handles test email.", "",
                email_model_id, email_server_id,
                confirm_tools=["send_email", "delete_email"],
                dedupe_tools=["send_email"],
            )
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
                remote_auth = await client.post(
                    "/api/mcp-servers",
                    json={
                        "name": "Remote auth",
                        "transport": "streamable_http",
                        "connection": "https://example.test/mcp",
                        "headers": '{"Authorization":"Bearer github-pat"}',
                        "auth_scheme": "bearer",
                    },
                )
                self.assertEqual(remote_auth.status_code, 200)
                self.assertEqual(remote_auth.json()["auth_scheme"], "bearer")
                named_header = await client.put(
                    f"/api/mcp-servers/{remote_auth.json()['id']}",
                    json={
                        "headers": '{"X-API-Key":"service-key"}',
                        "auth_scheme": "header",
                    },
                )
                self.assertEqual(named_header.status_code, 200)
                self.assertEqual(named_header.json()["auth_scheme"], "header")
                self.assertEqual(
                    json.loads(named_header.json()["headers"]),
                    {"X-API-Key": "service-key"},
                )
                untouched_tools = await client.get(
                    f"/api/mcp-servers/{server_response.json()['id']}/tools"
                )
                self.assertEqual(untouched_tools.status_code, 200)
                self.assertEqual(untouched_tools.json()["status"], "untested")
                self.assertEqual(untouched_tools.json()["tools"], [])
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
                            "input_schema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ],
                )
                self.assertEqual(test_response.json()["status"], "connected")
                cached_tools = await client.get(
                    f"/api/mcp-servers/{server_response.json()['id']}/tools"
                )
                self.assertEqual(cached_tools.status_code, 200)
                self.assertEqual(cached_tools.json()["tools"], test_response.json()["tools"])

                stale_server = await client.put(
                    f"/api/mcp-servers/{server_response.json()['id']}",
                    json={"env": '{"ECHO_TOKEN":"changed"}'},
                )
                self.assertEqual(stale_server.status_code, 200)
                self.assertEqual(stale_server.json()["connection_status"], "stale")
                stale_tools = await client.get(
                    f"/api/mcp-servers/{server_response.json()['id']}/tools"
                )
                self.assertEqual(stale_tools.json()["status"], "stale")
                self.assertEqual(stale_tools.json()["tools"], test_response.json()["tools"])

                with patch.object(
                    web_server, "discover_tools", side_effect=RuntimeError("server offline")
                ):
                    failed_test = await client.post(
                        f"/api/mcp-servers/{server_response.json()['id']}/test"
                    )
                self.assertEqual(failed_test.status_code, 400)
                failed_state = await client.get(
                    f"/api/mcp-servers/{server_response.json()['id']}/tools"
                )
                self.assertEqual(failed_state.json()["status"], "failed")
                self.assertEqual(failed_state.json()["tools"], test_response.json()["tools"])
                overview_response = await client.get("/api/agent-overview")
                self.assertEqual(overview_response.status_code, 200)
                overview = overview_response.json()
                self.assertEqual(overview["supervisor"]["name"], "Mounir")
                self.assertEqual(len(overview["builtins"]), 3)
                self.assertTrue(overview["supervisor"]["model"])
                self.assertTrue(overview["supervisor"]["provider"])
                self.assertTrue(overview["supervisor"]["description"])
                supervisor_tools = {
                    tool["name"] for tool in overview["supervisor"]["tools"]
                }
                self.assertIn("read_file", supervisor_tools)
                self.assertFalse(
                    any(name.startswith("delegate_to_") for name in supervisor_tools)
                )
                self.assertTrue(overview["supervisor"]["model_options"])

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

                heartbeat_response = await client.get("/api/heartbeat")
                self.assertEqual(heartbeat_response.status_code, 200)
                self.assertEqual(heartbeat_response.json()["enabled"], False)
                protected_heartbeat = await client.put(
                    "/api/heartbeat",
                    json={
                        "enabled": False,
                        "selected_tools": [
                            {
                                "subagent_id": agent_response.json()["id"],
                                "tool_name": "echo",
                            }
                        ],
                    },
                )
                self.assertEqual(protected_heartbeat.status_code, 400)
                safe_agent = await client.put(
                    f"/api/subagents/{agent_response.json()['id']}",
                    json={"confirm_tools": []},
                )
                self.assertEqual(safe_agent.status_code, 200)
                heartbeat_update = await client.put(
                    "/api/heartbeat",
                    json={
                        "enabled": True,
                        "interval_minutes": 15,
                        "instructions": "Check the echo service for important updates.",
                        "selected_tools": [
                            {
                                "subagent_id": agent_response.json()["id"],
                                "tool_name": "echo",
                            }
                        ],
                    },
                )
                self.assertEqual(heartbeat_update.status_code, 200)
                self.assertEqual(heartbeat_update.json()["enabled"], True)
                self.assertEqual(heartbeat_update.json()["interval_minutes"], 15)
                echo_capability = next(
                    item
                    for item in heartbeat_update.json()["capabilities"]
                    if item["id"] == agent_response.json()["id"]
                )
                self.assertTrue(echo_capability["tools"][0]["selected"])
                with patch.object(
                    web_server.heartbeat_service,
                    "_runner",
                    return_value=("quiet", ""),
                ):
                    heartbeat_run = await client.post("/api/heartbeat/run")
                self.assertEqual(heartbeat_run.status_code, 200)
                self.assertEqual(heartbeat_run.json()["last_status"], "quiet")
                self.assertEqual(
                    heartbeat_run.json()["recent_runs"][0]["trigger"], "manual"
                )
                heartbeat_notifications = await client.get(
                    "/api/heartbeat/notifications"
                )
                self.assertEqual(heartbeat_notifications.status_code, 200)
                self.assertEqual(
                    heartbeat_notifications.json(), {"notifications": []}
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


    def test_admin_api_toggles_dynamic_and_builtin_availability(self):
        import httpx
        import server as web_server

        db.init()
        model = db.add_model(
            "Toggle model", "toggle/model", "Ollama",
            "http://localhost:11434/v1", "",
        )
        mcp_server = db.add_server("Toggle server", "fake-mcp-server")
        subagent = db.add_subagent(
            "Toggle helper", "Handles toggle tests.", "",
            model["id"], mcp_server["id"],
        )

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                disabled = await client.put(
                    f"/api/subagents/{subagent['id']}",
                    json={"enabled": False},
                )
                self.assertEqual(disabled.status_code, 200)
                self.assertEqual(disabled.json()["enabled"], 0)
                listed = await client.get("/api/subagents")
                self.assertEqual(len(listed.json()), 1)
                self.assertEqual(listed.json()[0]["enabled"], 0)

                invalid = await client.put(
                    f"/api/subagents/{subagent['id']}",
                    json={"enabled": "sometimes"},
                )
                self.assertEqual(invalid.status_code, 400)

                builtin = await client.put(
                    "/api/builtin-agents/media", json={"enabled": False}
                )
                self.assertEqual(builtin.status_code, 200)
                self.assertFalse(builtin.json()["enabled"])
                overview = await client.get("/api/agent-overview")
                media = next(
                    item for item in overview.json()["builtins"]
                    if item["key"] == "media"
                )
                self.assertFalse(media["enabled"])

                restored = await client.put(
                    f"/api/subagents/{subagent['id']}",
                    json={"enabled": True},
                )
                self.assertEqual(restored.json()["enabled"], 1)

        asyncio.run(exercise_api())


    def test_telegram_admin_setup_never_returns_secret(self):
        import httpx
        import server as web_server

        db.init()

        def fake_apply():
            saved = db.get_telegram_settings(include_secret=True)
            web_server.telegram_service.token = saved["bot_token"]
            web_server.telegram_service.chat_id = saved["chat_id"]
            web_server.telegram_service.last_error = ""
            return web_server._telegram_public_state()

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            with (
                patch.object(
                    type(web_server.telegram_service),
                    "is_running",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch.object(web_server, "_apply_telegram_settings", side_effect=fake_apply),
                patch.object(
                    web_server.telegram_service,
                    "test_connection",
                    return_value={"username": "mounir_bot", "first_name": "Mounir", "id": 7},
                ),
                patch.object(
                    web_server.telegram_service,
                    "create_pairing_code",
                    return_value={
                        "code": "123456",
                        "command": "/pair 123456",
                        "expires_at": "2030-01-01T00:00:00+00:00",
                    },
                ),
            ):
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://localhost"
                ) as client:
                    initial = await client.get("/api/telegram")
                    self.assertEqual(initial.status_code, 200)
                    self.assertNotIn("bot_token", initial.json())
                    self.assertNotIn("chat_id", initial.json())

                    saved = await client.put(
                        "/api/telegram",
                        json={"bot_token": "123:secret", "enabled": True},
                    )
                    self.assertEqual(saved.status_code, 200)
                    self.assertTrue(saved.json()["token_configured"])
                    self.assertNotIn("123:secret", saved.text)

                    tested = await client.post("/api/telegram/test")
                    self.assertEqual(tested.status_code, 200)
                    self.assertEqual(tested.json()["bot_username"], "mounir_bot")
                    self.assertNotIn("123:secret", tested.text)

                    pairing = await client.post("/api/telegram/pairing-code")
                    self.assertEqual(pairing.status_code, 200)
                    self.assertEqual(pairing.json()["command"], "/pair 123456")

                    db.pair_telegram_chat(42, "Ada", "ada")
                    connected = await client.get("/api/telegram")
                    self.assertTrue(connected.json()["paired"])
                    self.assertEqual(connected.json()["chat_name"], "Ada")
                    self.assertNotIn("chat_id", connected.json())

                    disconnected = await client.delete("/api/telegram/pairing")
                    self.assertEqual(disconnected.status_code, 200)
                    self.assertFalse(disconnected.json()["paired"])

                    removed = await client.delete("/api/telegram/token")
                    self.assertEqual(removed.status_code, 200)
                    self.assertFalse(removed.json()["token_configured"])
                    self.assertFalse(removed.json()["enabled"])

        asyncio.run(exercise_api())


class InterfaceRoutingTests(unittest.TestCase):
    def test_server_lifespan_owns_telegram_bridge(self):
        import server as web_server

        events = []

        async def heartbeat_start():
            events.append("heartbeat-start")

        async def heartbeat_stop():
            events.append("heartbeat-stop")

        def telegram_start():
            events.append("telegram-start")
            return True

        def telegram_stop():
            events.append("telegram-stop")

        async def exercise_lifespan():
            with (
                patch.object(
                    web_server.db,
                    "get_telegram_settings",
                    return_value={"enabled": True, "bot_token": "123:abc"},
                ),
                patch.object(web_server.heartbeat_service, "start", heartbeat_start),
                patch.object(web_server.heartbeat_service, "stop", heartbeat_stop),
                patch.object(web_server.telegram_service, "start_background", telegram_start),
                patch.object(web_server.telegram_service, "stop", telegram_stop),
            ):
                async with web_server._lifespan(web_server.app):
                    events.append("serving")

        asyncio.run(exercise_lifespan())
        self.assertEqual(
            events,
            [
                "heartbeat-start",
                "telegram-start",
                "serving",
                "telegram-stop",
                "heartbeat-stop",
            ],
        )

    def test_confirmation_handler_reaches_the_agent_graph_worker(self):
        observed = []

        def compile_graph(stream_q, *_args, **_kwargs):
            class Graph:
                def invoke(self, state):
                    observed.append(mounir_tools.request_confirmation("safe?"))
                    stream_q.put("done")
                    return {
                        "messages": state["messages"] + [
                            {"role": "assistant", "content": "done"}
                        ]
                    }

            return Graph()

        agent = Agent(conversation=Conversation(system_prompt="test"))
        with (
            patch.object(langgraph_agent, "_compile_graph", side_effect=compile_graph),
            patch.object(mounir_tools, "confirm_fn", return_value=False),
            mounir_tools.use_confirmation_handler(lambda _action: True),
        ):
            reply = "".join(agent.respond("hello"))

        self.assertEqual(reply, "done")
        self.assertEqual(observed, [True])

    def test_telegram_turn_uses_telegram_confirmation_handler(self):
        db.init()

        class FakeBot:
            def __init__(self):
                self.handlers = []
                self.sent = []

            def register_message_handler(self, handler, **filters):
                self.handlers.append((handler, filters))

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))

            def reply_to(self, message, text):
                self.sent.append((message.chat.id, text, {}))

            def send_chat_action(self, *_args, **_kwargs):
                return None

            def stop_polling(self):
                return None

        class FakeAgent:
            def __init__(self):
                self.conversation = Conversation(system_prompt="test")
                self.requests = []

            def respond(self, text):
                self.requests.append(text)
                self.confirmed = mounir_tools.request_confirmation("send email?")
                yield "Telegram reply"

        fake_bot = FakeBot()
        fake_agent = FakeAgent()
        bridge = TelegramBridge(
            agent=fake_agent,
            turn_lock=threading.Lock(),
            token="123:abc",
            chat_id="42",
            bot_factory=lambda _token: fake_bot,
        )
        message = SimpleNamespace(chat=SimpleNamespace(id=42), text="hello")

        with (
            patch.object(mounir_tools, "confirm_fn", return_value=False) as fallback,
            patch.object(bridge, "_telegram_confirm", return_value=True) as confirm,
        ):
            bridge._handle_text(message)
            outside_turn = mounir_tools.request_confirmation("outside")

        self.assertEqual(fake_agent.requests, ["hello"])
        self.assertTrue(fake_agent.confirmed)
        confirm.assert_called_once_with("send email?")
        self.assertFalse(outside_turn)
        fallback.assert_called_once_with("outside")
        self.assertEqual(fake_bot.sent[-1][1], "Telegram reply")

    def test_telegram_can_send_a_proactive_notification(self):
        class FakeBot:
            def __init__(self):
                self.sent = []

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))

        fake_bot = FakeBot()
        bridge = TelegramBridge(
            token="123:abc",
            chat_id="42",
            bot_factory=lambda _token: fake_bot,
        )

        self.assertTrue(bridge.send_notification("Heartbeat update"))
        self.assertEqual(fake_bot.sent[0][0:2], (42, "Heartbeat update"))

        unpaired = TelegramBridge(
            token="123:abc",
            chat_id="",
            bot_factory=lambda _token: fake_bot,
        )
        self.assertFalse(unpaired.send_notification("Heartbeat update"))

    def test_telegram_stops_polling_when_token_is_rejected(self):
        class UnauthorizedError(Exception):
            error_code = 401

        class FakeBot:
            def __init__(self):
                self.infinity_started = False
                self.stopped = False

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def get_me(self):
                raise UnauthorizedError("Unauthorized")

            def infinity_polling(self, **_kwargs):
                self.infinity_started = True

            def stop_polling(self):
                self.stopped = True

        statuses = []
        fake_bot = FakeBot()
        bridge = TelegramBridge(
            token="invalid-token",
            chat_id="42",
            bot_factory=lambda _token: fake_bot,
            on_status=lambda status, _username, error: statuses.append(
                (status, error)
            ),
        )

        bridge._poll()

        self.assertFalse(fake_bot.infinity_started)
        self.assertTrue(fake_bot.stopped)
        self.assertEqual(statuses[-1][0], "error")
        self.assertIn("Replace it in Agent Studio", statuses[-1][1])

    def test_telegram_pairing_code_is_one_use_and_records_identity(self):
        db.init()

        class FakeBot:
            def __init__(self):
                self.sent = []

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def send_message(self, chat_id, text, **_kwargs):
                self.sent.append((chat_id, text))

            def reply_to(self, message, text):
                self.sent.append((message.chat.id, text))

            def stop_polling(self):
                return None

        paired = []
        fake_bot = FakeBot()
        bridge = TelegramBridge(
            agent=SimpleNamespace(conversation=Conversation(system_prompt="test")),
            token="123:abc",
            chat_id="",
            bot_factory=lambda _token: fake_bot,
            on_paired=lambda chat_id, name, username: paired.append(
                (chat_id, name, username)
            ),
        )
        with patch("mounir.telegram_bridge.secrets.randbelow", return_value=123456):
            pairing = bridge.create_pairing_code()
        message = SimpleNamespace(
            chat=SimpleNamespace(id=42),
            from_user=SimpleNamespace(
                first_name="Ada", last_name="Lovelace", username="ada"
            ),
            text=pairing["command"],
        )

        bridge._handle_text(message)
        bridge._handle_text(message)

        self.assertEqual(bridge.chat_id, "42")
        self.assertEqual(paired, [(42, "Ada Lovelace", "ada")])
        self.assertIn("now connected", fake_bot.sent[0][1])
        self.assertIn("invalid or expired", fake_bot.sent[1][1])

    def test_telegram_without_token_does_not_start(self):
        bridge = TelegramBridge(token="", chat_id="")
        self.assertFalse(bridge.start_background())
        self.assertIn("bot token", bridge.last_error)


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
