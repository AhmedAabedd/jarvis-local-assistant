"""LangGraph orchestration for Mounir.

The graph owns the complete supervisor workflow: canonical message state,
conditional routing, typed tool execution, specialist hand-offs, safety caps,
and token streaming.  Provider-specific HTTP/SDK calls remain in ``llm.py``;
everything around them uses LangGraph and LangChain primitives.
"""

from __future__ import annotations

import operator
import threading
from dataclasses import dataclass
from typing import Annotated, Callable, Iterator

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
    convert_to_messages,
)
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from . import (
    action_decline,
    agent_skills,
    builtin_agents,
    config as cfg,
    context_history,
    db,
    graph_runtime,
    llm,
    mcp_agents,
    tool_outcome,
    tools,
    trace,
    workflow_runtime,
)
from .memory import Conversation
from .specialists.computer import run as run_computer
from .specialists.knowledge import (
    automatic_context as automatic_knowledge_context,
    run as run_knowledge,
)
from .specialists.media import run as run_media
from .specialists.mcp_agent import run as run_mcp_agent
from .specialists.system import run as run_system

MAX_TOOL_ROUNDS = 10
MAX_DELEGATIONS = 3
VOICE_RESPONSE_INSTRUCTION = (
    "MANDATORY VOICE MODE: Reply as natural spoken language only. "
    "Never use Markdown, bullets, tables, code formatting, emojis, or decorative symbols."
)
_ASSISTANT_COMPLETION_EVENT = "mounir.assistant_completion"

_DELEGATES = {
    "delegate_to_computer": "computer",
    "delegate_to_media": "media",
    "delegate_to_knowledge": "knowledge",
    "delegate_to_system": "system",
    "delegate_to_facebook": "facebook",
    "delegate_to_messenger": "messenger",
    "delegate_to_instagram": "instagram",
    "delegate_to_threads": "threads",
    "delegate_to_whatsapp": "whatsapp",
}


class TurnState(MessagesState):
    """Canonical LangGraph message state plus bounded-loop counters."""

    tool_rounds: Annotated[int, operator.add]
    delegations: Annotated[int, operator.add]
    pending_tool_calls: list[dict]


@dataclass(frozen=True)
class AssistantCompletion:
    """One complete user-visible assistant response before the next graph step."""

    text: str
    has_tool_calls: bool
    is_final: bool


def _tool_calls(message: BaseMessage | dict) -> list[dict]:
    if isinstance(message, AIMessage):
        return list(message.tool_calls)
    if isinstance(message, dict):
        normalized = graph_runtime.ai_message(message)
        return list(normalized.tool_calls)
    return []


def _extract_delegate(
    messages: list[BaseMessage | dict], tool_name: str
) -> tuple[str, str]:
    """Return the task and call ID from the latest matching delegation."""

    for message in reversed(messages):
        for call in _tool_calls(message):
            if call.get("name") == tool_name:
                return str((call.get("args") or {}).get("task", "")), str(
                    call.get("id") or "call_0"
                )
    return "", "call_0"


def _stream_text(content: str) -> None:
    if content:
        get_stream_writer()(content)


def _stream_completion(message: AIMessage) -> None:
    """Publish a boundary event without adding it to the visible token stream."""
    text = str(message.content or "").strip()
    if not text:
        return
    has_tool_calls = bool(message.tool_calls)
    get_stream_writer()(
        {
            "type": _ASSISTANT_COMPLETION_EVENT,
            "text": text,
            "has_tool_calls": has_tool_calls,
            "is_final": not has_tool_calls,
        }
    )


def _report_completion(
    handler: Callable[[AssistantCompletion], None] | None,
    completion: AssistantCompletion,
) -> None:
    """Keep an optional output consumer from changing graph execution."""
    if handler is None:
        return
    try:
        handler(completion)
    except Exception as exc:
        trace.kv("assistant completion", f"consumer failed: {exc}")


def _supervisor(
    state: TurnState,
    model: str,
    use_tools: bool,
    delegates: dict[str, str],
    available_tools: list[BaseTool],
) -> Command:
    """Run one supervisor model step and route its canonical tool calls."""

    runtime = db.get_supervisor_runtime(model)
    advertised = list(available_tools) if use_tools else []
    if state.get("delegations", 0) >= MAX_DELEGATIONS:
        advertised = [item for item in advertised if item.name not in delegates]

    raw_calls: list = []
    chunks: list[str] = []
    for chunk in llm.chat_stream(
        graph_runtime.message_dicts(state["messages"]),
        tools=graph_runtime.tool_schemas(advertised) or None,
        tool_calls_out=raw_calls,
        model=runtime["model"],
        provider=runtime["provider"],
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
    ):
        chunks.append(chunk)
        _stream_text(chunk)

    response = graph_runtime.ai_message(
        {"content": "".join(chunks), "tool_calls": raw_calls},
        call_prefix=f"call_{state.get('tool_rounds', 0)}",
    )
    _stream_completion(response)
    if not response.tool_calls:
        return Command(
            goto=END,
            update={"messages": [response], "pending_tool_calls": []},
        )

    first, *pending = response.tool_calls
    target = delegates.get(str(first.get("name") or ""), "tools")
    first_message = AIMessage(content=response.content, tool_calls=[first])
    if target != "tools":
        trace.gap()
        trace.event(f"→ delegating to {target}")

    return Command(
        goto=target,
        update={
            "messages": [first_message],
            "pending_tool_calls": pending,
            "tool_rounds": 1,
            "delegations": 1 if target != "tools" else 0,
        },
    )


def _advance_tool_batch(state: TurnState, delegates: dict[str, str]) -> Command:
    """Dispatch the next call from one model-declared batch in original order."""

    pending = list(state.get("pending_tool_calls") or [])
    if not pending:
        return Command(goto="supervisor", update={"pending_tool_calls": []})
    current, *remaining = pending
    target = delegates.get(str(current.get("name") or ""), "tools")
    if target != "tools":
        trace.gap()
        trace.event(f"→ delegating to {target}")
    return Command(
        goto=target,
        update={
            "messages": [AIMessage(content="", tool_calls=[current])],
            "pending_tool_calls": remaining,
            "delegations": 1 if target != "tools" else 0,
        },
    )


def _after_tools(state: TurnState) -> str:
    """Trace the latest batch and decide whether the loop continues."""

    latest: list[ToolMessage] = []
    caller: AIMessage | None = None
    for message in reversed(state["messages"]):
        if isinstance(message, AIMessage):
            caller = message
            break
        if isinstance(message, ToolMessage):
            latest.append(message)
    graph_runtime.trace_tool_messages(
        [*([caller] if caller is not None else []), *reversed(latest)]
    )
    if any(str(message.content) == tools.USER_DECLINED for message in latest):
        return "declined"
    if state.get("pending_tool_calls"):
        return "advance_tools"
    if state.get("tool_rounds", 0) >= MAX_TOOL_ROUNDS:
        return "force_final"
    return "supervisor"


def _declined(state: TurnState) -> dict:
    outcome = next(
        (
            action_decline.from_artifact(getattr(message, "artifact", None))
            for message in reversed(state["messages"])
            if isinstance(message, ToolMessage)
            and action_decline.from_artifact(getattr(message, "artifact", None))
        ),
        None,
    )
    notice = action_decline.user_notice(outcome)
    _stream_text(notice)
    response = AIMessage(content=notice)
    _stream_completion(response)
    return {"messages": [response]}


def _force_final(state: TurnState, model: str) -> dict:
    runtime = db.get_supervisor_runtime(model)
    parts: list[str] = []
    for chunk in llm.chat_stream(
        graph_runtime.message_dicts(state["messages"]),
        tools=None,
        model=runtime["model"],
        provider=runtime["provider"],
        base_url=runtime["base_url"],
        api_key=runtime["api_key"],
    ):
        parts.append(chunk)
        _stream_text(chunk)
    response = AIMessage(content="".join(parts).strip())
    _stream_completion(response)
    return {"messages": [response]}


def _specialist_result(
    state: TurnState,
    tool_name: str,
    node_name: str,
    runner,
    unavailable: str | None = None,
) -> Command:
    task, call_id = _extract_delegate(state["messages"], tool_name)
    trace.node(node_name)
    trace.block("received  ← supervisor", task)
    if unavailable:
        result = tool_outcome.ToolOutcome.error(unavailable)
    elif not task:
        result = tool_outcome.ToolOutcome.error(
            f"No task was provided to the {node_name} agent."
        )
    else:
        try:
            raw_report = runner(task)
        except Exception as exc:
            result = tool_outcome.ToolOutcome.error(
                f"The {node_name} agent failed: {exc}"
            )
        else:
            if isinstance(raw_report, action_decline.Signal):
                decline = action_decline.parse(raw_report) or {}
                result = tool_outcome.ToolOutcome.declined(
                    action_decline.MESSAGE,
                    action_decline.artifact(decline),
                )
            else:
                result = tool_outcome.ToolOutcome.success(str(raw_report).strip())
    decline = (
        action_decline.from_artifact(result.artifact)
        if isinstance(result.artifact, dict)
        else None
    )
    visible_report = str(result.content)
    trace.block("returned  → supervisor", visible_report)
    trace.gap()
    return Command(
        goto=(
            "declined"
            if decline is not None
            else "advance_tools"
            if state.get("pending_tool_calls")
            else "supervisor"
        ),
        update={
            "messages": [
                result.as_tool_message(
                    name=tool_name,
                    tool_call_id=call_id,
                )
            ]
        },
    )


def _media(
    state: TurnState,
    history: context_history.ContextHistory | None = None,
) -> Command:
    enabled = db.is_builtin_agent_enabled("media")
    return _specialist_result(
        state,
        "delegate_to_media",
        "media",
        lambda task: run_media(task, context_history_store=history),
        None if enabled else "Files and Media is inactive and cannot be used.",
    )


def _knowledge(
    state: TurnState,
    history: context_history.ContextHistory | None = None,
) -> Command:
    enabled = db.is_builtin_agent_enabled("knowledge")
    return _specialist_result(
        state,
        "delegate_to_knowledge",
        "knowledge",
        lambda task: run_knowledge(task, context_history_store=history),
        None if enabled else "The Knowledge agent is inactive and cannot be used.",
    )


def _computer(
    state: TurnState,
    history: context_history.ContextHistory | None = None,
) -> Command:
    enabled = db.is_builtin_agent_enabled("computer")
    return _specialist_result(
        state,
        "delegate_to_computer",
        "computer",
        lambda task: run_computer(task, context_history_store=history),
        None if enabled else "The Computer agent is inactive and cannot be used.",
    )


def _system(
    state: TurnState,
    history: context_history.ContextHistory | None = None,
) -> Command:
    enabled = db.is_builtin_agent_enabled("system")
    return _specialist_result(
        state,
        "delegate_to_system",
        "system",
        lambda task: run_system(task, context_history_store=history),
        None if enabled else "The System agent is inactive and cannot be used.",
    )


def _make_mcp_node(
    spec: dict,
    all_specs: list[dict],
    protected_attempts: set[str],
    history: context_history.ContextHistory | None,
):
    tool_name = mcp_agents.delegate_tool_name(spec["name"])

    def node(state: TurnState) -> Command:
        enabled = db.is_subagent_enabled(spec["id"])
        unavailable = (
            None
            if enabled
            else f"The {spec['name']} agent is inactive and cannot be used."
        )
        return _specialist_result(
            state,
            tool_name,
            spec["name"],
            lambda task: run_mcp_agent(
                task,
                spec,
                protected_attempts,
                all_specs=all_specs,
                context_history_store=history,
            ),
            unavailable,
        )

    return node


def _make_workflow_node(
    placement: dict,
    protected_attempts: set[str],
    history: context_history.ContextHistory | None,
):
    tool_name = workflow_runtime.delegate_tool_name(placement)

    def node(state: TurnState) -> Command:
        return _specialist_result(
            state,
            tool_name,
            placement["name"],
            lambda task: workflow_runtime.run(
                int(placement["child_workflow_id"]),
                task,
                protected_attempts,
                context_history_store=history,
            ),
        )

    return node


def _make_heartbeat_builtin_node(
    spec: dict,
    history: context_history.ContextHistory | None,
):
    key = spec["builtin_key"]
    tool_name = f"delegate_to_{key}"
    task_prompt = str(spec.get("task_prompt") or "").strip()

    def node(state: TurnState) -> Command:
        return _specialist_result(
            state,
            tool_name,
            spec["name"],
            lambda task: builtin_agents.run(
                key,
                "\n\n".join(part for part in (task, task_prompt) if part),
                spec["allowed_tools"],
                context_history_store=history,
            ),
        )

    return node


def _make_direct_builtin_node(key: str):
    tool_name = f"delegate_to_{key}"
    definition = builtin_agents.definition(key) or {"name": key.title()}

    def node(state: TurnState) -> Command:
        enabled = db.is_builtin_agent_enabled(key)
        return _specialist_result(
            state,
            tool_name,
            definition["name"],
            lambda task: builtin_agents.run_direct(key, task),
            None if enabled else f"The {definition['name']} agent is inactive and cannot be used.",
        )

    return node


def _compile_graph(
    model: str,
    use_tools: bool,
    scoped_targets: list[dict] | None = None,
    *,
    loaded_dynamic: list[dict] | None = None,
    skill_tool: BaseTool | None = None,
    context_history_store: context_history.ContextHistory | None = None,
):
    scoped = scoped_targets is not None
    dynamic = (
        [dict(spec) for spec in loaded_dynamic]
        if loaded_dynamic is not None
        else (
            [
                dict(spec)
                for spec in scoped_targets or []
                if spec.get("kind") == "mcp"
            ]
            if scoped
            else mcp_agents.load()
        )
    )
    scoped_builtins = {
        spec["builtin_key"]: dict(spec)
        for spec in scoped_targets or []
        if spec.get("kind") == "builtin"
    }
    enabled_builtins = (
        set(scoped_builtins) if scoped else db.enabled_builtin_agent_keys()
    )
    delegates = {
        name: node
        for name, node in _DELEGATES.items()
        if node in enabled_builtins
    }
    root_specs = (
        dynamic
        if scoped
        else [
            spec
            for spec in dynamic
            if spec.get("connected_to_supervisor")
            or (
                "connected_to_supervisor" not in spec
                and spec.get("parent_agent_id") is None
            )
        ]
    )
    workflow_placements = (
        [] if scoped else workflow_runtime.attached_workflows(None, None)
    )
    dynamic_tools = []
    for spec in root_specs:
        name = mcp_agents.delegate_tool_name(spec["name"])
        delegates[name] = mcp_agents.node_name(spec["name"])
        dynamic_tools.append(mcp_agents.delegate_tool(spec))
    workflow_tools = []
    for placement in workflow_placements:
        name = workflow_runtime.delegate_tool_name(placement)
        delegates[name] = workflow_runtime.node_name(placement)
        workflow_tools.append(workflow_runtime.routing_tool(placement))

    builtin_tools = [
        item
        for item in tools.DELEGATE_TOOLS
        if item.name in delegates
    ]
    general_tools = [] if scoped else list(tools.GENERAL_TOOLS)
    if skill_tool is not None:
        general_tools.append(skill_tool)
    advertised_tools = [
        *general_tools,
        *builtin_tools,
        *dynamic_tools,
        *workflow_tools,
    ]

    declined_batch = threading.Event()
    general_tool_lock = threading.Lock()

    def execute_general_tool(request, execute):
        with general_tool_lock:
            if declined_batch.is_set():
                return tool_outcome.ToolOutcome.skipped(
                    "Skipped — the user declined a command in this batch."
                ).as_tool_message(
                    name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"],
                )
            result = execute(request)
            if (
                isinstance(result, ToolMessage)
                and str(result.content) == tools.USER_DECLINED
            ):
                declined_batch.set()
                return tool_outcome.normalize(
                    result, outcome_status="declined"
                )
            return (
                tool_outcome.normalize(result)
                if isinstance(result, ToolMessage)
                else result
            )

    graph = StateGraph(TurnState)
    graph.add_node(
        "supervisor",
        lambda state: _supervisor(
            state, model, use_tools, delegates, advertised_tools
        ),
    )
    graph.add_node(
        "tools",
        ToolNode(
            general_tools,
            handle_tool_errors=True,
            wrap_tool_call=execute_general_tool,
        ),
    )
    graph.add_node(
        "advance_tools",
        lambda state: _advance_tool_batch(state, delegates),
    )
    graph.add_node("declined", _declined)
    graph.add_node("force_final", lambda state: _force_final(state, model))
    normal_nodes = {
        "computer": lambda state: _computer(state, context_history_store),
        "media": lambda state: _media(state, context_history_store),
        "knowledge": lambda state: _knowledge(state, context_history_store),
        "system": lambda state: _system(state, context_history_store),
    }
    for key in enabled_builtins:
        graph.add_node(
            key,
            _make_heartbeat_builtin_node(scoped_builtins[key], context_history_store)
            if scoped
            else normal_nodes.get(key, _make_direct_builtin_node(key)),
        )

    protected_attempts: set[str] = set()
    for spec in root_specs:
        graph.add_node(
            mcp_agents.node_name(spec["name"]),
            _make_mcp_node(spec, dynamic, protected_attempts, context_history_store),
        )
    for placement in workflow_placements:
        graph.add_node(
            workflow_runtime.node_name(placement),
            _make_workflow_node(
                placement, protected_attempts, context_history_store
            ),
        )

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "tools",
        _after_tools,
        {
            "supervisor": "supervisor",
            "advance_tools": "advance_tools",
            "declined": "declined",
            "force_final": "force_final",
        },
    )
    graph.add_edge("declined", END)
    graph.add_edge("force_final", END)
    return graph.compile()


def _stored_message(message: BaseMessage) -> dict:
    rendered = graph_runtime.message_dicts([message])[0]
    if isinstance(message, ToolMessage):
        rendered["tool_name"] = message.name or "tool"
    return rendered


class Agent:
    def __init__(
        self,
        conversation: Conversation | None = None,
        model: str = cfg.MODEL,
        use_tools: bool = True,
        scoped_targets: list[dict] | None = None,
        automatic_knowledge: bool | None = None,
        subagent_history: context_history.ContextHistory | None = None,
    ) -> None:
        db.init()
        if conversation is None:
            conversation = Conversation(system_prompt=cfg.build_system_prompt(db.get_profile()))
            self._profile_managed_prompt = True
        else:
            self._profile_managed_prompt = False
        self.conversation = conversation
        self.model = model
        self.use_tools = use_tools
        self.scoped_targets = scoped_targets
        self.subagent_history = subagent_history or context_history.ContextHistory()
        self.automatic_knowledge = (
            self._profile_managed_prompt
            if automatic_knowledge is None
            else bool(automatic_knowledge)
        )

    def respond(
        self,
        user_input: str,
        *,
        voice: bool = False,
        attachments: list[dict] | None = None,
        system_context: str = "",
    ) -> Iterator[str]:
        """Run and stream one complete LangGraph turn."""

        return self._respond(
            user_input,
            voice=voice,
            attachments=attachments,
            system_context=system_context,
        )

    def respond_with_completions(
        self,
        user_input: str,
        *,
        on_completion: Callable[[AssistantCompletion], None],
        voice: bool = False,
        attachments: list[dict] | None = None,
        system_context: str = "",
    ) -> Iterator[str]:
        """Stream a turn and report each real model-completion boundary."""

        return self._respond(
            user_input,
            voice=voice,
            attachments=attachments,
            system_context=system_context,
            on_completion=on_completion,
        )

    def _respond(
        self,
        user_input: str,
        *,
        voice: bool = False,
        attachments: list[dict] | None = None,
        system_context: str = "",
        on_completion: Callable[[AssistantCompletion], None] | None = None,
    ) -> Iterator[str]:
        """Shared graph runner for token and completion-aware consumers."""

        if self._profile_managed_prompt:
            self.conversation.system_prompt = cfg.build_system_prompt(db.get_profile())
        self.conversation.add_user(user_input, attachments=attachments)
        messages = self.conversation.to_messages()
        if system_context.strip():
            insert_at = next(
                (
                    index
                    for index, message in enumerate(messages)
                    if message.get("role") != "system"
                ),
                len(messages),
            )
            messages.insert(
                insert_at,
                {"role": "system", "content": system_context.strip()},
            )
        dynamic_specs = None
        skill_tool = None
        if self.use_tools:
            skill_prompt, skill_tool = agent_skills.runtime_access(
                "supervisor", "supervisor"
            )
            if skill_prompt:
                insert_at = next(
                    (
                        index
                        for index, message in enumerate(messages)
                        if message.get("role") != "system"
                    ),
                    len(messages),
                )
                messages.insert(insert_at, {"role": "system", "content": skill_prompt})
            dynamic_specs = (
                [
                    dict(spec)
                    for spec in self.scoped_targets or []
                    if spec.get("kind") == "mcp"
                ]
                if self.scoped_targets is not None
                else mcp_agents.load()
            )
            tree_prompt = mcp_agents.subagent_tree_prompt(dynamic_specs)
            if tree_prompt:
                insert_at = next(
                    (
                        index
                        for index, message in enumerate(messages)
                        if message.get("role") != "system"
                    ),
                    len(messages),
                )
                messages.insert(
                    insert_at, {"role": "system", "content": tree_prompt}
                )
        if self.use_tools and self.automatic_knowledge:
            knowledge_context = automatic_knowledge_context(messages)
            if knowledge_context:
                insert_at = next(
                    (
                        index
                        for index, message in enumerate(messages)
                        if message.get("role") != "system"
                    ),
                    len(messages),
                )
                messages.insert(
                    insert_at,
                    {"role": "system", "content": knowledge_context},
                )
        if voice:
            messages = [dict(message) for message in messages]
            for message in reversed(messages):
                if message.get("role") == "user":
                    message["content"] = (
                        f"{message.get('content') or ''}\n\n{VOICE_RESPONSE_INSTRUCTION}"
                    )
                    break

        initial_messages = convert_to_messages(messages)
        state: TurnState = {
            "messages": initial_messages,
            "tool_rounds": 0,
            "delegations": 0,
            "pending_tool_calls": [],
        }
        input_len = len(initial_messages)
        graph = _compile_graph(
            self.model,
            self.use_tools,
            self.scoped_targets,
            loaded_dynamic=dynamic_specs,
            skill_tool=skill_tool,
            context_history_store=self.subagent_history,
        )
        result_state: TurnState | None = None
        streamed: list[str] = []

        try:
            for event in graph.stream(
                state,
                stream_mode=["custom", "values"],
                version="v2",
            ):
                if event["type"] == "custom":
                    data = event["data"]
                    if (
                        isinstance(data, dict)
                        and data.get("type") == _ASSISTANT_COMPLETION_EVENT
                    ):
                        _report_completion(
                            on_completion,
                            AssistantCompletion(
                                text=str(data.get("text") or "").strip(),
                                has_tool_calls=bool(data.get("has_tool_calls")),
                                is_final=bool(data.get("is_final")),
                            ),
                        )
                        continue
                    chunk = str(data)
                    streamed.append(chunk)
                    yield chunk
                elif event["type"] == "values":
                    result_state = event["data"]
        except Exception as exc:
            chunk = f"\n[agent error: {exc}]"
            streamed.append(chunk)
            _report_completion(
                on_completion,
                AssistantCompletion(
                    text=chunk.strip(),
                    has_tool_calls=False,
                    is_final=True,
                ),
            )
            yield chunk

        produced = (result_state or {}).get("messages", [])[input_len:]
        if produced:
            for message in produced:
                self.conversation.add_message(_stored_message(message))
        else:
            reply = "".join(streamed).strip()
            if reply:
                self.conversation.add_assistant(reply)

    def reset(self) -> None:
        """Clear the supervisor and specialist conversations together."""
        self.conversation.reset()
        self.subagent_history.reset()


def build_graph():
    """Expose the current compiled graph for tests and topology inspection."""

    return _compile_graph(cfg.MODEL, True)
