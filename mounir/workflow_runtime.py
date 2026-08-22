"""Executable saved workflows built from LangGraph and typed delegation tools."""

from __future__ import annotations

import asyncio
import re
import threading
from typing import Annotated, Any, TypedDict

from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph

from . import action_decline, db, graph_runtime, llm, mcp_agents
from .specialists.mcp_agent import run as run_mcp_agent

MAX_WORKFLOW_DEPTH = 8
MAX_AGENTIC_ROUNDS = 10

ORCHESTRATOR_PROMPT = """\
You are the orchestrator of a saved workflow. Complete the user's request by
delegating work to the provided subagent and workflow tools.

WORKING RULES
- Choose only the delegates needed for the request.
- Give every delegate a self-contained task with all details it needs.
- Treat delegate reports as evidence; never claim an action succeeded unless a
  delegate reports that it succeeded.
- When the work is complete, return one concise final response to the caller.
"""


class DirectWorkflowState(TypedDict):
    request: str
    output: Any
    completed: list[dict]


def _slug(value: str) -> str:
    return re.sub(
        r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    ).strip("_")


def delegate_tool_name(workflow: dict) -> str:
    """Return a namespace-safe tool name for one saved workflow."""
    return f"run_workflow_{_slug(str(workflow.get('name') or workflow['id']))}"


def node_name(placement: dict) -> str:
    """Return a graph-node id unique to this workflow placement."""
    return f"workflow_{int(placement['id'])}"


def attached_workflows(
    owner_workflow_id: int | None, parent_node_id: int | None
) -> list[dict]:
    """Resolve workflow tools visible at exactly one graph placement."""
    return [
        placement
        for placement in db.list_workflow_nodes(owner_workflow_id)
        if placement.get("parent_node_id") == parent_node_id
    ]


def _description(placement: dict) -> str:
    description = " ".join(str(placement.get("description") or "").split())
    mode = str(placement.get("execution_mode") or "agentic")
    detail = f" Run the saved {mode} workflow and return its final result."
    return f"{description}{detail}".strip()


def routing_tool(placement: dict) -> StructuredTool:
    """Build the supervisor-facing routing schema for a workflow placement."""

    def route(
        task: Annotated[str, "Task with every detail the workflow needs."],
    ) -> str:
        return (
            f"Workflow routing for {placement['name']} must run inside the "
            f"agent graph: {task}"
        )

    return StructuredTool.from_function(
        func=route,
        name=delegate_tool_name(placement),
        description=_description(placement),
    )


def async_delegate_tool(
    placement: dict,
    protected_attempts: set[str],
    lineage: tuple[int, ...],
) -> StructuredTool:
    """Build an executable workflow tool for a subagent/orchestrator ToolNode."""

    async def delegate(
        task: Annotated[str, "Task with every detail the workflow needs."],
    ):
        report = await asyncio.to_thread(
            run,
            int(placement["child_workflow_id"]),
            task,
            protected_attempts=protected_attempts,
            lineage=lineage,
        )
        if isinstance(report, action_decline.Signal):
            outcome = action_decline.parse(report)
            return action_decline.MESSAGE, action_decline.artifact(outcome or {})
        return report, None

    return StructuredTool.from_function(
        coroutine=delegate,
        name=delegate_tool_name(placement),
        description=_description(placement),
        response_format="content_and_artifact",
    )


def _delegate_tool(
    placement: dict,
    protected_attempts: set[str],
    lineage: tuple[int, ...],
    decline_state: dict[str, action_decline.Signal | None],
    execution_lock: threading.Lock,
) -> StructuredTool:
    """Build a synchronous executable workflow tool for an orchestrator."""

    def delegate(
        task: Annotated[str, "Task with every detail the workflow needs."],
    ):
        with execution_lock:
            if decline_state["signal"] is not None:
                return "Skipped — an earlier action was declined.", None
            report = run(
                int(placement["child_workflow_id"]),
                task,
                protected_attempts=protected_attempts,
                lineage=lineage,
            )
            if isinstance(report, action_decline.Signal):
                decline_state["signal"] = report
                return action_decline.MESSAGE, None
            return report, None

    return StructuredTool.from_function(
        func=delegate,
        name=delegate_tool_name(placement),
        description=_description(placement),
        response_format="content_and_artifact",
    )


def _subagent_delegate_tool(
    spec: dict,
    all_specs: list[dict],
    protected_attempts: set[str],
    decline_state: dict[str, action_decline.Signal | None],
    execution_lock: threading.Lock,
) -> StructuredTool:
    def delegate(
        task: Annotated[str, "Task with every detail the subagent needs."],
    ):
        with execution_lock:
            if decline_state["signal"] is not None:
                return "Skipped — an earlier action was declined.", None
            report = run_mcp_agent(
                task,
                spec,
                protected_attempts,
                all_specs=all_specs,
            )
            if isinstance(report, action_decline.Signal):
                decline_state["signal"] = report
                return action_decline.MESSAGE, None
            return report, None

    return StructuredTool.from_function(
        func=delegate,
        name=mcp_agents.delegate_tool_name(spec["name"]),
        description=(
            f"Delegate to the {spec['name']} subagent. {spec['description']} "
            "It completes the work with its own tools and returns a short report."
        ),
        response_format="content_and_artifact",
    )


def _step_task(request: str, completed: list[dict]) -> str:
    if not completed:
        return request
    history = "\n\n".join(
        f"{index}. {item['name']}\n{str(item['result'])}"
        for index, item in enumerate(completed, start=1)
    )
    return (
        f"Original workflow request:\n{request}\n\n"
        f"Completed workflow steps:\n{history}"
    )


def _run_direct(
    workflow: dict,
    task: str,
    protected_attempts: set[str],
    lineage: tuple[int, ...],
) -> str:
    workflow_id = int(workflow["id"])
    specs = db.build_specs(workflow_id)
    specs_by_node = {int(spec["node_id"]): spec for spec in specs}
    steps = [
        {"kind": "subagent", **placement}
        for placement in db.list_subagent_nodes(workflow_id)
        if placement.get("parent_node_id") is None
    ]
    steps.extend(
        {"kind": "workflow", **placement}
        for placement in attached_workflows(workflow_id, None)
    )
    steps.sort(
        key=lambda item: (
            int(item.get("position") or 0),
            str(item.get("created_at") or ""),
            int(item["id"]),
        )
    )
    if not steps:
        return f"The {workflow['name']} workflow has no steps configured."

    graph = StateGraph(DirectWorkflowState)
    previous_node = START

    for index, step in enumerate(steps):
        graph_node = f"step_{index}_{step['kind']}_{int(step['id'])}"

        if step["kind"] == "subagent":
            spec = specs_by_node.get(int(step["node_id"]))

            def execute_subagent(
                state: DirectWorkflowState,
                selected_spec=spec,
                selected_step=step,
            ) -> dict:
                if isinstance(state.get("output"), action_decline.Signal):
                    return {}
                if selected_spec is None:
                    report: Any = (
                        f"The {selected_step['name']} step is inactive or unavailable."
                    )
                else:
                    report = run_mcp_agent(
                        _step_task(state["request"], state.get("completed", [])),
                        selected_spec,
                        protected_attempts,
                        all_specs=specs,
                    )
                return {
                    "output": report,
                    "completed": [
                        *state.get("completed", []),
                        {"name": selected_step["name"], "result": report},
                    ],
                }

            graph.add_node(graph_node, execute_subagent)
        else:

            def execute_workflow(
                state: DirectWorkflowState,
                selected_step=step,
            ) -> dict:
                if isinstance(state.get("output"), action_decline.Signal):
                    return {}
                report = run(
                    int(selected_step["child_workflow_id"]),
                    _step_task(state["request"], state.get("completed", [])),
                    protected_attempts=protected_attempts,
                    lineage=lineage,
                )
                return {
                    "output": report,
                    "completed": [
                        *state.get("completed", []),
                        {"name": selected_step["name"], "result": report},
                    ],
                }

            graph.add_node(graph_node, execute_workflow)

        graph.add_edge(previous_node, graph_node)
        previous_node = graph_node

    graph.add_edge(previous_node, END)
    result = graph.compile().invoke(
        {"request": task, "output": None, "completed": []}
    )
    report = result.get("output")
    if isinstance(report, action_decline.Signal):
        completed = [
            {
                "agent": workflow["name"],
                "name": item["name"],
                "result": item["result"],
            }
            for item in result.get("completed", [])[:-1]
        ]
        return action_decline.add_agent_context(
            report, agent=workflow["name"], completed_actions=completed
        )
    return str(report or "").strip()


def _run_agentic(
    workflow: dict,
    task: str,
    protected_attempts: set[str],
    lineage: tuple[int, ...],
) -> str:
    model_id = workflow.get("model_id")
    if model_id is None:
        return f"The {workflow['name']} workflow has no orchestrator model configured."
    runtime = db.get_model_runtime(int(model_id))
    if runtime is None:
        return f"The {workflow['name']} workflow's orchestrator model is unavailable."

    workflow_id = int(workflow["id"])
    specs = db.build_specs(workflow_id)
    root_specs = [spec for spec in specs if spec.get("parent_node_id") is None]
    decline_state: dict[str, action_decline.Signal | None] = {"signal": None}
    execution_lock = threading.Lock()
    tools = [
        _subagent_delegate_tool(
            spec,
            specs,
            protected_attempts,
            decline_state,
            execution_lock,
        )
        for spec in root_specs
    ]
    tools.extend(
        _delegate_tool(
            placement,
            protected_attempts,
            lineage,
            decline_state,
            execution_lock,
        )
        for placement in attached_workflows(workflow_id, None)
    )
    custom_prompt = str(workflow.get("system_prompt") or "").strip()
    system_prompt = ORCHESTRATOR_PROMPT
    if custom_prompt:
        system_prompt += f"\n\nWORKFLOW INSTRUCTIONS\n{custom_prompt}"

    def call_model(history: list[dict], schemas: list[dict] | None):
        if decline_state["signal"] is not None:
            return {"content": str(decline_state["signal"]), "tool_calls": []}
        return llm.openai_chat(
            history,
            tools=schemas,
            model=runtime["model"],
            provider=runtime["provider"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
        )

    report = graph_runtime.run_tool_agent(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ],
        tools,
        call_model,
        max_rounds=MAX_AGENTIC_ROUNDS,
        empty_response=(
            f"The {workflow['name']} orchestrator returned an empty response twice."
        ),
        exhausted_response=(
            f"The {workflow['name']} orchestrator reached its maximum tool rounds."
        ),
        error_formatter=lambda _executed, error: (
            f"The {workflow['name']} orchestrator failed: {error}"
        ),
    )
    if action_decline.parse(report) is not None:
        return action_decline.Signal(report)
    return report


def run(
    workflow_id: int,
    task: str,
    protected_attempts: set[str] | None = None,
    *,
    lineage: tuple[int, ...] = (),
) -> str:
    """Execute one workflow and return its single report to the caller."""
    workflow = db.get_workflow(int(workflow_id))
    if workflow is None:
        return "The requested workflow no longer exists."
    current_id = int(workflow["id"])
    if current_id in lineage:
        return "Workflow execution was blocked because it would create a cycle."
    if len(lineage) >= MAX_WORKFLOW_DEPTH:
        return "Workflow execution was blocked at the maximum nesting depth."

    attempts = protected_attempts if protected_attempts is not None else set()
    current_lineage = (*lineage, current_id)
    if workflow["execution_mode"] == "direct":
        return _run_direct(workflow, task, attempts, current_lineage)

    report = _run_agentic(workflow, task, attempts, current_lineage)
    if isinstance(report, action_decline.Signal):
        return action_decline.add_agent_context(
            report, agent=workflow["name"]
        )
    return str(report or "").strip()
