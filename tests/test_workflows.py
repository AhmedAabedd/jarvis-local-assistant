from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from mounir import db, langgraph_agent, workflow_runtime


class WorkflowDatabaseTests(unittest.TestCase):
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

    def _agent(self):
        model = db.add_model(
            "Workflow model", "test/model", "OpenAI compatible",
            "http://localhost:11434/v1", "",
        )
        server = db.add_server("Workflow server", "test-workflow-server")
        return db.add_subagent(
            "Workflow helper", "Used only in a saved workflow.", "",
            model["id"], server["id"], connect_to_workflow=False,
        )

    def test_workflows_have_no_active_or_default_architecture(self):
        workflow = db.create_workflow(name="Research", execution_mode="agentic")
        self.assertEqual(workflow["execution_mode"], "agentic")
        self.assertNotIn("enabled", workflow)
        self.assertNotIn("is_default", workflow)
        self.assertNotIn("is_system", workflow)

    def test_workflow_subagents_are_isolated_from_global_runtime(self):
        workflow = db.create_workflow(name="Private design", execution_mode="agentic")
        agent = self._agent()
        node = db.add_subagent_node(agent["id"], workflow_id=workflow["id"])

        self.assertEqual(node["workflow_id"], workflow["id"])
        self.assertEqual(len(db.list_subagent_nodes(workflow["id"])), 1)
        self.assertEqual(db.list_subagent_nodes(), [])
        self.assertEqual(db.build_specs(), [])

    def test_reused_workflows_reject_cycles_and_restrict_deletion(self):
        first = db.create_workflow(name="First")
        second = db.create_workflow(name="Second")
        placement = db.add_workflow_node(
            second["id"], owner_workflow_id=first["id"]
        )
        with self.assertRaisesRegex(ValueError, "indirectly"):
            db.add_workflow_node(first["id"], owner_workflow_id=second["id"])

        self.assertEqual(db.delete_workflow(second["id"]).status, "restricted")
        self.assertTrue(db.remove_workflow_node(placement["id"]))
        self.assertTrue(db.delete_workflow(second["id"]).deleted)

    def test_direct_workflow_steps_remain_flat(self):
        workflow = db.create_workflow(name="Sequence", execution_mode="direct")
        agent = self._agent()
        root = db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        repeated = db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        self.assertGreater(repeated["position"], root["position"])
        # A restart must not recreate the obsolete global uniqueness index.
        db.init()
        with self.assertRaisesRegex(ValueError, "cannot have parent"):
            db.add_subagent_node(
                agent["id"], parent_node_id=root["id"], workflow_id=workflow["id"]
            )

    def test_direct_workflows_do_not_store_orchestrator_configuration(self):
        agent = self._agent()
        direct = db.create_workflow(
            name="No orchestrator",
            execution_mode="direct",
            model_id=agent["model_id"],
            system_prompt="This must not be retained.",
        )
        self.assertIsNone(direct["model_id"])
        self.assertEqual(direct["system_prompt"], "")

        agentic = db.create_workflow(
            name="Configured orchestrator",
            execution_mode="agentic",
            model_id=agent["model_id"],
            system_prompt="Delegate carefully.",
        )
        converted = db.update_workflow(agentic["id"], execution_mode="direct")
        self.assertIsNone(converted["model_id"])
        self.assertEqual(converted["system_prompt"], "")

    def test_direct_nodes_can_be_inserted_at_any_edge_position(self):
        owner = db.create_workflow(name="Insertable sequence", execution_mode="direct")
        nested = db.create_workflow(name="Nested sequence", execution_mode="direct")
        agent = self._agent()

        first = db.add_subagent_node(agent["id"], workflow_id=owner["id"])
        workflow_node = db.add_workflow_node(
            nested["id"], owner_workflow_id=owner["id"]
        )
        inserted = db.add_subagent_node(
            agent["id"], workflow_id=owner["id"], position=1
        )

        self.assertEqual(first["position"], 0)
        self.assertEqual(inserted["position"], 1)
        refreshed_workflow_node = db.list_workflow_nodes(owner["id"])[0]
        self.assertEqual(refreshed_workflow_node["id"], workflow_node["id"])
        self.assertEqual(refreshed_workflow_node["position"], 2)

    def test_saved_workflow_specs_are_runtime_scoped(self):
        workflow = db.create_workflow(name="Scoped runtime", execution_mode="agentic")
        agent = self._agent()
        placement = db.add_subagent_node(agent["id"], workflow_id=workflow["id"])

        self.assertEqual(db.build_specs(), [])
        specs = db.build_specs(workflow["id"])
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["node_id"], placement["id"])
        self.assertEqual(specs[0]["workflow_id"], workflow["id"])
        self.assertFalse(specs[0]["connected_to_supervisor"])

    def test_direct_workflow_runs_steps_in_saved_order_with_langgraph(self):
        workflow = db.create_workflow(name="Ordered runtime", execution_mode="direct")
        agent = self._agent()
        db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        calls = []

        def run_step(task, spec, *_args, **_kwargs):
            calls.append((spec["node_id"], task))
            if len(calls) == 1:
                return "first complete report\nwith every detail"
            return f"result-{len(calls)}"

        with patch.object(workflow_runtime, "run_mcp_agent", run_step):
            result = workflow_runtime.run(workflow["id"], "prepare release")

        self.assertEqual(result, "result-3")
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1], "prepare release")
        self.assertIn("Original workflow request:\nprepare release", calls[1][1])
        self.assertIn(
            "1. Workflow helper\nfirst complete report\nwith every detail",
            calls[1][1],
        )
        self.assertIn(
            "1. Workflow helper\nfirst complete report\nwith every detail",
            calls[2][1],
        )
        self.assertIn("2. Workflow helper\nresult-2", calls[2][1])

    def test_agentic_workflow_orchestrator_can_delegate_to_root_node(self):
        agent = self._agent()
        workflow = db.create_workflow(
            name="Agentic runtime",
            execution_mode="agentic",
            model_id=agent["model_id"],
            system_prompt="Coordinate the configured specialists.",
        )
        db.add_subagent_node(agent["id"], workflow_id=workflow["id"])
        observed_schemas = []
        responses = [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_helper",
                        "function": {
                            "name": "delegate_to_workflow_helper",
                            "arguments": {"task": "collect evidence"},
                        },
                    }
                ],
            },
            {"content": "finished", "tool_calls": []},
        ]

        def call_model(_messages, tools=None, **_kwargs):
            observed_schemas.extend(tools or [])
            return responses.pop(0)

        with (
            patch.object(workflow_runtime.llm, "openai_chat", call_model),
            patch.object(
                workflow_runtime,
                "run_mcp_agent",
                return_value="evidence collected",
            ) as delegated,
        ):
            result = workflow_runtime.run(workflow["id"], "research this")

        self.assertEqual(result, "finished")
        delegated.assert_called_once()
        self.assertIn(
            "delegate_to_workflow_helper",
            {
                schema["function"]["name"]
                for schema in observed_schemas
            },
        )

    def test_mounir_sees_only_root_workflow_placements(self):
        root = db.create_workflow(name="Mounir workflow", execution_mode="direct")
        nested = db.create_workflow(name="Nested workflow", execution_mode="direct")
        root_node = db.add_workflow_node(root["id"])
        agent = self._agent()
        parent = db.add_subagent_node(agent["id"])
        db.add_workflow_node(nested["id"], parent_node_id=parent["id"])

        graph_nodes = set(langgraph_agent.build_graph().get_graph().nodes)
        self.assertIn(workflow_runtime.node_name(root_node), graph_nodes)
        self.assertNotIn(
            workflow_runtime.node_name(
                workflow_runtime.attached_workflows(None, parent["id"])[0]
            ),
            graph_nodes,
        )

        advertised = []

        def chat_stream(_messages, tools=None, **_kwargs):
            advertised.extend(
                schema["function"]["name"] for schema in (tools or [])
            )
            yield "ok"

        with patch.object(langgraph_agent.llm, "chat_stream", chat_stream):
            list(langgraph_agent.Agent().respond("What can you run?"))

        self.assertIn("run_workflow_mounir_workflow", advertised)
        self.assertNotIn("run_workflow_nested_workflow", advertised)

    def test_mounir_can_execute_a_connected_workflow_and_receive_its_result(self):
        workflow = db.create_workflow(name="Callable workflow", execution_mode="direct")
        db.add_workflow_node(workflow["id"])
        model_calls = []

        def chat_stream(messages, tool_calls_out=None, **_kwargs):
            model_calls.append(messages)
            if not any(message.get("role") == "tool" for message in messages):
                tool_calls_out.append(
                    {
                        "id": "workflow_call",
                        "function": {
                            "name": "run_workflow_callable_workflow",
                            "arguments": {"task": "prepare the result"},
                        },
                    }
                )
                return
            yield "Workflow result received."

        with (
            patch.object(langgraph_agent.llm, "chat_stream", chat_stream),
            patch.object(
                langgraph_agent.workflow_runtime,
                "run",
                return_value="prepared result",
            ) as execute,
        ):
            reply = "".join(langgraph_agent.Agent().respond("Run it"))

        self.assertEqual(reply, "Workflow result received.")
        execute.assert_called_once_with(
            workflow["id"],
            "prepare the result",
            ANY,
            context_history_store=ANY,
        )
        self.assertEqual(len(model_calls), 2)

    def test_subagent_workflow_visibility_is_placement_scoped(self):
        first = self._agent()
        second = db.add_subagent(
            "Second helper", "Another saved helper.", "",
            first["model_id"], first["mcp_server_id"],
            connect_to_workflow=False,
        )
        first_node = db.add_subagent_node(first["id"])
        second_node = db.add_subagent_node(second["id"])
        workflow = db.create_workflow(name="Attached tool", execution_mode="direct")
        placement = db.add_workflow_node(
            workflow["id"], parent_node_id=first_node["id"]
        )

        self.assertEqual(
            workflow_runtime.attached_workflows(None, first_node["id"]),
            [placement],
        )
        self.assertEqual(
            workflow_runtime.attached_workflows(None, second_node["id"]),
            [],
        )


if __name__ == "__main__":
    unittest.main()
