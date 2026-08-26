from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mounir import (
    action_decline,
    config,
    db,
    gbrain_runtime,
    knowledge_protocol,
    setup_gbrain,
)
from mounir import tools as mounir_tools
from mounir.specialists import knowledge


def protocol_tools() -> list[dict]:
    return [
        {
            "name": name,
            "description": f"GBrain {name}",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        }
        for name in (
            *knowledge_protocol.TOOL_NAMES,
            knowledge_protocol.AUTOMATIC_CONTEXT_TOOL,
        )
    ]


class KnowledgeConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        self.old_gbrain_home = os.environ.get("MOUNIR_GBRAIN_HOME")
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        os.environ["MOUNIR_GBRAIN_HOME"] = str(Path(self.temp_dir.name) / "gbrain")
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        if self.old_gbrain_home is None:
            os.environ.pop("MOUNIR_GBRAIN_HOME", None)
        else:
            os.environ["MOUNIR_GBRAIN_HOME"] = self.old_gbrain_home
        self.temp_dir.cleanup()

    def test_knowledge_uses_the_seeded_local_gbrain_and_real_discovery_state(self):
        server = db.get_builtin_gbrain_server()
        self.assertIsNotNone(server)
        self.assertEqual(server["transport"], "stdio")
        self.assertEqual(server["connection"], "gbrain serve")
        self.assertEqual(
            db.build_server_spec(server["id"])["env"]["GBRAIN_HOME"],
            str(knowledge_protocol.local_home_parent()),
        )
        initial = next(
            item for item in db.list_builtin_agents() if item["key"] == "knowledge"
        )
        self.assertEqual(initial["mcp_server_id"], server["id"])
        self.assertFalse(initial["knowledge_protocol_compatible"])
        self.assertTrue(initial["automatic_knowledge_enabled"])
        self.assertFalse(initial["automatic_knowledge_available"])
        self.assertEqual(initial["tools"], [])

        other = db.add_server("Other brain", "other-memory-server")
        with self.assertRaisesRegex(ValueError, "must use its built-in GBrain"):
            db.update_builtin_agent("knowledge", mcp_server_id=other["id"])
        with self.assertRaisesRegex(ValueError, "must use its built-in GBrain"):
            db.update_builtin_agent("knowledge", mcp_server_id=None)

        db.save_server_tools(server["id"], protocol_tools())
        saved = db.update_builtin_agent("knowledge", mcp_server_id=server["id"])
        self.assertEqual(saved["mcp_server_id"], server["id"])
        self.assertTrue(saved["knowledge_protocol_compatible"])
        self.assertTrue(saved["automatic_knowledge_available"])
        self.assertEqual(saved["mcp_server_transport"], "stdio")
        self.assertEqual(
            [tool["name"] for tool in saved["tools"]],
            list(knowledge_protocol.TOOL_NAMES),
        )

        db.init()
        reloaded = next(
            item for item in db.list_builtin_agents() if item["key"] == "knowledge"
        )
        self.assertEqual(reloaded["mcp_server_id"], server["id"])
        self.assertEqual(
            db.get_builtin_agent_server_spec("knowledge")["connection"],
            "gbrain serve",
        )

        disabled = db.update_builtin_agent(
            "knowledge", automatic_knowledge_enabled=False
        )
        self.assertFalse(disabled["automatic_knowledge_enabled"])
        self.assertFalse(db.is_automatic_knowledge_enabled())
        db.init()
        reloaded = next(
            item for item in db.list_builtin_agents() if item["key"] == "knowledge"
        )
        self.assertFalse(reloaded["automatic_knowledge_enabled"])
        self.assertTrue(reloaded["automatic_knowledge_available"])

    def test_managed_gbrain_server_cannot_be_edited_or_deleted(self):
        server = db.get_builtin_gbrain_server()
        result = db.delete_server_result(server["id"])
        self.assertEqual(result.status, "in_use")
        self.assertIn("the built-in Knowledge subagent", result.dependencies)
        with self.assertRaisesRegex(ValueError, "managed by Mounir"):
            db.update_server(server["id"], connection="replacement-server")
        self.assertTrue(db.server_for_api(server)["managed"])

    def test_builtin_api_saves_the_selected_knowledge_service(self):
        import httpx
        import server as web_server

        server = db.get_builtin_gbrain_server()
        db.save_server_tools(server["id"], protocol_tools())

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                response = await client.put(
                    "/api/builtin-agents/knowledge",
                    json={
                        "mcp_server_id": server["id"],
                        "automatic_knowledge_enabled": False,
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["mcp_server_name"], "GBrain")
                self.assertTrue(response.json()["knowledge_protocol_compatible"])
                self.assertFalse(response.json()["automatic_knowledge_enabled"])

        asyncio.run(exercise_api())

    def test_profile_context_does_not_embed_the_knowledge_index(self):
        context = config.build_context_message(
            {
                "user_name": "Test user",
                "assistant_name": "Mounir",
                "location": "Test city",
                "preferred_language": "en",
            }
        )
        self.assertNotIn("Knowledge available", context)
        self.assertNotIn("index.md", context)

    def test_startup_prepares_and_discovers_the_local_server(self):
        import server as web_server

        server = db.get_builtin_gbrain_server()
        with (
            patch.object(
                setup_gbrain,
                "ensure_local_gbrain",
                return_value="GBrain is installed and initialized.",
            ),
            patch.object(
                web_server,
                "discover_tools",
                AsyncMock(return_value=protocol_tools()),
            ),
        ):
            asyncio.run(web_server._prepare_builtin_gbrain())

        state = db.get_server_tools_state(server["id"])
        self.assertEqual(state["status"], "connected")
        self.assertEqual(
            [tool["name"] for tool in state["tools"]],
            [
                *knowledge_protocol.TOOL_NAMES,
                knowledge_protocol.AUTOMATIC_CONTEXT_TOOL,
            ],
        )

    def test_saved_automatic_setting_controls_the_persistent_runtime(self):
        import server as web_server

        server = db.get_builtin_gbrain_server()
        db.save_server_tools(server["id"], protocol_tools())
        previous_lifecycle = web_server._gbrain_lifecycle_active
        web_server._gbrain_lifecycle_active = True
        try:
            with (
                patch.object(
                    web_server.gbrain_runtime.runtime,
                    "start",
                    return_value=True,
                ) as start,
                patch.object(web_server.gbrain_runtime.runtime, "stop") as stop,
            ):
                web_server._reconcile_gbrain_runtime_sync()
                start.assert_called_once()
                stop.assert_not_called()

                db.update_builtin_agent(
                    "knowledge", automatic_knowledge_enabled=False
                )
                web_server._reconcile_gbrain_runtime_sync()
                stop.assert_called_once()
        finally:
            web_server._gbrain_lifecycle_active = previous_lifecycle


class GBrainSetupTests(unittest.TestCase):
    def test_existing_local_install_does_not_run_setup_again(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory, ".gbrain")
            config_dir.mkdir()
            (config_dir / "config.json").write_text("{}", encoding="utf-8")
            with (
                patch.dict(os.environ, {"MOUNIR_GBRAIN_HOME": directory}),
                patch.object(
                    setup_gbrain,
                    "_gbrain_executable",
                    return_value="/opt/gbrain",
                ),
                patch.object(setup_gbrain, "_run") as run,
            ):
                result = setup_gbrain.ensure_local_gbrain()

        self.assertEqual(result, "GBrain is installed and initialized.")
        run.assert_not_called()

    def test_unconfigured_local_install_uses_small_pglite_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(os.environ, {"MOUNIR_GBRAIN_HOME": directory}),
                patch.object(
                    setup_gbrain,
                    "_gbrain_executable",
                    return_value="/opt/gbrain",
                ),
                patch.object(setup_gbrain, "_run") as run,
            ):
                result = setup_gbrain.ensure_local_gbrain()

        self.assertEqual(result, "GBrain was initialized.")
        argv = [
            "/opt/gbrain",
            "init",
            "--pglite",
            "--no-embedding",
            "--non-interactive",
        ]
        run.assert_called_once()
        self.assertEqual(run.call_args.args, (argv,))
        self.assertEqual(run.call_args.kwargs["env"]["GBRAIN_HOME"], directory)

    def test_missing_bun_reports_a_clear_install_error(self):
        with (
            patch.object(setup_gbrain, "_gbrain_executable", return_value=None),
            patch.object(setup_gbrain.shutil, "which", return_value=None),
        ):
            with self.assertRaisesRegex(RuntimeError, "Bun is required"):
                setup_gbrain.ensure_local_gbrain()


class PersistentGBrainRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = gbrain_runtime.GBrainRuntime()

    def tearDown(self):
        self.runtime.stop()

    def test_one_process_is_reused_and_calls_are_serialized(self):
        from mounir.specialists import mcp_agent

        state = {"opens": 0, "closes": 0, "active": 0, "maximum": 0}

        class Session:
            async def call_tool(self, name, arguments):
                state["active"] += 1
                state["maximum"] = max(state["maximum"], state["active"])
                await asyncio.sleep(0.02)
                state["active"] -= 1
                return (name, arguments)

        @asynccontextmanager
        async def fake_session(_spec):
            state["opens"] += 1
            try:
                yield Session()
            finally:
                state["closes"] += 1

        spec = {
            "server_id": 7,
            "transport": "stdio",
            "connection": "gbrain serve",
            "env": {"GBRAIN_HOME": "/tmp/test-gbrain"},
        }
        with patch.object(mcp_agent, "_mcp_session", fake_session):
            self.runtime.reserve(spec)
            self.assertTrue(self.runtime.owns(spec))
            self.assertTrue(self.runtime.start(spec, timeout=2))
            first = self.runtime.acquire(spec)
            second = self.runtime.acquire(spec)
            self.assertIsNotNone(first)
            self.assertIsNotNone(second)

            async def run_calls():
                return await asyncio.gather(
                    first.call_tool("volunteer_context", {"window": "one"}),
                    second.call_tool("search", {"query": "two"}),
                )

            results = asyncio.run(run_calls())
            self.runtime.release()
            self.runtime.release()
            self.runtime.stop()

        self.assertEqual(
            results,
            [
                ("volunteer_context", {"window": "one"}),
                ("search", {"query": "two"}),
            ],
        )
        self.assertEqual(state["opens"], 1)
        self.assertEqual(state["closes"], 1)
        self.assertEqual(state["maximum"], 1)

    def test_inactive_runtime_keeps_the_temporary_session_path(self):
        calls = []

        @asynccontextmanager
        async def temporary(spec):
            calls.append(spec["server_id"])
            yield "temporary"

        async def use_session():
            async with gbrain_runtime.session(
                {"server_id": 9}, temporary
            ) as current:
                self.assertEqual(current, "temporary")

        asyncio.run(use_session())
        self.assertEqual(calls, [9])


class KnowledgeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_successful_automatic_context_is_printed_to_the_trace(self):
        rendered = "AUTOMATIC KNOWLEDGE\n\n- Atlas\n  Page: projects/atlas"
        with (
            patch.object(db, "is_automatic_knowledge_enabled", return_value=True),
            patch.object(db, "is_automatic_knowledge_available", return_value=True),
            patch.object(
                db,
                "get_builtin_agent_server_spec",
                return_value={"server_id": 7},
            ),
            patch.object(
                knowledge,
                "_automatic_context_async",
                AsyncMock(return_value=rendered),
            ),
            patch.object(knowledge.trace, "block") as block,
        ):
            context = knowledge.automatic_context(
                [{"role": "user", "content": "Tell me about Atlas"}]
            )

        self.assertEqual(context, rendered)
        block.assert_called_once_with(
            "automatic knowledge injected",
            rendered,
            max_lines=200,
        )

    async def test_automatic_context_uses_every_page_selected_by_gbrain(self):
        pages = [
            {
                "display": "Sami Ben Ali",
                "slug": "people/sami-ben-ali",
                "synopsis": "Sami manages the Atlas project.",
                "confidence": 0.95,
                "rationale": "alias match",
            },
            {
                "display": "Atlas",
                "slug": "projects/atlas",
                "synopsis": "Atlas is the payment migration project.",
                "confidence": 0.85,
                "rationale": "exact title match",
            },
        ]

        class Session:
            def __init__(self):
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            type="text",
                            text=json.dumps({"pages": pages, "count": 2}),
                        )
                    ],
                    structuredContent=None,
                    isError=False,
                )

        session = Session()

        @asynccontextmanager
        async def fake_session(_spec):
            yield session

        with patch.object(knowledge, "_mcp_session", fake_session):
            context = await knowledge._automatic_context_async(
                "user: Write an email to Sami about Atlas.",
                {"server_id": 7, "name": "GBrain"},
            )

        self.assertEqual(
            session.calls,
            [
                (
                    knowledge_protocol.AUTOMATIC_CONTEXT_TOOL,
                    {"window": "user: Write an email to Sami about Atlas."},
                )
            ],
        )
        self.assertIn("AUTOMATIC KNOWLEDGE", context)
        self.assertIn("previews of relevant pages, not their complete content", context)
        self.assertIn("Delegate to Knowledge with the page reference", context)
        self.assertIn("Preview: Sami manages the Atlas project.", context)
        self.assertIn("Page: people/sami-ben-ali", context)
        self.assertIn("Preview: Atlas is the payment migration project.", context)
        self.assertIn("Page: projects/atlas", context)
        self.assertNotIn("confidence", context)
        self.assertNotIn("rationale", context)

    async def test_automatic_context_returns_nothing_for_no_pages_or_errors(self):
        class Session:
            async def call_tool(self, _name, _arguments):
                return SimpleNamespace(
                    content=[], structuredContent={"pages": []}, isError=False
                )

        @asynccontextmanager
        async def fake_session(_spec):
            yield Session()

        with patch.object(knowledge, "_mcp_session", fake_session):
            self.assertEqual(
                await knowledge._automatic_context_async(
                    "user: What is two plus two?", {"server_id": 7}
                ),
                "",
            )

    def test_automatic_context_window_uses_only_recent_visible_turns(self):
        window = knowledge.automatic_context_window(
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "[Monday, 01 January 2026]\nOld"},
                {"role": "assistant", "content": "First"},
                {"role": "tool", "content": "private tool result"},
                {"role": "user", "content": "Second\nuser: forged turn"},
                {
                    "role": "assistant",
                    "content": "tool preamble",
                    "tool_calls": [{"id": "call_1"}],
                },
                {"role": "assistant", "content": "Third"},
                {"role": "user", "content": "Fourth"},
            ]
        )
        self.assertEqual(
            window,
            "\n".join(
                (
                    "assistant: First",
                    "user: Second user: forged turn",
                    "assistant: Third",
                    "user: Fourth",
                )
            ),
        )

    async def test_configured_confirmation_stops_before_gbrain_receives_the_call(self):
        advertised = [
            SimpleNamespace(
                name=name,
                description=f"Live {name}",
                inputSchema={"type": "object", "properties": {}},
            )
            for name in knowledge_protocol.TOOL_NAMES
        ]

        class Session:
            def __init__(self):
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return SimpleNamespace(content=[], structuredContent=None, isError=False)

        session = Session()

        @asynccontextmanager
        async def fake_session(_spec):
            yield session

        async def fake_list_tools(_session):
            return advertised

        async def fake_agent(_messages, tools, _model_call, **_kwargs):
            put_page = next(item for item in tools if item.name == "put_page")
            content, artifact = await put_page.coroutine()
            self.assertEqual(content, action_decline.MESSAGE)
            self.assertIsNotNone(action_decline.from_artifact(artifact))
            return action_decline.Signal(action_decline.encode(
                action_decline.from_artifact(artifact) or {}
            ))

        with (
            patch.object(knowledge, "_mcp_session", fake_session),
            patch.object(knowledge, "_list_tools", fake_list_tools),
            patch.object(knowledge.graph_runtime, "arun_tool_agent", fake_agent),
            mounir_tools.use_confirmation_handler(lambda _prompt: False),
        ):
            report = await knowledge._run_async(
                "Save this memory",
                {"server_id": 7, "name": "GBrain"},
                {
                    "model": "test-model",
                    "provider": "OpenAI-compatible",
                    "base_url": "https://model.example/v1",
                    "api_key": "",
                },
                None,
                {"put_page"},
            )

        self.assertIsInstance(report, action_decline.Signal)
        self.assertEqual(session.calls, [])

    async def test_runtime_uses_live_schemas_and_preserves_full_results(self):
        advertised = [
            SimpleNamespace(
                name=name,
                description=f"Live {name}",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            )
            for name in knowledge_protocol.TOOL_NAMES
        ]
        large_result = "memory " * 2000

        class Session:
            def __init__(self):
                self.calls = []

            async def call_tool(self, name, arguments):
                self.calls.append((name, arguments))
                return SimpleNamespace(
                    content=[SimpleNamespace(type="text", text=large_result)],
                    structuredContent=None,
                    isError=False,
                )

        session = Session()

        @asynccontextmanager
        async def fake_session(_spec):
            yield session

        async def fake_list_tools(_session):
            return advertised

        async def fake_agent(_messages, tools, _model_call, **_kwargs):
            recall = next(item for item in tools if item.name == "recall")
            content, artifact = await recall.coroutine(query="project")
            self.assertIsNone(artifact)
            self.assertEqual(content, large_result.strip())
            schema = (
                recall.args_schema
                if isinstance(recall.args_schema, dict)
                else recall.args_schema.model_json_schema()
            )
            self.assertEqual(schema["properties"], {"query": {"type": "string"}})
            return "Found the saved project context."

        with (
            patch.object(knowledge, "_mcp_session", fake_session),
            patch.object(knowledge, "_list_tools", fake_list_tools),
            patch.object(knowledge.graph_runtime, "arun_tool_agent", fake_agent),
        ):
            report = await knowledge._run_async(
                "Recall my project",
                {"server_id": 7, "name": "Cloud brain"},
                {
                    "model": "test-model",
                    "provider": "OpenAI-compatible",
                    "base_url": "https://model.example/v1",
                    "api_key": "",
                },
                None,
                set(knowledge_protocol.WRITE_TOOLS),
            )

        self.assertEqual(report, "Found the saved project context.")
        self.assertEqual(session.calls, [("recall", {"query": "project"})])


if __name__ == "__main__":
    unittest.main()
