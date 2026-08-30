from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mounir import context_history, db
from mounir.agent import Agent
from mounir.memory import Conversation
from mounir.specialists import knowledge, media, system


class ContextHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temp_dir.cleanup()

    def _repeated_specs(self) -> tuple[dict, dict]:
        model = db.add_model(
            "History model",
            "history-model",
            "OpenAI compatible",
            "http://localhost:11434/v1",
            "",
        )
        server = db.add_server("History server", "history-server")
        agent = db.add_subagent(
            "History helper",
            "Remembers its own placement history.",
            "",
            model["id"],
            server["id"],
            connect_to_workflow=False,
        )
        workflow = db.create_workflow(
            name="History workflow", execution_mode="direct"
        )
        first = db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        second = db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        specs = {
            int(spec["node_id"]): spec for spec in db.build_specs(workflow["id"])
        }
        return specs[int(first["id"])], specs[int(second["id"])]

    def test_reused_placements_and_conversations_keep_separate_history(self):
        first, second = self._repeated_specs()
        live_history = context_history.ContextHistory()
        fresh_history = context_history.ContextHistory()
        live_history.remember(
            "first task",
            "first report",
            subagent_node_id=first["node_id"],
        )

        self.assertIn(
            "first task",
            str(live_history.messages(subagent_node_id=first["node_id"])),
        )
        self.assertEqual(
            live_history.messages(subagent_node_id=second["node_id"]), []
        )
        self.assertEqual(
            fresh_history.messages(subagent_node_id=first["node_id"]), []
        )

    def test_new_agent_starts_with_fresh_subagent_conversations(self):
        first = Agent(conversation=Conversation(system_prompt="test"))
        first.subagent_history.remember(
            "earlier task", "earlier report", builtin_key="media"
        )

        second = Agent(conversation=Conversation(system_prompt="test"))

        self.assertIn(
            "earlier task",
            str(first.subagent_history.messages(builtin_key="media")),
        )
        self.assertEqual(second.subagent_history.messages(builtin_key="media"), [])

    def test_media_and_system_receive_only_their_own_history(self):
        history = context_history.ContextHistory()
        observed: dict[str, list[list[dict]]] = {"media": [], "system": []}

        def media_run(messages, *_args, **_kwargs):
            observed["media"].append(messages)
            return "media report"

        def system_run(messages, *_args, **_kwargs):
            observed["system"].append(messages)
            return "system report"

        with patch.object(media.graph_runtime, "run_tool_agent", side_effect=media_run):
            media.run("inspect alpha", context_history_store=history)
            media.run("inspect beta", context_history_store=history)
        with (
            patch.object(system, "_context", return_value="CURRENT DEVICE"),
            patch.object(system.graph_runtime, "run_tool_agent", side_effect=system_run),
        ):
            system.run("check alpha", context_history_store=history)
            system.run("check beta", context_history_store=history)

        self.assertIn("inspect alpha", str(observed["media"][1]))
        self.assertNotIn("check alpha", str(observed["media"][1]))
        self.assertIn("check alpha", str(observed["system"][1]))
        self.assertNotIn("inspect alpha", str(observed["system"][1]))

    def test_knowledge_receives_its_previous_runs(self):
        history = context_history.ContextHistory()
        observed: list[list[dict]] = []

        async def fake_run(
            _task,
            _spec,
            _runtime,
            _allowed_tools,
            _confirmation_tools,
            prior_history,
        ):
            observed.append(prior_history)
            return "knowledge report"

        with (
            patch.object(
                db,
                "get_builtin_agent_server_spec",
                return_value={"server_id": 1},
            ),
            patch.object(db, "get_builtin_agent_runtime", return_value={}),
            patch.object(db, "get_builtin_confirmation_tools", return_value=[]),
            patch.object(knowledge, "_run_async", side_effect=fake_run),
        ):
            knowledge.run("remember alpha", context_history_store=history)
            knowledge.run("remember beta", context_history_store=history)

        self.assertEqual(observed[0], [])
        self.assertIn("remember alpha", str(observed[1]))

    def test_reset_clears_supervisor_and_specialist_history(self):
        conversation = Conversation(system_prompt="test")
        conversation.add_user("hello")
        agent = Agent(conversation=conversation)
        agent.subagent_history.remember(
            "dynamic task", "dynamic report", subagent_node_id=42
        )
        agent.subagent_history.remember(
            "media task", "media report", builtin_key="media"
        )

        agent.reset()

        self.assertEqual(len(conversation), 0)
        self.assertEqual(
            agent.subagent_history.messages(subagent_node_id=42), []
        )
        self.assertEqual(agent.subagent_history.messages(builtin_key="media"), [])

    def test_meta_specialists_are_not_history_owners(self):
        history = context_history.ContextHistory()
        history.remember("meta task", "meta report", builtin_key="facebook")
        self.assertEqual(history.messages(builtin_key="facebook"), [])


if __name__ == "__main__":
    unittest.main()
