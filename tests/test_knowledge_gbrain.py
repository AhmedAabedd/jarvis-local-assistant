from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mounir import action_decline, config, db, knowledge_protocol, setup_gbrain
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
        for name in knowledge_protocol.TOOL_NAMES
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
                    json={"mcp_server_id": server["id"]},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["mcp_server_name"], "GBrain")
                self.assertTrue(response.json()["knowledge_protocol_compatible"])

        asyncio.run(exercise_api())

    def test_old_knowledge_index_is_not_automatically_injected(self):
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
            list(knowledge_protocol.TOOL_NAMES),
        )


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


class KnowledgeRuntimeTests(unittest.IsolatedAsyncioTestCase):
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
