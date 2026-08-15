from __future__ import annotations

import asyncio
import base64
import io
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
from unittest.mock import Mock, PropertyMock, patch

# Keep tests away from the owner's real ~/.mounir database.
_IMPORT_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["MOUNIR_DATA_DIR"] = _IMPORT_DATA_DIR.name
os.environ["MOUNIR_TELEGRAM_ENABLED"] = "false"
os.environ.pop("TELEGRAM_BOT_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)

from mounir import (
    action_decline,
    browser_control,
    config,
    db,
    graph_runtime,
    heartbeat as heartbeat_mod,
    langgraph_agent,
    llm as llm_mod,
    mcp_agents,
    mcp_oauth,
    tools as mounir_tools,
)
from mounir.agent import Agent
from mounir.memory import Conversation
from mounir.specialists import mcp_agent
from mounir.specialists import system as system_agent
from mounir.specialists.mcp_agent import _call, _mcp_session, _system_prompt, discover_tools
from mounir.telegram_bridge import TelegramBridge


def _create_moss_package_fixture(
    engine: Path, package_name: str, voices: list[dict]
) -> None:
    package = engine / "models" / package_name
    codec = engine / "models" / "fixture-codec"
    package.mkdir(parents=True)
    codec.mkdir(parents=True)
    (engine / "ort_cpu_runtime.py").write_text("# fixture runtime\n", encoding="utf-8")
    (package / "tokenizer.model").write_bytes(b"tokenizer")
    (package / "tts.onnx").write_bytes(b"onnx")
    (package / "tts.data").write_bytes(b"weights")
    (codec / "codec.onnx").write_bytes(b"onnx")
    (codec / "codec.data").write_bytes(b"weights")
    (package / "tts_meta.json").write_text(
        json.dumps(
            {
                "files": {"prefill": "tts.onnx"},
                "external_data_files": {"tts.onnx": ["tts.data"]},
            }
        ),
        encoding="utf-8",
    )
    (codec / "codec_meta.json").write_text(
        json.dumps(
            {
                "files": {"decode": "codec.onnx"},
                "external_data_files": {"codec.onnx": ["codec.data"]},
            }
        ),
        encoding="utf-8",
    )
    (package / "browser_poc_manifest.json").write_text(
        json.dumps(
            {
                "model_files": {
                    "tts_meta": "tts_meta.json",
                    "codec_meta": "../fixture-codec/codec_meta.json",
                    "tokenizer_model": "tokenizer.model",
                },
                "builtin_voices": voices,
            }
        ),
        encoding="utf-8",
    )


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
                "provider": "openai_compatible",
                "model": "whisper-large-v3-turbo",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": "groq-voice-key",
                "language": "auto",
            },
            tts={
                "provider": "openai_compatible",
                "model": "speech-model",
                "voice": "voice-id",
                "base_url": "https://speech.example.test/v1",
                "api_key": "speech-voice-key",
                "language": "auto",
            },
        )

        self.assertNotIn("api_key", saved["stt"])
        self.assertNotIn("api_key", saved["tts"])
        self.assertTrue(saved["stt"]["api_key_configured"])
        self.assertTrue(saved["tts"]["api_key_configured"])
        self.assertEqual(db.get_voice_runtime("stt")["api_key"], "groq-voice-key")
        self.assertEqual(db.get_voice_runtime("tts")["api_key"], "speech-voice-key")
        self.assertEqual(db.get_voice_runtime("tts")["voice"], "voice-id")

        db.update_voice_settings(
            stt={
                "provider": "openai_compatible",
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
                "provider": "openai_compatible",
                "model": "whisper-test",
                "base_url": "https://speech.example.test/v1",
                "api_key": "speech-key",
                "language": "en",
            },
            tts={
                "provider": "openai_compatible",
                "model": "speech-test",
                "voice": "voice-test",
                "base_url": "https://voice.example.test/v1",
                "api_key": "voice-key",
                "language": "auto",
            },
        )
        from mounir import stt as stt_mod, tts as tts_mod

        with patch.object(
            stt_mod, "_transcribe_openai_compatible", return_value=("hello", "en")
        ) as transcribe:
            self.assertEqual(stt_mod.transcribe([0.1]), ("hello", "en"))
        self.assertEqual(transcribe.call_args.args[1], "en")
        self.assertEqual(transcribe.call_args.args[2]["model"], "whisper-test")
        self.assertEqual(transcribe.call_args.args[2]["api_key"], "speech-key")

        with patch.object(
            tts_mod, "_synthesize_openai_compatible_wav", return_value=b"wav"
        ) as synthesize:
            self.assertEqual(tts_mod.synthesize_wav("hello"), b"wav")
        self.assertEqual(synthesize.call_args.args[1]["model"], "speech-test")
        self.assertEqual(synthesize.call_args.args[1]["voice"], "voice-test")
        self.assertEqual(synthesize.call_args.args[1]["api_key"], "voice-key")

    def test_voice_configuration_rejects_unsupported_stt_language(self):
        db.init()
        with self.assertRaisesRegex(ValueError, "STT language is not supported"):
            db.update_voice_settings(
                stt={
                    "provider": "local_whisper",
                    "model": "small",
                    "language": "not-a-language",
                }
            )

    def test_compatible_voice_endpoints_allow_local_servers_without_api_keys(self):
        db.init()
        with db._connect() as conn:
            conn.execute(
                "UPDATE voice_settings SET stt_api_key = '', tts_api_key = '' WHERE id = 1"
            )
            conn.commit()
        saved = db.update_voice_settings(
            stt={
                "provider": "openai_compatible",
                "model": "local-whisper",
                "base_url": "http://127.0.0.1:8080/v1",
                "language": "auto",
            },
            tts={
                "provider": "openai_compatible",
                "model": "local-speech",
                "voice": "local-voice",
                "base_url": "http://127.0.0.1:8080/v1",
                "language": "auto",
            },
        )

        self.assertFalse(saved["stt"]["api_key_configured"])
        self.assertFalse(saved["tts"]["api_key_configured"])
        self.assertEqual(saved["stt"]["provider"], "openai_compatible")
        self.assertEqual(saved["tts"]["provider"], "openai_compatible")

    def test_legacy_groq_voice_setting_migrates_to_compatible_transport(self):
        db.init()
        with db._connect() as conn:
            conn.execute(
                "UPDATE voice_settings SET stt_provider = 'groq' WHERE id = 1"
            )
            conn.execute("ALTER TABLE voice_settings RENAME TO voice_settings_current")
            conn.execute(
                """
                CREATE TABLE voice_settings (
                    id INTEGER PRIMARY KEY, stt_provider TEXT NOT NULL,
                    stt_model TEXT NOT NULL, stt_base_url TEXT NOT NULL DEFAULT '',
                    stt_api_key TEXT NOT NULL DEFAULT '', stt_language TEXT NOT NULL DEFAULT 'auto',
                    tts_provider TEXT NOT NULL, tts_model TEXT NOT NULL,
                    tts_base_url TEXT NOT NULL DEFAULT '', tts_api_key TEXT NOT NULL DEFAULT '',
                    tts_language TEXT NOT NULL DEFAULT 'en-US', updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO voice_settings
                SELECT id, stt_provider, stt_model, stt_base_url, stt_api_key,
                       stt_language, tts_provider, tts_model, tts_base_url,
                       tts_api_key, tts_language, updated_at
                FROM voice_settings_current
                """
            )
            conn.execute("DROP TABLE voice_settings_current")
            conn.commit()

        db.init()

        saved = db.get_voice_settings()
        self.assertEqual(saved["stt"]["provider"], "openai_compatible")
        self.assertIn("voice", saved["tts"])

    def test_openai_compatible_stt_posts_standard_multipart_contract(self):
        from mounir import stt as stt_mod

        response = Mock()
        response.json.return_value = {"text": "hello world", "language": "en"}
        runtime = {
            "model": "whisper-test",
            "base_url": "https://speech.example.test/v1?api-version=1",
            "api_key": "speech-key",
        }
        with patch("requests.post", return_value=response) as post:
            result = stt_mod._transcribe_openai_compatible([0.1, -0.1], None, runtime)

        self.assertEqual(result, ("hello world", "en"))
        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://speech.example.test/v1/audio/transcriptions?api-version=1",
        )
        self.assertEqual(request.kwargs["data"]["model"], "whisper-test")
        self.assertEqual(request.kwargs["data"]["response_format"], "json")
        self.assertEqual(
            request.kwargs["headers"]["Authorization"], "Bearer speech-key"
        )

    def test_openai_compatible_tts_posts_standard_speech_contract(self):
        from mounir import tts as tts_mod

        response = Mock(content=b"wave-data")
        runtime = {
            "model": "speech-test",
            "voice": "voice-test",
            "base_url": "http://localhost:8080/v1/audio/speech",
            "api_key": "",
        }
        with patch("requests.post", return_value=response) as post:
            result = tts_mod._synthesize_openai_compatible_wav("hello", runtime)

        self.assertEqual(result, b"wave-data")
        request = post.call_args
        self.assertEqual(request.args[0], "http://localhost:8080/v1/audio/speech")
        self.assertEqual(
            request.kwargs["json"],
            {
                "model": "speech-test",
                "input": "hello",
                "voice": "voice-test",
                "response_format": "wav",
            },
        )
        self.assertNotIn("Authorization", request.kwargs["headers"])

    def test_native_google_tts_remains_available(self):
        db.init()
        db.update_voice_settings(
            tts={
                "provider": "google",
                "model": "en-US-Neural2-D",
                "base_url": "https://texttospeech.googleapis.com/v1",
                "api_key": "google-key",
                "language": "en-US",
            }
        )
        from mounir import tts as tts_mod

        with patch.object(tts_mod, "_synthesize_google_wav", return_value=b"wav") as call:
            self.assertEqual(tts_mod.synthesize_wav("hello"), b"wav")
        self.assertEqual(call.call_args.args[1]["model"], "en-US-Neural2-D")
        self.assertEqual(call.call_args.args[1]["api_key"], "google-key")

    def test_local_moss_tts_uses_selected_engine_and_voice(self):
        db.init()
        db.update_voice_settings(
            tts={
                "provider": "moss_onnx",
                "model": "/models/MOSS-TTS-Nano",
                "voice": "Adam",
                "language": "auto",
            }
        )
        from mounir import tts as tts_mod

        with patch.object(tts_mod, "_synthesize_moss_wav", return_value=b"wav") as call:
            self.assertEqual(tts_mod.synthesize_wav("Hello there."), b"wav")
        self.assertEqual(call.call_args.args[1]["model"], "/models/MOSS-TTS-Nano")
        self.assertEqual(call.call_args.args[1]["voice"], "Adam")

    def test_local_moss_voices_are_discovered_from_the_selected_package(self):
        from mounir import tts as tts_mod

        engine = Path(self.temp_dir.name) / "custom-moss-engine"
        _create_moss_package_fixture(
            engine,
            "different-model-name",
            [
                {
                    "voice": "CustomOne",
                    "display_name": "Custom voice one",
                    "group": "Test voices",
                    "prompt_audio_codes": [[1, 2]],
                },
                {
                    "voice": "CustomTwo",
                    "display_name": "Custom voice two",
                    "group": "Test voices",
                    "prompt_audio_codes": [[3, 4]],
                },
            ],
        )

        catalog = tts_mod.discover_voices("moss_onnx", str(engine))

        self.assertEqual(catalog["discovery"], "model_manifest")
        self.assertEqual(
            catalog["voices"],
            [
                {
                    "id": "CustomOne",
                    "label": "Custom voice one",
                    "group": "Test voices",
                },
                {
                    "id": "CustomTwo",
                    "label": "Custom voice two",
                    "group": "Test voices",
                },
            ],
        )

    def test_telegram_token_replacement_and_pairing_are_persisted(self):
        db.init()
        self.assertEqual(db.get_telegram_settings()["reply_mode"], "text")
        with self.assertRaisesRegex(ValueError, "bot token"):
            db.update_telegram_settings(enabled=True)

        saved = db.update_telegram_settings(bot_token="123:first", enabled=True)
        self.assertTrue(saved["enabled"])
        self.assertFalse(saved["paired"])
        paired = db.pair_telegram_chat(42, "Mounir Owner", "owner")
        self.assertTrue(paired["paired"])
        self.assertEqual(paired["chat_name"], "Mounir Owner")

        voice_mode = db.update_telegram_settings(reply_mode="voice")
        self.assertEqual(voice_mode["reply_mode"], "voice")
        self.assertEqual(db.get_telegram_settings()["reply_mode"], "voice")
        with self.assertRaisesRegex(ValueError, "text or voice"):
            db.update_telegram_settings(reply_mode="automatic")

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

        def compile_graph(*_args, **_kwargs):
            class Graph:
                def stream(self, state, **_stream_options):
                    from langchain_core.messages import AIMessage

                    observed["user"] = next(
                        message.content
                        for message in reversed(state["messages"])
                        if message.type == "human"
                    )
                    yield {"type": "custom", "data": "plain reply"}
                    yield {
                        "type": "values",
                        "data": {
                            **state,
                            "messages": [
                                *state["messages"],
                                AIMessage(content="plain reply"),
                            ],
                        },
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

    def test_heartbeat_tasks_are_independent_and_reject_protected_tools(self):
        db.init()
        email = self._create_email_fixture()
        db.save_server_tools(
            email["mcp_server_id"],
            [
                {"name": "search_emails", "description": "Read email", "input_schema": {}},
                {"name": "send_email", "description": "Send email", "input_schema": {}},
            ],
        )
        agent_key = f"mcp:{email['id']}"

        with self.assertRaisesRegex(ValueError, "require confirmation"):
            db.create_heartbeat_task(
                name="Unsafe email task",
                instructions="Send a message.",
                enabled=True,
                selected_agents=[agent_key],
                selected_tools=[
                    {"agent_key": agent_key, "tool_name": "send_email"}
                ],
            )

        first = db.create_heartbeat_task(
            name="Priority mail",
            instructions="Find important unread messages.",
            enabled=True,
            interval_minutes=15,
            selected_agents=[agent_key],
            selected_tools=[
                {"agent_key": agent_key, "tool_name": "search_emails"}
            ],
        )
        second = db.create_heartbeat_task(
            name="Daily media",
            instructions="Find newly added media.",
            interval_minutes=1440,
            selected_agents=["builtin:media"],
            selected_tools=[
                {"agent_key": "builtin:media", "tool_name": "find_media"}
            ],
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            db.get_heartbeat_targets(first["id"])[0]["allowed_tools"],
            ["search_emails"],
        )
        self.assertEqual(
            db.get_heartbeat_targets(second["id"])[0]["allowed_tools"],
            ["find_media"],
        )
        run_id = db.begin_heartbeat_task_run(first["id"], "manual")
        db.finish_heartbeat_task_run(
            first["id"], run_id, status="alert", message="Important mail."
        )
        self.assertEqual(db.list_heartbeat_task_runs(first["id"])[0]["id"], run_id)
        self.assertEqual(db.list_heartbeat_task_runs(second["id"]), [])
        self.assertEqual(
            db.list_heartbeat_notifications()[0]["heartbeat_task_name"],
            "Priority mail",
        )

    def test_heartbeat_task_pauses_after_its_last_execution(self):
        db.init()
        task = db.create_heartbeat_task(
            name="Finite system watch",
            instructions="Check the system twice.",
            enabled=True,
            interval_minutes=15,
            execution_limit=2,
            selected_agents=["builtin:system"],
            selected_tools=[
                {"agent_key": "builtin:system", "tool_name": "system_status"}
            ],
        )
        self.assertEqual(task["execution_limit"], 2)
        self.assertEqual(task["remaining_runs"], 2)

        first_run = db.begin_heartbeat_task_run(task["id"], "scheduled")
        after_first = db.finish_heartbeat_task_run(
            task["id"], first_run, status="quiet"
        )
        self.assertEqual(after_first["remaining_runs"], 1)
        self.assertTrue(after_first["enabled"])
        self.assertIsNotNone(after_first["next_run_at"])

        second_run = db.begin_heartbeat_task_run(task["id"], "scheduled")
        completed = db.finish_heartbeat_task_run(
            task["id"], second_run, status="error", error="Temporary failure"
        )
        self.assertEqual(completed["remaining_runs"], 0)
        self.assertFalse(completed["enabled"])
        self.assertIsNone(completed["next_run_at"])
        with self.assertRaisesRegex(RuntimeError, "no remaining executions"):
            db.begin_heartbeat_task_run(task["id"], "manual")

        unchanged = db.update_heartbeat_task(task["id"], name="Still completed")
        self.assertEqual(unchanged["remaining_runs"], 0)
        reset = db.update_heartbeat_task(task["id"], execution_limit=3)
        self.assertEqual(reset["remaining_runs"], 3)

    def test_heartbeat_task_can_run_without_an_execution_limit(self):
        db.init()
        task = db.create_heartbeat_task(
            name="Continuous system watch",
            instructions="Keep checking the system.",
            enabled=True,
            interval_minutes=15,
            selected_agents=["builtin:system"],
            selected_tools=[
                {"agent_key": "builtin:system", "tool_name": "system_status"}
            ],
        )
        self.assertEqual(task["execution_limit"], -1)
        self.assertEqual(task["remaining_runs"], -1)

        for _ in range(2):
            run_id = db.begin_heartbeat_task_run(task["id"], "scheduled")
            task = db.finish_heartbeat_task_run(
                task["id"], run_id, status="quiet"
            )

        self.assertEqual(task["remaining_runs"], -1)
        self.assertTrue(task["enabled"])
        self.assertIsNotNone(task["next_run_at"])

    def test_multi_record_migration_runs_once_and_preserves_deletion(self):
        db.init()
        tasks = db.list_heartbeat_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["name"], "General heartbeat")
        self.assertTrue(db.delete_heartbeat_task(tasks[0]["id"]))

        db.init()

        self.assertEqual(db.list_heartbeat_tasks(), [])

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

    def test_builtin_agent_model_selection_accepts_every_configured_provider(self):
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
        self.assertEqual(options, {nvidia["id"], ollama["id"]})

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
        self.assertEqual(runtime["provider"], "NVIDIA")
        updated = db.update_builtin_agent_model("system", ollama["id"])
        self.assertEqual(updated["model_id"], ollama["id"])

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

    def test_existing_empty_database_does_not_seed_dynamic_resources(self):
        # Existing databases follow the same user-owned registry policy as new
        # installations; application startup must never invent resources.
        with db._connect() as conn:
            db._init_schema(conn)

        db.init()
        self.assertEqual(db.list_models(), [])
        self.assertEqual(db.list_servers(), [])
        self.assertEqual(db.list_subagents(), [])
        db.init()
        self.assertEqual(db.list_models(), [])
        self.assertEqual(db.list_servers(), [])
        self.assertEqual(db.list_subagents(), [])

    def test_legacy_gmail_setup_marker_is_removed_without_deleting_server(self):
        with db._connect() as conn:
            db._init_schema(conn)
            server_id = db._add_server(
                conn,
                "User mail server",
                "user-selected-mail-mcp",
                setup_type="gmail_oauth",
            )

        db.init()

        server = db.get_server(server_id)
        self.assertIsNotNone(server)
        self.assertEqual(server["connection"], "user-selected-mail-mcp")
        self.assertEqual(server["setup_type"], "")
        db.init()
        self.assertEqual(db.list_models(), [])
        self.assertEqual([item["id"] for item in db.list_servers()], [server_id])
        self.assertEqual(db.list_subagents(), [])

    def test_private_mcp_files_are_masked_materialized_and_removable(self):
        db.init()
        server = db.add_server("Files", "run-files-server")
        db.replace_server_files(
            server["id"],
            [
                {
                    "env_var": "SERVICE_CREDENTIALS",
                    "filename": "account.json",
                    "content": b'{"private":"value"}',
                }
            ],
        )

        public = db.server_for_api(db.get_server(server["id"]))
        self.assertEqual(
            public["credential_files"],
            [{"env_var": "SERVICE_CREDENTIALS", "filename": "account.json"}],
        )
        self.assertTrue(public["credentials_configured"])
        path = Path(db.build_server_spec(server["id"])["env"]["SERVICE_CREDENTIALS"])
        self.assertEqual(path.read_bytes(), b'{"private":"value"}')
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

        db.replace_server_files(server["id"], removals=["SERVICE_CREDENTIALS"])
        self.assertEqual(db.list_server_files(server["id"]), [])
        self.assertFalse(path.exists())

    def test_oauth_state_is_private_and_cleared_when_connection_changes(self):
        db.init()
        server = db.add_server(
            "OAuth server",
            "https://example.test/mcp",
            transport="streamable_http",
            auth_scheme="oauth",
        )
        db.prepare_server_oauth(server["id"], "http://localhost/oauth/callback")

        async def save_state():
            storage = mcp_oauth.DatabaseOAuthStorage(server["id"])
            from mcp.shared.auth import OAuthToken

            await storage.set_tokens(
                OAuthToken(access_token="private-token", expires_in=3600)
            )
            provider = mcp_oauth.provider_for_spec(db.build_server_spec(server["id"]))
            await provider._initialize()
            return provider.context.token_expiry_time

        restored_expiry = asyncio.run(save_state())
        self.assertGreater(restored_expiry, 0)
        public = db.server_for_api(db.get_server(server["id"]))
        self.assertTrue(public["oauth_connected"])
        self.assertNotIn("oauth_tokens", public)
        self.assertNotIn("oauth_token_expires_at", public)
        self.assertNotIn("private-token", json.dumps(public))

        db.update_server(server["id"], connection="https://other.test/mcp")
        self.assertFalse(db.server_for_api(db.get_server(server["id"]))["oauth_connected"])

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
        self.assertEqual(
            db.update_model(system["model_id"], provider="Ollama")["provider"],
            "Ollama",
        )

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
        self.assertEqual(
            db.update_model(alternate["id"], provider="NVIDIA")["provider"],
            "NVIDIA",
        )

    def test_openai_compatible_adapter_uses_saved_endpoint_and_credentials(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "ready", "tool_calls": []}}]
        }

        with patch.object(llm_mod.requests, "post", return_value=response) as post:
            result = llm_mod.openai_chat(
                [{"role": "user", "content": "hello"}],
                model="custom-test",
                provider="My private gateway",
                base_url="https://models.example.test/v1",
                api_key="custom-key",
            )

        self.assertEqual(result["content"], "ready")
        request = post.call_args
        self.assertEqual(request.args[0], "https://models.example.test/v1/chat/completions")
        self.assertEqual(request.kwargs["json"]["model"], "custom-test")
        self.assertNotIn("temperature", request.kwargs["json"])
        self.assertEqual(
            request.kwargs["headers"]["Authorization"], "Bearer custom-key"
        )

    def test_openai_compatible_adapter_sends_explicit_temperature(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "ready", "tool_calls": []}}]
        }

        with patch.object(llm_mod.requests, "post", return_value=response) as post:
            llm_mod.openai_chat(
                [{"role": "user", "content": "hello"}],
                model="custom-test",
                provider="Custom",
                base_url="https://models.example.test/v1",
                temperature=0.7,
            )

        self.assertEqual(post.call_args.kwargs["json"]["temperature"], 0.7)

    def test_openai_compatible_adapter_retries_nvidia_degraded_deployment(self):
        degraded = []
        for _ in range(2):
            response = Mock()
            response.status_code = 400
            response.headers = {}
            response.json.return_value = {
                "detail": (
                    "Function id 'c4ed50ff-b5c3-409d-ab57-b79c33f5bb39': "
                    "DEGRADED function cannot be invoked"
                )
            }
            degraded.append(response)
        ready = Mock()
        ready.status_code = 200
        ready.json.return_value = {
            "choices": [{"message": {"content": "ready", "tool_calls": []}}]
        }

        with (
            patch.object(
                llm_mod.requests, "post", side_effect=[*degraded, ready]
            ) as post,
            patch.object(llm_mod.time, "sleep") as sleep,
        ):
            result = llm_mod.openai_chat(
                [{"role": "user", "content": "hello"}],
                model="nvidia/test-model",
                provider="NVIDIA",
                base_url="https://integrate.api.nvidia.com/v1",
                api_key="test-key",
            )

        self.assertEqual(result["content"], "ready")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(response.close.called for response in degraded))

    def test_openai_compatible_adapter_explains_persistent_degraded_deployment(self):
        responses = []
        for _ in range(3):
            response = Mock()
            response.status_code = 400
            response.headers = {}
            response.json.return_value = {
                "error": {
                    "message": (
                        "Function id 'provider-function-id': "
                        "DEGRADED function cannot be invoked"
                    )
                }
            }
            responses.append(response)

        with (
            patch.object(llm_mod.requests, "post", side_effect=responses) as post,
            patch.object(llm_mod.time, "sleep"),
            self.assertRaisesRegex(
                llm_mod.OllamaError,
                "provider's model deployment is temporarily degraded",
            ),
        ):
            llm_mod.openai_chat(
                [{"role": "user", "content": "hello"}],
                model="nvidia/test-model",
                provider="NVIDIA",
                base_url="https://integrate.api.nvidia.com/v1",
                api_key="test-key",
            )

        self.assertEqual(post.call_count, 3)
        self.assertTrue(all(response.close.called for response in responses))

    def test_openai_compatible_stream_normalizes_tool_call_deltas(self):
        response = Mock()
        response.status_code = 200
        response.iter_lines.return_value = iter(
            [
                'data: {"choices":[{"delta":{"content":"working "}}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"tool_1","function":{"name":"read_file","arguments":"{\\\"path\\\":"}}]}}]}',
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\\"notes.md\\\"}"}}]}}]}',
                "data: [DONE]",
            ]
        )

        calls = []
        with patch.object(
            llm_mod.requests, "post", return_value=response
        ) as post:
            text = "".join(
                llm_mod.chat_stream(
                    [{"role": "user", "content": "read notes"}],
                    model="gpt-test",
                    provider="OpenAI",
                    base_url="https://models.example.test/v1",
                    tool_calls_out=calls,
                )
            )

        self.assertEqual(text, "working ")
        self.assertNotIn("temperature", post.call_args.kwargs["json"])
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(calls[0]["function"]["arguments"], {"path": "notes.md"})

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
            patch.object(system_agent.llm, "openai_chat", side_effect=fake_chat),
            patch.object(system_agent, "set_volume") as set_volume,
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
        set_volume.assert_not_called()

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

    def test_heartbeat_task_is_read_by_scoped_mounir_supervisor(self):
        db.init()
        task = db.create_heartbeat_task(
            name="System watch",
            instructions="Check whether the computer needs attention.",
            selected_agents=["builtin:system"],
            selected_tools=[
                {"agent_key": "builtin:system", "tool_name": "system_status"}
            ],
        )
        observed = {}

        class FakeAgent:
            def __init__(self, conversation, scoped_targets):
                observed["targets"] = scoped_targets
                self.conversation = conversation

            def respond(self, prompt):
                observed["prompt"] = prompt
                self.conversation.add_assistant("Battery health needs attention.")
                yield "Battery health needs attention."

        with patch.object(heartbeat_mod, "Agent", FakeAgent):
            self.assertEqual(
                heartbeat_mod.run_task(task["id"]),
                ("alert", "Battery health needs attention."),
            )

        self.assertIn("Check whether the computer", observed["prompt"])
        self.assertEqual(observed["targets"][0]["builtin_key"], "system")
        self.assertEqual(observed["targets"][0]["allowed_tools"], ["system_status"])
        scoped_graph = langgraph_agent._compile_graph(
            config.MODEL, True, observed["targets"]
        )
        self.assertIsNotNone(scoped_graph)
        with patch.object(heartbeat_mod, "Agent", FakeAgent):
            self.assertEqual(heartbeat_mod.run_task(task["id"]), ("quiet", ""))

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
        self.assertIsNone(notifications[0]["read_at"])
        self.assertTrue(db.mark_heartbeat_notification_read(alert_run))
        self.assertEqual(db.list_heartbeat_notifications(unread_only=True), [])
        self.assertEqual(
            db.list_heartbeat_notifications()[0]["id"], alert_run
        )
        self.assertTrue(db.delete_heartbeat_notification(alert_run))
        self.assertEqual(db.list_heartbeat_notifications(), [])
        self.assertFalse(db.delete_heartbeat_notification(alert_run))

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
            connection_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'subagent_connections'"
            ).fetchone()
            node_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'subagent_nodes'"
            ).fetchone()
            node_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(subagent_nodes)")
            }
            migrated_node = conn.execute(
                "SELECT agent_id, parent_node_id FROM subagent_nodes"
            ).fetchone()
            migrated_connection = conn.execute(
                "SELECT parent_agent_id, child_agent_id FROM subagent_connections"
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
                "auth_scheme", "setup_command", "oauth_redirect_uri",
                "oauth_tokens", "oauth_token_expires_at", "oauth_client_info",
            }
            <= server_columns
        )
        self.assertTrue(
            {
                "description", "icon_data", "icon_mime",
                "confirm_tool_calls", "confirm_tools", "dedupe_tools", "enabled",
                "parent_agent_id",
            }
            <= agent_columns
        )
        self.assertIsNotNone(tool_table)
        self.assertIsNotNone(connection_table)
        self.assertIsNotNone(node_table)
        self.assertIn("enabled_tools", node_columns)
        self.assertEqual(migrated_node["agent_id"], 1)
        self.assertIsNone(migrated_node["parent_node_id"])
        self.assertIsNone(migrated_connection["parent_agent_id"])
        self.assertEqual(migrated_connection["child_agent_id"], 1)
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

    def test_subagents_support_safe_nested_hierarchies(self):
        db.init()
        model = db.add_model(
            "Hierarchy model", "hierarchy/model", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Hierarchy server", "hierarchy-server")
        parent = db.add_subagent(
            "Team lead", "Coordinates the team.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        child = db.add_subagent(
            "Worker", "Handles delegated work.", "",
            model["id"], server["id"], confirm_tools=[],
            parent_agent_id=parent["id"],
        )
        grandchild = db.add_subagent(
            "Specialist", "Handles narrow work.", "",
            model["id"], server["id"], confirm_tools=[],
            parent_agent_id=child["id"],
        )

        self.assertIsNone(parent["parent_agent_id"])
        self.assertEqual(child["parent_agent_id"], parent["id"])
        self.assertEqual(child["parent_name"], "Team lead")
        specs = {item["id"]: item for item in db.build_specs()}
        self.assertEqual(specs[grandchild["id"]]["parent_agent_id"], child["id"])
        with self.assertRaisesRegex(ValueError, "placement's children"):
            db.update_subagent(parent["id"], parent_agent_id=grandchild["id"])
        with self.assertRaisesRegex(ValueError, "children first"):
            db.delete_subagent(parent["id"])

        level_four = db.add_subagent(
            "Deep specialist", "Handles the deepest allowed work.", "",
            model["id"], server["id"], confirm_tools=[],
            parent_agent_id=grandchild["id"],
        )
        with self.assertRaisesRegex(ValueError, "at most 4 levels"):
            db.add_subagent(
                "Too deep", "Must be rejected.", "",
                model["id"], server["id"], confirm_tools=[],
                parent_agent_id=level_four["id"],
            )

    def test_supervisor_graph_exposes_only_top_level_dynamic_agents(self):
        db.init()
        model = db.add_model(
            "Routing model", "routing/model", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Routing server", "routing-server")
        parent = db.add_subagent(
            "Lead agent", "Coordinates work.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        db.add_subagent(
            "Nested agent", "Completes child tasks.", "",
            model["id"], server["id"], confirm_tools=[],
            parent_agent_id=parent["id"],
        )

        nodes = set(langgraph_agent.build_graph().get_graph().nodes)
        self.assertIn("mcp_lead_agent", nodes)
        self.assertNotIn("mcp_nested_agent", nodes)

    def test_parent_assigns_multiple_children_atomically(self):
        db.init()
        model = db.add_model(
            "Bulk hierarchy model", "bulk/hierarchy", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Bulk hierarchy server", "bulk-server")
        parent = db.add_subagent(
            "Bulk lead", "Coordinates selected children.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        other_parent = db.add_subagent(
            "Other lead", "Starts with one child.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        first = db.add_subagent(
            "First worker", "Handles first tasks.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        second = db.add_subagent(
            "Second worker", "Handles second tasks.", "",
            model["id"], server["id"], confirm_tools=[],
            parent_agent_id=other_parent["id"],
        )

        updated = db.update_subagent(
            parent["id"], child_agent_ids=[first["id"], second["id"]]
        )
        self.assertEqual(updated["child_count"], 2)
        self.assertEqual(db.get_subagent(first["id"])["parent_agent_id"], parent["id"])
        self.assertEqual(db.get_subagent(second["id"])["parent_agent_id"], parent["id"])
        self.assertEqual(
            set(db.get_subagent(second["id"])["parent_agent_ids"]),
            {parent["id"], other_parent["id"]},
        )
        self.assertTrue(db.get_subagent(first["id"])["connected_to_supervisor"])
        self.assertEqual(db.get_subagent(other_parent["id"])["child_count"], 1)

        db.update_subagent(parent["id"], child_agent_ids=[second["id"]])
        self.assertIsNone(db.get_subagent(first["id"])["parent_agent_id"])
        self.assertEqual(db.get_subagent(second["id"])["parent_agent_id"], parent["id"])

        with self.assertRaisesRegex(ValueError, "placement's children"):
            db.update_subagent(parent["id"], parent_agent_id=first["id"])
        self.assertIsNone(db.get_subagent(parent["id"])["parent_agent_id"])
        self.assertIsNone(db.get_subagent(first["id"])["parent_agent_id"])
        self.assertEqual(db.get_subagent(second["id"])["parent_agent_id"], parent["id"])

        with self.assertRaisesRegex(ValueError, "existing subagent"):
            db.update_subagent(parent["id"], child_agent_ids=[999999])
        self.assertEqual(db.get_subagent(second["id"])["parent_agent_id"], parent["id"])

    def test_subagent_can_keep_several_parent_connections(self):
        db.init()
        model = db.add_model(
            "Flexible hierarchy model", "flexible/hierarchy", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Flexible hierarchy server", "flexible-server")
        github = db.add_subagent(
            "GitHub", "Handles repositories.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        projects = db.add_subagent(
            "Projects", "Coordinates projects.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        gmail = db.add_subagent(
            "Gmail", "Handles email.", "",
            model["id"], server["id"], confirm_tools=[],
        )

        db.connect_subagent(gmail["id"], github["id"])
        connected = db.connect_subagent(gmail["id"], projects["id"])
        self.assertTrue(connected["connected_to_supervisor"])
        self.assertEqual(
            set(connected["parent_agent_ids"]), {github["id"], projects["id"]}
        )
        self.assertEqual(
            connected["parent_names"], ["Mounir", "GitHub", "Projects"]
        )

        db.update_subagent(github["id"], child_agent_ids=[])
        remaining = db.get_subagent(gmail["id"])
        self.assertTrue(remaining["connected_to_supervisor"])
        self.assertEqual(remaining["parent_agent_ids"], [projects["id"]])
        repeated = db.connect_subagent(projects["id"], gmail["id"])
        self.assertEqual(len(repeated["placements"]), 2)

        gmail_specs = [item for item in db.build_specs() if item["id"] == gmail["id"]]
        self.assertTrue(any(item["connected_to_supervisor"] for item in gmail_specs))
        self.assertTrue(
            any(item["parent_agent_ids"] == [projects["id"]] for item in gmail_specs)
        )
        nodes = set(langgraph_agent.build_graph().get_graph().nodes)
        self.assertIn("mcp_gmail", nodes)

    def test_duplicate_agent_nodes_keep_independent_children(self):
        db.init()
        model = db.add_model(
            "Placement model", "placement/model", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Placement server", "placement-server")
        github = db.add_subagent(
            "GitHub", "Handles repositories.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        gmail = db.add_subagent(
            "Gmail", "Handles email.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        researcher = db.add_subagent(
            "Researcher", "Handles research.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        gmail_node_id = gmail["placements"][0]["id"]
        db.connect_subagent(
            github["id"], parent_node_id=gmail_node_id
        )
        github_nodes = db.get_subagent(github["id"])["placements"]
        root_github = next(node for node in github_nodes if node["parent_node_id"] is None)
        nested_github = next(
            node for node in github_nodes if node["parent_node_id"] == gmail_node_id
        )

        db.connect_subagent(
            researcher["id"], parent_node_id=nested_github["id"]
        )
        github_nodes = db.get_subagent(github["id"])["placements"]
        root_github = next(node for node in github_nodes if node["id"] == root_github["id"])
        nested_github = next(node for node in github_nodes if node["id"] == nested_github["id"])
        self.assertEqual(root_github["child_agent_ids"], [])
        self.assertEqual(nested_github["child_agent_ids"], [researcher["id"]])

        specs = db.build_specs()
        github_specs = [spec for spec in specs if spec["id"] == github["id"]]
        self.assertEqual(len(github_specs), 2)
        researcher_nodes = [spec for spec in specs if spec["id"] == researcher["id"]]
        self.assertTrue(
            any(spec["parent_node_id"] == nested_github["id"] for spec in researcher_nodes)
        )
        self.assertFalse(
            any(spec["parent_node_id"] == root_github["id"] for spec in researcher_nodes)
        )

    def test_subagent_node_details_describe_only_one_placement(self):
        db.init()
        model = db.add_model(
            "Node detail model", "node/detail", "Ollama",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Node detail server", "node-detail-server")
        github = db.add_subagent(
            "GitHub details", "Handles repositories.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        gmail = db.add_subagent(
            "Gmail details", "Handles email.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        researcher = db.add_subagent(
            "Research details", "Handles research.", "",
            model["id"], server["id"], confirm_tools=[],
        )
        gmail_node_id = gmail["placements"][0]["id"]
        db.connect_subagent(github["id"], parent_node_id=gmail_node_id)
        github_nodes = db.get_subagent(github["id"])["placements"]
        root_node = next(node for node in github_nodes if node["parent_node_id"] is None)
        nested_node = next(
            node for node in github_nodes if node["parent_node_id"] == gmail_node_id
        )
        db.connect_subagent(researcher["id"], parent_node_id=nested_node["id"])

        root_details = db.get_subagent_node(root_node["id"])
        nested_details = db.get_subagent_node(nested_node["id"])

        self.assertIsNone(root_details["parent"])
        self.assertEqual(root_details["children"], [])
        self.assertEqual(nested_details["parent"]["id"], gmail_node_id)
        self.assertEqual(nested_details["parent"]["name"], "Gmail details")
        self.assertEqual(nested_details["subagent"]["id"], github["id"])
        self.assertEqual(
            nested_details["path_label"],
            "Mounir / Gmail details / GitHub details",
        )
        self.assertEqual(
            [child["subagent_id"] for child in nested_details["children"]],
            [researcher["id"]],
        )
        self.assertNotIn("placements", nested_details["subagent"])
        self.assertIsNone(root_details["enabled_tools"])
        self.assertIsNone(nested_details["enabled_tools"])
        self.assertIsNone(db.get_subagent_node(999999))

        db.update_subagent_node(root_node["id"], enabled_tools=["read_repository"])
        db.update_subagent_node(
            nested_node["id"], enabled_tools=["create_issue", "update_issue"]
        )
        root_details = db.get_subagent_node(root_node["id"])
        nested_details = db.get_subagent_node(nested_node["id"])
        self.assertEqual(root_details["enabled_tools"], ["read_repository"])
        self.assertEqual(
            nested_details["enabled_tools"], ["create_issue", "update_issue"]
        )
        specs = {spec["node_id"]: spec for spec in db.build_specs()}
        self.assertEqual(specs[root_node["id"]]["allowed_tools"], ["read_repository"])
        self.assertEqual(
            specs[nested_node["id"]]["allowed_tools"],
            ["create_issue", "update_issue"],
        )
        db.update_subagent_node(root_node["id"], enabled_tools=None)
        self.assertIsNone(db.get_subagent_node(root_node["id"])["enabled_tools"])
        with self.assertRaisesRegex(ValueError, "explicit tool names"):
            db.update_subagent_node(root_node["id"], enabled_tools=["*"])
        self.assertIsNone(db.update_subagent_node(999999, enabled_tools=[]))

        removed = db.remove_subagent_node(nested_node["id"])
        self.assertEqual(removed["removed_nodes"], 2)
        self.assertIsNone(db.get_subagent_node(nested_node["id"]))
        self.assertIsNotNone(db.get_subagent_node(root_node["id"]))
        self.assertEqual(
            [node["id"] for node in db.get_subagent(github["id"])["placements"]],
            [root_node["id"]],
        )
        self.assertEqual(len(db.get_subagent(researcher["id"])["placements"]), 1)
        root_removed = db.remove_subagent_node(root_node["id"])
        self.assertEqual(root_removed["removed_nodes"], 1)
        self.assertIsNone(root_removed["parent_node_id"])
        self.assertIsNone(db.get_subagent_node(root_node["id"]))
        self.assertEqual(db.get_subagent(github["id"])["placements"], [])
        self.assertIsNotNone(db.get_subagent(github["id"]))
        self.assertIsNone(db.remove_subagent_node(999999))

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

    def test_typed_tools_generate_schemas_and_toolnode_executes_supervisor_calls(self):
        db.init()
        schemas = graph_runtime.tool_schemas(mounir_tools.TOOLS)
        self.assertEqual(
            [schema["function"]["name"] for schema in schemas],
            [registered.name for registered in mounir_tools.TOOLS],
        )
        bash_schema = next(
            schema for schema in schemas if schema["function"]["name"] == "bash"
        )
        self.assertEqual(
            bash_schema["function"]["parameters"]["properties"]["timeout"]["type"],
            "integer",
        )

        observed = []

        def fake_chat(messages, tools=None, tool_calls_out=None, **_kwargs):
            if not any(message.get("role") == "tool" for message in messages):
                tool_calls_out.append(
                    SimpleNamespace(
                        id="list_1",
                        function=SimpleNamespace(
                            name="list_directory", arguments={"path": "."}
                        ),
                    )
                )
                return
            observed.extend(
                message.get("content")
                for message in messages
                if message.get("role") == "tool"
            )
            yield "done"

        with (
            patch.object(langgraph_agent.llm, "chat_stream", fake_chat),
            patch.object(mounir_tools, "list_directory", return_value="listing"),
        ):
            reply = "".join(
                Agent(conversation=Conversation(system_prompt="test")).respond("list")
            )

        self.assertEqual(reply, "done")
        self.assertEqual(observed, ["listing"])

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

        model_result = db.delete_model_result(model["id"])
        server_result = db.delete_server_result(server["id"])
        self.assertEqual(model_result.status, "in_use")
        self.assertIn("the Helper subagent", model_result.dependencies)
        self.assertEqual(server_result.status, "in_use")
        self.assertEqual(server_result.dependencies, ("the Helper subagent",))

    def test_api_views_mask_credentials_and_masked_updates_preserve_them(self):
        db.init()
        model = db.add_model(
            "Private model", "model-id", "Cloud", "https://models.test/v1", "model-key"
        )
        server = db.add_server(
            "Private server",
            "https://example.test/mcp",
            transport="streamable_http",
            headers={"Authorization": "Bearer server-key"},
            auth_scheme="bearer",
        )

        public_model = db.model_for_api(model)
        public_server = db.server_for_api(server)
        self.assertNotIn("api_key", public_model)
        self.assertTrue(public_model["api_key_configured"])
        self.assertEqual(json.loads(public_server["headers"]), {"Authorization": ""})
        self.assertTrue(public_server["headers_configured"])

        db.update_server(server["id"], headers=public_server["headers"])
        self.assertEqual(
            db.build_server_spec(server["id"])["headers"],
            {"Authorization": "Bearer server-key"},
        )

    def test_connection_rolls_back_failed_write(self):
        db.init()
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with db._connect() as conn:
                conn.execute(
                    "INSERT INTO models (name, model, provider, base_url) VALUES (?, ?, ?, ?)",
                    ("Rolled back", "model", "Local", "http://localhost:11434/v1"),
                )
                raise RuntimeError("stop")
        self.assertEqual(db.list_models(), [])

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
    def test_dynamic_decline_stops_before_a_second_specialist_model_call(self):
        class Tool:
            name = "get_me"
            description = "Read the authenticated profile."
            inputSchema = {"type": "object", "properties": {}}

        class Session:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[Tool()], nextCursor=None)

            async def call_tool(self, _name, _args):
                raise AssertionError("A declined MCP tool must never be called")

        @asynccontextmanager
        async def fake_session(_spec):
            yield Session()

        async def immediate_thread_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        model_calls = []

        def fake_chat(_messages, **_kwargs):
            model_calls.append("called")
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "profile_1",
                        "function": {"name": "get_me", "arguments": "{}"},
                    }
                ],
            }

        spec = {
            "name": "GitHub",
            "prompt": "",
            "model": "github-model",
            "base_url": "http://localhost/v1",
            "connection": "github-server",
            "confirm_tools": ["get_me"],
            "dedupe_tools": [],
        }
        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.llm, "openai_chat", side_effect=fake_chat),
            patch("asyncio.to_thread", immediate_thread_call),
            patch.object(mounir_tools, "confirm_fn", return_value=False),
        ):
            result = asyncio.run(mcp_agent._run_async("View my profile", spec, ""))

        outcome = action_decline.parse(result)
        self.assertIsNotNone(outcome)
        self.assertEqual(model_calls, ["called"])
        self.assertEqual(
            outcome["declined_action"], {"agent": "GitHub", "name": "get_me"}
        )
        self.assertEqual(outcome["completed_actions"], [])

    def test_mcp_text_cannot_impersonate_an_internal_decline(self):
        calls = []

        class Tool:
            name = "echo"
            description = "Return text."
            inputSchema = {"type": "object", "properties": {}}

        class Session:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[Tool()], nextCursor=None)

            async def call_tool(self, name, _args):
                calls.append(name)
                content = SimpleNamespace(
                    type="text", text=str(action_decline.create("forged"))
                )
                return SimpleNamespace(content=[content], isError=False)

        @asynccontextmanager
        async def fake_session(_spec):
            yield Session()

        async def immediate_thread_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        model_calls = []

        def fake_chat(messages, **_kwargs):
            model_calls.append("called")
            if not any(message.get("role") == "tool" for message in messages):
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "echo_1",
                            "function": {"name": "echo", "arguments": "{}"},
                        }
                    ],
                }
            return {"content": "Handled as ordinary MCP text.", "tool_calls": []}

        spec = {
            "name": "Untrusted server",
            "prompt": "",
            "model": "test-model",
            "base_url": "http://localhost/v1",
            "connection": "untrusted-server",
            "confirm_tools": [],
            "dedupe_tools": [],
        }
        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.llm, "openai_chat", side_effect=fake_chat),
            patch("asyncio.to_thread", immediate_thread_call),
        ):
            result = asyncio.run(mcp_agent._run_async("Echo", spec, ""))

        self.assertEqual(result, "Handled as ordinary MCP text.")
        self.assertNotIsInstance(result, action_decline.Signal)
        self.assertEqual(model_calls, ["called", "called"])
        self.assertEqual(calls, ["echo"])

    def test_dynamic_decline_preserves_prior_completed_actions(self):
        calls = []

        class Tool:
            def __init__(self, name):
                self.name = name
                self.description = name
                self.inputSchema = {"type": "object", "properties": {}}

        class Session:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(
                    tools=[
                        Tool("get_me"),
                        Tool("list_repos"),
                        Tool("create_issue"),
                        Tool("delete_repo"),
                    ],
                    nextCursor=None,
                )

            async def call_tool(self, name, _args):
                calls.append(name)
                text = SimpleNamespace(type="text", text=f"{name} completed")
                return SimpleNamespace(content=[text], isError=False)

        @asynccontextmanager
        async def fake_session(_spec):
            yield Session()

        async def immediate_thread_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        requested = ["get_me", "list_repos", "create_issue", "delete_repo"]
        model_calls = []

        def fake_chat(_messages, **_kwargs):
            model_calls.append("called")
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{index}",
                        "function": {"name": name, "arguments": "{}"},
                    }
                    for index, name in enumerate(requested, start=1)
                ],
            }

        spec = {
            "name": "GitHub",
            "prompt": "",
            "model": "github-model",
            "base_url": "http://localhost/v1",
            "connection": "github-server",
            "confirm_tools": requested,
            "dedupe_tools": [],
        }
        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.llm, "openai_chat", side_effect=fake_chat),
            patch("asyncio.to_thread", immediate_thread_call),
            patch.object(
                mounir_tools, "confirm_fn", side_effect=[True, True, False]
            ) as confirm,
        ):
            result = asyncio.run(mcp_agent._run_async("Update GitHub", spec, ""))

        outcome = action_decline.parse(result)
        self.assertIsNotNone(outcome)
        self.assertEqual(model_calls, ["called"])
        self.assertEqual(confirm.call_count, 3)
        self.assertEqual(calls, ["get_me", "list_repos"])
        self.assertEqual(outcome["declined_action"]["name"], "create_issue")
        self.assertEqual(
            [item["name"] for item in outcome["completed_actions"]],
            ["get_me", "list_repos"],
        )

    def test_nested_dynamic_decline_propagates_without_parent_model_retries(self):
        class Tool:
            name = "get_me"
            description = "Read the authenticated profile."
            inputSchema = {"type": "object", "properties": {}}

        class Session:
            def __init__(self, tools):
                self.tools = tools

            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=self.tools, nextCursor=None)

            async def call_tool(self, _name, _args):
                raise AssertionError("The nested declined tool must never be called")

        @asynccontextmanager
        async def fake_session(spec):
            yield Session([Tool()] if spec["name"] == "Profile Reader" else [])

        async def immediate_thread_call(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        specs = [
            {
                "id": 1,
                "name": "Coordinator",
                "description": "Coordinates GitHub tasks.",
                "prompt": "",
                "model": "coordinator-model",
                "base_url": "http://localhost/v1",
                "connection": "coordinator-server",
                "confirm_tools": [],
                "dedupe_tools": [],
                "parent_agent_id": None,
            },
            {
                "id": 2,
                "name": "GitHub",
                "description": "Handles GitHub tasks.",
                "prompt": "",
                "model": "github-model",
                "base_url": "http://localhost/v1",
                "connection": "github-server",
                "confirm_tools": [],
                "dedupe_tools": [],
                "parent_agent_id": 1,
            },
            {
                "id": 3,
                "name": "Profile Reader",
                "description": "Reads GitHub profiles.",
                "prompt": "",
                "model": "profile-model",
                "base_url": "http://localhost/v1",
                "connection": "profile-server",
                "confirm_tools": ["get_me"],
                "dedupe_tools": [],
                "parent_agent_id": 2,
            },
        ]
        model_calls = []

        def fake_chat(_messages, model="", **_kwargs):
            model_calls.append(model)
            if model == "coordinator-model":
                name = "delegate_to_github"
            elif model == "github-model":
                name = "delegate_to_profile_reader"
            else:
                name = "get_me"
            arguments = (
                '{"task":"Read my profile"}'
                if name.startswith("delegate")
                else "{}"
            )
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": f"{name}_1",
                        "function": {"name": name, "arguments": arguments},
                    }
                ],
            }

        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.llm, "openai_chat", side_effect=fake_chat),
            patch("asyncio.to_thread", immediate_thread_call),
            patch.object(db, "is_subagent_enabled", return_value=True),
            patch.object(mounir_tools, "confirm_fn", return_value=False),
        ):
            result = asyncio.run(
                mcp_agent._run_async(
                    "Handle the request",
                    specs[0],
                    "",
                    all_specs=specs,
                    lineage=(1,),
                )
            )

        outcome = action_decline.parse(result)
        self.assertIsNotNone(outcome)
        self.assertEqual(
            model_calls, ["coordinator-model", "github-model", "profile-model"]
        )
        self.assertEqual(
            outcome["agent_path"], ["Coordinator", "GitHub", "Profile Reader"]
        )
        self.assertEqual(
            outcome["declined_action"],
            {"agent": "Profile Reader", "name": "get_me"},
        )

    def test_supervisor_intercepts_dynamic_decline_without_another_llm_call(self):
        spec = {
            "id": 1,
            "name": "GitHub",
            "description": "Handles GitHub tasks.",
            "prompt": "",
            "model": "github-model",
            "base_url": "http://localhost/v1",
            "connection": "github-server",
            "confirm_tools": ["get_me"],
            "dedupe_tools": [],
            "parent_agent_id": None,
            "connected_to_supervisor": True,
        }
        signal = action_decline.add_agent_context(
            action_decline.create("create_issue"),
            agent="GitHub",
            completed_actions=[
                {"name": "get_me", "result": "Profile loaded"},
                {"name": "list_repos", "result": "Repositories loaded"},
            ],
        )
        model_calls = []

        def fake_chat_stream(_messages, tool_calls_out=None, **_kwargs):
            model_calls.append("called")
            tool_calls_out.append(
                SimpleNamespace(
                    id="github_1",
                    function=SimpleNamespace(
                        name="delegate_to_github",
                        arguments={"task": "View my GitHub profile"},
                    ),
                )
            )
            if False:
                yield ""

        with (
            patch.object(mcp_agents, "load", return_value=[spec]),
            patch.object(db, "enabled_builtin_agent_keys", return_value=set()),
            patch.object(db, "is_subagent_enabled", return_value=True),
            patch.object(
                db,
                "get_supervisor_runtime",
                return_value={
                    "model": "supervisor-model",
                    "provider": "test",
                    "base_url": "http://localhost/v1",
                    "api_key": "",
                },
            ),
            patch.object(langgraph_agent.llm, "chat_stream", fake_chat_stream),
            patch.object(langgraph_agent, "run_mcp_agent", return_value=signal),
        ):
            reply = "".join(
                Agent(conversation=Conversation(system_prompt="test")).respond(
                    "View my GitHub profile"
                )
        )

        self.assertEqual(model_calls, ["called"])
        self.assertIn("declined create_issue in GitHub", reply)
        self.assertIn("get_me in GitHub: Profile loaded", reply)
        self.assertIn("list_repos in GitHub: Repositories loaded", reply)

    def test_parent_agent_delegates_to_child_through_toolnode(self):
        class Session:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[], nextCursor=None)

        @asynccontextmanager
        async def fake_session(_spec):
            yield Session()

        parent = {
            "id": 1,
            "name": "Lead",
            "description": "Coordinates tasks.",
            "prompt": "",
            "model": "parent-model",
            "base_url": "http://localhost/v1",
            "connection": "parent-server",
            "confirm_tools": [],
            "dedupe_tools": [],
            "parent_agent_id": None,
        }
        child = {
            "id": 2,
            "name": "Worker",
            "description": "Completes focused tasks.",
            "prompt": "",
            "model": "child-model",
            "base_url": "http://localhost/v1",
            "connection": "child-server",
            "confirm_tools": [],
            "dedupe_tools": [],
            "parent_agent_id": 1,
        }
        observed_tools = {}

        def fake_chat(messages, tools=None, model="", **_kwargs):
            observed_tools.setdefault(model, tools or [])
            if model == "child-model":
                return {"content": "Child completed the focused task.", "tool_calls": []}
            tool_result = next(
                (message for message in messages if message.get("role") == "tool"),
                None,
            )
            if tool_result is None:
                return {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "delegate_1",
                            "function": {
                                "name": "delegate_to_worker",
                                "arguments": '{"task":"Do the focused task"}',
                            },
                        }
                    ],
                }
            self.assertIn("Child completed", tool_result["content"])
            return {"content": "The team completed the task.", "tool_calls": []}

        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.llm, "openai_chat", side_effect=fake_chat),
            patch.object(db, "is_subagent_enabled", return_value=True),
        ):
            result = asyncio.run(
                mcp_agent._run_async(
                    "Handle the request",
                    parent,
                    "",
                    all_specs=[parent, child],
                    lineage=(1,),
                )
            )

        self.assertEqual(result, "The team completed the task.")
        self.assertEqual(
            [tool["function"]["name"] for tool in observed_tools["parent-model"]],
            ["delegate_to_worker"],
        )
        self.assertEqual(observed_tools["child-model"], [])

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

        self.assertIn("Use only relevant tools", dynamic_prompt)
        self.assertIn(
            "SPECIALIST INSTRUCTIONS\nUse the echo tool.", dynamic_prompt
        )
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
    def test_tts_voice_catalog_api_reads_the_requested_model(self):
        import httpx
        import server as web_server

        engine = Path(self.temp_dir.name) / "api-moss-engine"
        _create_moss_package_fixture(
            engine,
            "user-selected-package",
            [
                {
                    "voice": "UserVoice",
                    "display_name": "User-selected voice",
                    "group": "Custom",
                }
            ],
        )

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                response = await client.get(
                    "/api/tts-voices",
                    params={"provider": "moss_onnx", "model": str(engine)},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["voices"][0]["id"], "UserVoice")

        asyncio.run(exercise_api())

    def test_heartbeat_task_crud_api_keeps_tool_confirmation_safety(self):
        import httpx
        import server as web_server

        db.init()
        model = db.add_model(
            "Heartbeat API model",
            "heartbeat/api",
            "Ollama",
            "http://localhost:11434/v1",
            "",
        )
        mcp_server = db.add_server("Heartbeat API server", "heartbeat-api-server")
        agent = db.add_subagent(
            "Heartbeat API agent",
            "Reads service state.",
            "",
            model["id"],
            mcp_server["id"],
            confirm_tools=["write_state"],
        )
        db.save_server_tools(
            mcp_server["id"],
            [
                {"name": "read_state", "input_schema": {}},
                {"name": "write_state", "input_schema": {}},
            ],
        )
        agent_key = f"mcp:{agent['id']}"

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                unsafe = await client.post(
                    "/api/heartbeat/tasks",
                    json={
                        "name": "Unsafe task",
                        "instructions": "Change state.",
                        "selected_agents": [agent_key],
                        "selected_tools": [
                            {"agent_key": agent_key, "tool_name": "write_state"}
                        ],
                    },
                )
                self.assertEqual(unsafe.status_code, 400)

                created = await client.post(
                    "/api/heartbeat/tasks",
                    json={
                        "name": "Service state",
                        "instructions": "Read service state and report changes.",
                        "enabled": True,
                        "interval_minutes": 15,
                        "execution_limit": 3,
                        "selected_agents": [agent_key],
                        "selected_tools": [
                            {"agent_key": agent_key, "tool_name": "read_state"}
                        ],
                    },
                )
                self.assertEqual(created.status_code, 200)
                task = created.json()
                self.assertTrue(task["enabled"])
                self.assertEqual(task["execution_limit"], 3)
                self.assertEqual(task["remaining_runs"], 3)
                self.assertEqual(task["selected_agents"], [agent_key])

                listing = await client.get("/api/heartbeat")
                self.assertTrue(
                    any(item["id"] == task["id"] for item in listing.json()["tasks"])
                )
                updated = await client.put(
                    f"/api/heartbeat/tasks/{task['id']}",
                    json={**task, "name": "Updated service state", "enabled": False},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(updated.json()["name"], "Updated service state")

                deleted = await client.delete(f"/api/heartbeat/tasks/{task['id']}")
                self.assertEqual(deleted.status_code, 200)
                missing = await client.delete(f"/api/heartbeat/tasks/{task['id']}")
                self.assertEqual(missing.status_code, 404)

        asyncio.run(exercise_api())

    def test_subagent_api_persists_and_protects_parent_relationships(self):
        import httpx
        import server as web_server

        db.init()
        model = db.add_model(
            "API hierarchy model", "api/hierarchy", "Ollama",
            "http://localhost:11434/v1", "",
        )
        mcp_server = db.add_server("API hierarchy server", "api-server")

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                common = {
                    "description": "Hierarchy test agent.",
                    "system_prompt": "",
                    "model_id": model["id"],
                    "mcp_server_id": mcp_server["id"],
                    "confirm_tools": [],
                }
                parent_response = await client.post(
                    "/api/subagents", json={**common, "name": "API lead"}
                )
                self.assertEqual(parent_response.status_code, 200)
                parent = parent_response.json()
                child_response = await client.post(
                    "/api/subagents",
                    json={
                        **common,
                        "name": "API worker",
                        "parent_agent_id": parent["id"],
                    },
                )
                self.assertEqual(child_response.status_code, 200)
                child = child_response.json()
                self.assertEqual(child["parent_agent_id"], parent["id"])
                self.assertEqual(child["parent_name"], "API lead")
                second_child_response = await client.post(
                    "/api/subagents", json={**common, "name": "API second worker"}
                )
                self.assertEqual(second_child_response.status_code, 200)
                second_child = second_child_response.json()

                connected = await client.post(
                    f"/api/subagents/{second_child['id']}/connections",
                    json={"parent_node_id": parent["placements"][0]["id"]},
                )
                self.assertEqual(connected.status_code, 200)
                self.assertTrue(connected.json()["connected_to_supervisor"])
                self.assertEqual(
                    connected.json()["parent_agent_ids"], [parent["id"]]
                )
                nested_node = next(
                    node
                    for node in connected.json()["placements"]
                    if node["parent_node_id"] == parent["placements"][0]["id"]
                )
                node_response = await client.get(
                    f"/api/subagent-nodes/{nested_node['id']}"
                )
                self.assertEqual(node_response.status_code, 200)
                node_details = node_response.json()
                self.assertEqual(node_details["id"], nested_node["id"])
                self.assertEqual(
                    node_details["parent"]["id"], parent["placements"][0]["id"]
                )
                self.assertEqual(node_details["subagent"]["id"], second_child["id"])
                self.assertNotIn("placements", node_details["subagent"])
                self.assertIsNone(node_details["enabled_tools"])
                restricted_node = await client.put(
                    f"/api/subagent-nodes/{nested_node['id']}",
                    json={"enabled_tools": ["read_message"]},
                )
                self.assertEqual(restricted_node.status_code, 200)
                self.assertEqual(
                    restricted_node.json()["enabled_tools"], ["read_message"]
                )
                missing_field = await client.put(
                    f"/api/subagent-nodes/{nested_node['id']}", json={}
                )
                self.assertEqual(missing_field.status_code, 400)
                missing_node = await client.get("/api/subagent-nodes/999999")
                self.assertEqual(missing_node.status_code, 404)
                missing_node_update = await client.put(
                    "/api/subagent-nodes/999999", json={"enabled_tools": []}
                )
                self.assertEqual(missing_node_update.status_code, 404)
                disposable_response = await client.post(
                    "/api/subagents", json={**common, "name": "API disposable"}
                )
                disposable = disposable_response.json()
                disposable_child_response = await client.post(
                    "/api/subagents",
                    json={
                        **common,
                        "name": "API disposable child",
                        "parent_agent_id": disposable["id"],
                    },
                )
                disposable_child = disposable_child_response.json()
                disconnected_root = await client.delete(
                    f"/api/subagent-nodes/{disposable['placements'][0]['id']}"
                )
                self.assertEqual(disconnected_root.status_code, 200)
                self.assertIsNone(disconnected_root.json()["parent_node_id"])
                self.assertEqual(disconnected_root.json()["removed_nodes"], 2)
                remaining_definition_ids = {
                    item["id"] for item in (await client.get("/api/subagents")).json()
                }
                self.assertTrue(
                    {disposable["id"], disposable_child["id"]}
                    <= remaining_definition_ids
                )
                disconnected = await client.delete(
                    f"/api/subagent-nodes/{nested_node['id']}"
                )
                self.assertEqual(disconnected.status_code, 200)
                self.assertEqual(disconnected.json()["removed_nodes"], 1)
                self.assertEqual(
                    (await client.get(f"/api/subagent-nodes/{nested_node['id']}")).status_code,
                    404,
                )
                missing_node_delete = await client.delete("/api/subagent-nodes/999999")
                self.assertEqual(missing_node_delete.status_code, 404)
                reconnected = await client.post(
                    f"/api/subagents/{second_child['id']}/connections",
                    json={"parent_node_id": parent["placements"][0]["id"]},
                )
                self.assertEqual(reconnected.status_code, 200)

                assigned = await client.put(
                    f"/api/subagents/{parent['id']}",
                    json={"child_agent_ids": [child["id"], second_child["id"]]},
                )
                self.assertEqual(assigned.status_code, 200)
                self.assertEqual(assigned.json()["child_count"], 2)
                listed = (await client.get("/api/subagents")).json()
                listed_by_id = {agent["id"]: agent for agent in listed}
                self.assertEqual(
                    listed_by_id[second_child["id"]]["parent_agent_id"], parent["id"]
                )
                self.assertTrue(
                    listed_by_id[second_child["id"]]["connected_to_supervisor"]
                )
                self.assertEqual(
                    listed_by_id[second_child["id"]]["parent_agent_ids"], [parent["id"]]
                )

                cycle = await client.put(
                    f"/api/subagents/{parent['id']}",
                    json={"parent_agent_id": child["id"]},
                )
                self.assertEqual(cycle.status_code, 400)
                protected = await client.delete(f"/api/subagents/{parent['id']}")
                self.assertEqual(protected.status_code, 409)
                self.assertIn("children first", protected.json()["error"])

                detached = await client.put(
                    f"/api/subagents/{parent['id']}", json={"child_agent_ids": []}
                )
                self.assertEqual(detached.status_code, 200)
                self.assertEqual(detached.json()["child_count"], 0)
                listed = (await client.get("/api/subagents")).json()
                listed_by_id = {agent["id"]: agent for agent in listed}
                self.assertIsNone(listed_by_id[child["id"]]["parent_agent_id"])
                self.assertIsNone(listed_by_id[second_child["id"]]["parent_agent_id"])
                self.assertEqual(
                    (await client.delete(f"/api/subagents/{parent['id']}")).status_code,
                    200,
                )

        asyncio.run(exercise_api())

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
                read = await client.patch(
                    f"/api/heartbeat/notifications/{run_id}/read"
                )
                self.assertEqual(read.status_code, 200)
                unread = await client.get(
                    "/api/heartbeat/notifications?unread_only=true"
                )
                self.assertEqual(unread.json(), {"notifications": []})
                history = await client.get("/api/heartbeat/notifications")
                self.assertIsNotNone(
                    history.json()["notifications"][0]["read_at"]
                )
                deleted = await client.delete(
                    f"/api/heartbeat/notifications/{run_id}"
                )
                self.assertEqual(deleted.status_code, 200)
                missing = await client.delete(
                    f"/api/heartbeat/notifications/{run_id}"
                )
                self.assertEqual(missing.status_code, 404)

        asyncio.run(exercise_api())

    def test_admin_delete_api_distinguishes_dependencies_from_missing_records(self):
        import httpx
        import server as web_server

        db.init()
        model = db.add_model(
            "Delete model", "delete/model", "Ollama",
            "http://localhost:11434/v1", "private-model-key",
        )
        mcp_server = db.add_server(
            "Delete server", "delete-server", env={"PRIVATE_TOKEN": "server-secret"}
        )
        subagent = db.add_subagent(
            "Delete helper", "Handles deletion tests.", "",
            model["id"], mcp_server["id"],
        )

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                models = (await client.get("/api/models")).json()
                servers = (await client.get("/api/mcp-servers")).json()
                agents = (await client.get("/api/subagents")).json()
                self.assertNotIn("api_key", models[0])
                self.assertNotIn("server-secret", json.dumps(servers))
                self.assertNotIn("private-model-key", json.dumps(agents))
                self.assertNotIn("server-secret", json.dumps(agents))

                model_conflict = await client.delete(f"/api/models/{model['id']}")
                server_conflict = await client.delete(
                    f"/api/mcp-servers/{mcp_server['id']}"
                )
                self.assertEqual(model_conflict.status_code, 409)
                self.assertEqual(server_conflict.status_code, 409)
                self.assertIn("Delete helper", model_conflict.json()["error"])
                self.assertIn("Delete helper", server_conflict.json()["error"])

                self.assertEqual(
                    (await client.delete(f"/api/subagents/{subagent['id']}")).status_code,
                    200,
                )
                self.assertEqual(
                    (await client.delete(f"/api/models/{model['id']}")).status_code,
                    200,
                )
                self.assertEqual(
                    (
                        await client.delete(f"/api/mcp-servers/{mcp_server['id']}")
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    (await client.delete(f"/api/models/{model['id']}")).status_code,
                    404,
                )

        asyncio.run(exercise_api())

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
                self.assertEqual(generic_setup.status_code, 200)
                self.assertFalse(generic_setup.json()["configured"])
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
                    {"X-API-Key": ""},
                )
                self.assertTrue(named_header.json()["headers_configured"])
                self.assertEqual(
                    db.build_server_spec(remote_auth.json()["id"])["headers"],
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

    def test_generic_mcp_setup_actions_are_driven_by_saved_configuration(self):
        import httpx
        import server as web_server

        db.init()

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                local = await client.post(
                    "/api/mcp-servers",
                    json={
                        "name": "Configured local",
                        "transport": "stdio",
                        "connection": "local-server",
                        "setup_command": f'{sys.executable} -c "print(123)"',
                    },
                )
                self.assertEqual(local.status_code, 200)
                setup = await client.get(
                    f"/api/mcp-servers/{local.json()['id']}/setup"
                )
                self.assertTrue(setup.json()["configured"])
                self.assertTrue(setup.json()["command"]["configured"])
                ran = await client.post(
                    f"/api/mcp-servers/{local.json()['id']}/setup/actions/run_command"
                )
                self.assertEqual(ran.status_code, 200)
                self.assertEqual(ran.json()["message"], "123")

                remote = await client.post(
                    "/api/mcp-servers",
                    json={
                        "name": "Configured remote",
                        "transport": "streamable_http",
                        "connection": "https://example.test/mcp",
                        "auth_scheme": "oauth",
                    },
                )
                self.assertEqual(remote.status_code, 200)
                remote_setup = await client.get(
                    f"/api/mcp-servers/{remote.json()['id']}/setup"
                )
                self.assertTrue(remote_setup.json()["oauth"]["enabled"])
                self.assertFalse(remote_setup.json()["oauth"]["connected"])
                test = await client.post(
                    f"/api/mcp-servers/{remote.json()['id']}/test"
                )
                self.assertEqual(test.status_code, 409)
                self.assertIn("Connect OAuth", test.json()["error"])

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

    def test_telegram_reply_mode_api_updates_the_running_bridge(self):
        import httpx
        import server as web_server

        db.init()

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                voice_mode = await client.put(
                    "/api/telegram", json={"reply_mode": "voice"}
                )
                self.assertEqual(voice_mode.status_code, 200)
                self.assertEqual(voice_mode.json()["reply_mode"], "voice")
                self.assertEqual(web_server.telegram_service.reply_mode, "voice")

                invalid = await client.put(
                    "/api/telegram", json={"reply_mode": "automatic"}
                )
                self.assertEqual(invalid.status_code, 400)

                text_mode = await client.put(
                    "/api/telegram", json={"reply_mode": "text"}
                )
                self.assertEqual(text_mode.json()["reply_mode"], "text")
                self.assertEqual(web_server.telegram_service.reply_mode, "text")

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

    def test_confirmation_handler_reaches_the_agent_graph_stream(self):
        observed = []

        def compile_graph(*_args, **_kwargs):
            class Graph:
                def stream(self, state, **_stream_options):
                    from langchain_core.messages import AIMessage

                    observed.append(mounir_tools.request_confirmation("safe?"))
                    yield {"type": "custom", "data": "done"}
                    yield {
                        "type": "values",
                        "data": {
                            **state,
                            "messages": [*state["messages"], AIMessage(content="done")],
                        },
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

    def test_confirmation_context_reaches_toolnode_executor(self):
        observed = []

        def fake_chat(_messages, tool_calls_out=None, **_kwargs):
            tool_calls_out.append(
                SimpleNamespace(
                    id="bash_1",
                    function=SimpleNamespace(
                        name="bash", arguments={"command": "safe-command"}
                    ),
                )
            )
            if False:
                yield ""

        agent = Agent(conversation=Conversation(system_prompt="test"))
        with (
            patch.object(langgraph_agent.llm, "chat_stream", fake_chat),
            patch.object(mounir_tools, "confirm_fn", return_value=False),
            mounir_tools.use_confirmation_handler(
                lambda action: observed.append(action) or False
            ),
        ):
            reply = "".join(agent.respond("run it"))

        self.assertIn("declined", reply)
        self.assertEqual(observed, ["safe-command"])

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

            def respond(self, text, *, voice=False):
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

    def test_telegram_confirmation_stays_text_and_voice_mode_reply_stays_voice(self):
        db.init()

        class FakeBot:
            def __init__(self):
                self.sent = []
                self.sent_voice = []
                self.confirmation_sent = threading.Event()

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))
                if "Approval required" in text:
                    self.confirmation_sent.set()

            def send_voice(self, chat_id, voice, **kwargs):
                self.sent_voice.append((chat_id, voice.read(), kwargs))

            def send_chat_action(self, *_args, **_kwargs):
                return None

        class FakeAgent:
            conversation = Conversation(system_prompt="test")
            confirmed = False

            def respond(self, _text, *, voice=False):
                self.confirmed = mounir_tools.request_confirmation("Send the message?")
                self.voice = voice
                yield "Action completed" if self.confirmed else "Action denied"

        fake_bot = FakeBot()
        fake_agent = FakeAgent()
        bridge = TelegramBridge(
            agent=fake_agent,
            token="123:abc",
            chat_id="42",
            reply_mode="voice",
            confirm_timeout=2,
            bot_factory=lambda _token: fake_bot,
        )
        worker = threading.Thread(target=bridge._answer, args=(42, "Do it"))

        with (
            patch("mounir.telegram_bridge.tts.synthesize_wav", return_value=b"wav"),
            patch.object(bridge, "_encode_voice", return_value=io.BytesIO(b"opus")),
        ):
            worker.start()
            self.assertTrue(fake_bot.confirmation_sent.wait(1))
            bridge._handle_text(SimpleNamespace(chat=SimpleNamespace(id=42), text="YES!"))
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertTrue(fake_agent.confirmed)
        self.assertTrue(fake_agent.voice)
        self.assertIn('Reply "yes" to allow or "no" to deny', fake_bot.sent[-1][1])
        self.assertEqual(fake_bot.sent_voice, [(42, b"opus", {})])

    def test_telegram_confirmation_keeps_waiting_after_an_unclear_reply(self):
        db.init()

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
        bridge._confirm_event = threading.Event()

        handled = bridge._handle_confirmation_reply(42, "maybe")

        self.assertTrue(handled)
        self.assertFalse(bridge._confirm_event.is_set())
        self.assertIn("Please reply", fake_bot.sent[-1][1])
        self.assertTrue(bridge._handle_confirmation_reply(42, "No."))
        self.assertTrue(bridge._confirm_event.is_set())
        self.assertFalse(bridge._confirm_answer)

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

    def test_telegram_voice_message_uses_configured_stt(self):
        db.init()

        class FakeBot:
            def __init__(self):
                self.sent = []
                self.handlers = []

            def register_message_handler(self, handler, **filters):
                self.handlers.append((handler, filters))

            def get_file(self, file_id):
                self.requested_file_id = file_id
                return SimpleNamespace(file_path="voice/note.oga")

            def download_file(self, file_path):
                self.downloaded_path = file_path
                return b"telegram audio"

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))

            def reply_to(self, message, text):
                self.sent.append((message.chat.id, text, {}))

            def send_chat_action(self, *_args, **_kwargs):
                return None

        class FakeAgent:
            def __init__(self):
                self.conversation = Conversation(system_prompt="test")
                self.requests = []

            def respond(self, text):
                self.requests.append(text)
                yield "Voice-note reply"

        fake_bot = FakeBot()
        fake_agent = FakeAgent()
        bridge = TelegramBridge(
            agent=fake_agent,
            token="123:abc",
            chat_id="42",
            reply_mode="text",
            bot_factory=lambda _token: fake_bot,
        )
        message = SimpleNamespace(
            chat=SimpleNamespace(id=42),
            voice=SimpleNamespace(file_id="voice-file-id"),
            audio=None,
        )

        with (
            patch.object(bridge, "_decode_audio", return_value=[0.1, -0.1]) as decode,
            patch(
                "mounir.telegram_bridge.stt.transcribe",
                return_value=("hello", "en"),
            ) as transcribe,
        ):
            bridge._handle_audio(message)

        self.assertEqual(fake_bot.requested_file_id, "voice-file-id")
        self.assertEqual(fake_bot.downloaded_path, "voice/note.oga")
        self.assertTrue(
            any(
                filters.get("content_types") == ["voice", "audio"]
                for _handler, filters in fake_bot.handlers
            )
        )
        decode.assert_called_once_with(b"telegram audio")
        transcribe.assert_called_once_with([0.1, -0.1])
        self.assertEqual(fake_agent.requests, ["hello"])
        self.assertEqual(fake_bot.sent[-1][1], "Voice-note reply")

    def test_telegram_commands_persist_reply_mode_and_control_typed_replies(self):
        db.init()
        db.update_telegram_settings(reply_mode="text")

        class FakeBot:
            def __init__(self):
                self.sent = []
                self.sent_voice = []

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))

            def send_voice(self, chat_id, voice, **kwargs):
                self.sent_voice.append((chat_id, voice.read(), kwargs))

            def send_chat_action(self, *_args, **_kwargs):
                return None

        class FakeAgent:
            def __init__(self):
                self.conversation = Conversation(system_prompt="test")
                self.requests = []

            def respond(self, text, *, voice=False):
                self.requests.append((text, voice))
                yield "Spoken reply"

        fake_bot = FakeBot()
        fake_agent = FakeAgent()
        bridge = TelegramBridge(
            agent=fake_agent,
            token="123:abc",
            chat_id="42",
            reply_mode="text",
            bot_factory=lambda _token: fake_bot,
        )

        with (
            patch("mounir.telegram_bridge.tts.synthesize_wav", return_value=b"wav") as synthesize,
            patch.object(bridge, "_encode_voice", return_value=io.BytesIO(b"opus")),
        ):
            bridge._handle_text(
                SimpleNamespace(chat=SimpleNamespace(id=42), text="/vocal")
            )
            bridge._handle_text(
                SimpleNamespace(chat=SimpleNamespace(id=42), text="Typed request")
            )
            bridge._handle_text(
                SimpleNamespace(chat=SimpleNamespace(id=42), text="/status")
            )

        self.assertEqual(bridge.reply_mode, "voice")
        self.assertEqual(db.get_telegram_settings()["reply_mode"], "voice")
        self.assertEqual(fake_agent.requests, [("Typed request", True)])
        synthesize.assert_called_once_with("Spoken reply")
        self.assertEqual(fake_bot.sent_voice, [(42, b"opus", {})])
        self.assertIn("Voice replies enabled", fake_bot.sent[0][1])
        self.assertIn("Reply mode: Voice", fake_bot.sent[-1][1])

        bridge._handle_text(SimpleNamespace(chat=SimpleNamespace(id=42), text="/text"))
        self.assertEqual(bridge.reply_mode, "text")
        self.assertEqual(db.get_telegram_settings()["reply_mode"], "text")

    def test_telegram_voice_reply_falls_back_to_text_when_tts_fails(self):
        db.init()

        class FakeBot:
            def __init__(self):
                self.sent = []

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def send_message(self, chat_id, text, **kwargs):
                self.sent.append((chat_id, text, kwargs))

            def send_chat_action(self, *_args, **_kwargs):
                return None

        class FakeAgent:
            conversation = Conversation(system_prompt="test")

            @staticmethod
            def respond(_text, *, voice=False):
                self.assertTrue(voice)
                yield "Readable fallback"

        fake_bot = FakeBot()
        bridge = TelegramBridge(
            agent=FakeAgent(),
            token="123:abc",
            chat_id="42",
            reply_mode="voice",
            bot_factory=lambda _token: fake_bot,
        )
        with patch(
            "mounir.telegram_bridge.tts.synthesize_wav",
            side_effect=RuntimeError("TTS offline"),
        ):
            bridge._answer(42, "Hello")

        self.assertEqual(fake_bot.sent[-1][1], "Readable fallback")

    def test_telegram_registers_its_command_menu_when_polling_connects(self):
        class FakeBot:
            def __init__(self):
                self.commands = []
                self.polling_started = False

            def register_message_handler(self, *_args, **_kwargs):
                return None

            def get_me(self):
                return SimpleNamespace(username="mounir_bot")

            def set_my_commands(self, commands):
                self.commands = commands

            def infinity_polling(self, **_kwargs):
                self.polling_started = True

        fake_bot = FakeBot()
        bridge = TelegramBridge(
            token="123:abc",
            chat_id="42",
            reply_mode="text",
            bot_factory=lambda _token: fake_bot,
        )

        bridge._poll()

        self.assertTrue(fake_bot.polling_started)
        self.assertEqual(
            [command.command for command in fake_bot.commands],
            ["vocal", "text", "status", "reset", "help"],
        )

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
