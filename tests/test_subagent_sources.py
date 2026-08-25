from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mounir import db
from mounir.specialists import mcp_agent


class SubagentSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        db.init()
        self.model = db.add_model(
            "Source model",
            "source/model",
            "OpenAI compatible",
            "http://localhost:11434/v1",
            "",
        )

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temp_dir.cleanup()

    def test_prompt_only_subagent_needs_no_server(self):
        agent = db.add_subagent(
            "Validator",
            "Validates the previous result.",
            "Check the supplied result carefully.",
            self.model["id"],
            None,
            confirm_tools=[],
        )

        self.assertIsNone(agent["mcp_server_id"])
        self.assertEqual(agent["mcp_sources"], [])
        spec = db.build_specs()[0]
        self.assertEqual(spec["mcp_sources"], [])
        self.assertEqual(spec["connection"], "")

    def test_subagent_can_select_tools_from_multiple_servers(self):
        web = db.add_server("Web tools", "web-server")
        files = db.add_server("File tools", "file-server")
        db.save_server_tools(
            web["id"],
            [
                {"name": "search", "description": "Search the web."},
                {"name": "fetch", "description": "Fetch one page."},
            ],
        )
        db.save_server_tools(
            files["id"],
            [{"name": "read_file", "description": "Read a local file."}],
        )
        agent = db.add_subagent(
            "Research validator",
            "Researches and validates supplied material.",
            "Use only the granted tools.",
            self.model["id"],
            None,
            confirm_tools=[],
            mcp_sources=[
                {"mcp_server_id": web["id"], "enabled_tools": ["search"]},
                {"mcp_server_id": files["id"], "enabled_tools": None},
            ],
        )

        self.assertEqual(
            [source["mcp_server_id"] for source in agent["mcp_sources"]],
            [web["id"], files["id"]],
        )
        specs = db.build_specs()
        self.assertEqual(
            [source["allowed_tools"] for source in specs[0]["mcp_sources"]],
            [["search"], None],
        )
        capability = next(
            item for item in db.get_heartbeat_capabilities()
            if item["id"] == agent["id"]
        )
        self.assertEqual(
            {(tool["name"], tool["server_name"]) for tool in capability["tools"]},
            {("search", "Web tools"), ("read_file", "File tools")},
        )
        agent_key = f"mcp:{agent['id']}"
        task = db.create_heartbeat_task(
            name="Cross-server watch",
            instructions="Use both connected sources.",
            selected_agents=[agent_key],
            selected_tools=[
                {"agent_key": agent_key, "tool_name": "search"},
                {"agent_key": agent_key, "tool_name": "read_file"},
            ],
        )
        target = next(
            item for item in db.get_heartbeat_targets(task["id"])
            if item["id"] == agent["id"]
        )
        self.assertEqual(
            [source["allowed_tools"] for source in target["mcp_sources"]],
            [["search"], ["read_file"]],
        )
        db.update_subagent_node(
            agent["node_id"], enabled_tools=[f"{web['id']}:search"]
        )
        db.update_subagent(
            agent["id"], description="Updated description", mcp_sources=agent["mcp_sources"]
        )
        self.assertEqual(
            db.get_subagent_node(agent["node_id"])["enabled_tools"],
            [f"{web['id']}:search"],
        )
        self.assertEqual(db.delete_server_result(web["id"]).status, "in_use")

        updated = db.update_subagent(agent["id"], mcp_sources=[])
        self.assertEqual(updated["mcp_sources"], [])
        self.assertIsNone(db.get_subagent_node(agent["node_id"])["enabled_tools"])
        self.assertTrue(db.delete_server_result(web["id"]).deleted)
        self.assertTrue(db.delete_server_result(files["id"]).deleted)

    def test_prompt_only_runtime_does_not_open_an_mcp_session(self):
        spec = {
            "name": "Prompt reviewer",
            "prompt": "Review the input.",
            "model": "source/model",
            "provider": "OpenAI compatible",
            "base_url": "http://localhost:11434/v1",
            "mcp_sources": [],
            "confirm_tools": [],
            "dedupe_tools": [],
        }

        async def fake_graph(messages, tools, _model_call, **_kwargs):
            self.assertEqual(tools, [])
            self.assertEqual(messages[-1]["content"], "Validate this")
            return "Validation passed."

        with (
            patch.object(mcp_agent, "_mcp_session") as session,
            patch.object(mcp_agent.graph_runtime, "arun_tool_agent", fake_graph),
        ):
            result = asyncio.run(mcp_agent._run_async("Validate this", spec, ""))

        self.assertEqual(result, "Validation passed.")
        session.assert_not_called()

    def test_duplicate_tool_names_remain_selectable_per_server(self):
        first = db.add_server("First source", "first-server")
        second = db.add_server("Second source", "second-server")
        for server in (first, second):
            db.save_server_tools(
                server["id"],
                [{"name": "search", "description": "Search this source."}],
            )
        agent = db.add_subagent(
            "Cross-source search",
            "Searches either source.",
            "Use the requested source.",
            self.model["id"],
            mcp_sources=[
                {"mcp_server_id": first["id"], "enabled_tools": None},
                {"mcp_server_id": second["id"], "enabled_tools": None},
            ],
            confirm_tools=[],
        )

        capability = next(
            item for item in db.get_heartbeat_capabilities()
            if item["id"] == agent["id"]
        )
        self.assertEqual(
            {tool["name"] for tool in capability["tools"]},
            {f"{first['id']}:search", f"{second['id']}:search"},
        )
        agent_key = f"mcp:{agent['id']}"
        task = db.create_heartbeat_task(
            name="Selected source watch",
            instructions="Search the selected source.",
            selected_agents=[agent_key],
            selected_tools=[{
                "agent_key": agent_key,
                "tool_name": f"{second['id']}:search",
            }],
        )
        target = next(
            item for item in db.get_heartbeat_targets(task["id"])
            if item["id"] == agent["id"]
        )
        self.assertEqual(len(target["mcp_sources"]), 1)
        self.assertEqual(target["mcp_sources"][0]["mcp_server_id"], second["id"])
        self.assertEqual(target["mcp_sources"][0]["allowed_tools"], ["search"])

    def test_multi_server_runtime_namespaces_duplicate_tool_names(self):
        class Tool:
            name = "search"
            description = "Search this source."
            inputSchema = {"type": "object", "properties": {}}

        class Session:
            async def list_tools(self, cursor=None):
                return SimpleNamespace(tools=[Tool()], nextCursor=None)

        @asynccontextmanager
        async def fake_session(_source):
            yield Session()

        observed = []

        async def fake_graph(_messages, tools, _model_call, **_kwargs):
            observed.extend(tool.name for tool in tools)
            return "Combined."

        spec = {
            "name": "Combined researcher",
            "prompt": "Use the correct source.",
            "model": "source/model",
            "provider": "OpenAI compatible",
            "base_url": "http://localhost:11434/v1",
            "confirm_tools": [],
            "dedupe_tools": [],
            "mcp_sources": [
                {
                    "mcp_server_id": 1,
                    "server_name": "Web",
                    "connection": "web-server",
                    "transport": "stdio",
                    "allowed_tools": None,
                },
                {
                    "mcp_server_id": 2,
                    "server_name": "Knowledge",
                    "connection": "knowledge-server",
                    "transport": "stdio",
                    "allowed_tools": None,
                },
            ],
        }
        with (
            patch.object(mcp_agent, "_mcp_session", fake_session),
            patch.object(mcp_agent.graph_runtime, "arun_tool_agent", fake_graph),
        ):
            result = asyncio.run(mcp_agent._run_async("Research", spec, ""))

        self.assertEqual(result, "Combined.")
        self.assertEqual(observed, ["Web__search", "Knowledge__search"])


if __name__ == "__main__":
    unittest.main()
