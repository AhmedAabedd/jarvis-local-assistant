"""LangGraph orchestration for Mounir.

The graph owns the complete supervisor workflow: canonical message state,
conditional routing, typed tool execution, specialist hand-offs, safety caps,
and token streaming.  Provider-specific HTTP/SDK calls remain in ``llm.py``;
everything around them uses LangGraph and LangChain primitives.
"""

from __future__ import annotations

import operator
import threading
from typing import Annotated, Iterator

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

from . import config as cfg, db, graph_runtime, llm, mcp_agents, tools, trace
from .memory import Conversation
from .specialists.knowledge import run as run_knowledge
from .specialists.media import run as run_media
from .specialists.mcp_agent import run as run_mcp_agent
from .specialists.system import run as run_system

MAX_TOOL_ROUNDS = 10
MAX_DELEGATIONS = 3
VOICE_RESPONSE_INSTRUCTION = (
    "MANDATORY VOICE MODE: Reply as natural spoken language only. "
    "Never use Markdown, bullets, tables, code formatting, emojis, or decorative symbols."
)

_DELEGATES = {
    "delegate_to_media": "media",
    "delegate_to_knowledge": "knowledge",
    "delegate_to_system": "system",
}


class TurnState(MessagesState):
    """Canonical LangGraph message state plus bounded-loop counters."""

    tool_rounds: Annotated[int, operator.add]
    delegations: Annotated[int, operator.add]


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
    if not response.tool_calls:
        return Command(goto=END, update={"messages": [response]})

    delegate = next(
        (call for call in response.tool_calls if call.get("name") in delegates),
        None,
    )
    if delegate is not None:
        target = delegates[str(delegate["name"])]
        # A provider may mix delegation and ordinary calls in one response.
        # Handoffs are exclusive so the history contains one valid call/result pair.
        handoff = AIMessage(content=response.content, tool_calls=[delegate])
        trace.gap()
        trace.event(f"→ delegating to {target}")
        return Command(
            goto=target,
            update={"messages": [handoff], "delegations": 1},
        )

    return Command(
        goto="tools",
        update={"messages": [response], "tool_rounds": 1},
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
    if state.get("tool_rounds", 0) >= MAX_TOOL_ROUNDS:
        return "force_final"
    return "supervisor"


def _declined(_state: TurnState) -> dict:
    notice = (
        "Okay, I didn't run it — you declined the command. "
        "Tell me how you'd like to proceed."
    )
    _stream_text(notice)
    return {"messages": [AIMessage(content=notice)]}


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
    return {"messages": [AIMessage(content="".join(parts).strip())]}


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
        report = unavailable
    elif task:
        report = runner(task).strip()
    else:
        report = f"No task was provided to the {node_name} agent."
    trace.block("returned  → supervisor", report)
    trace.gap()
    return Command(
        goto="supervisor",
        update={
            "messages": [
                ToolMessage(
                    content=report,
                    name=tool_name,
                    tool_call_id=call_id,
                )
            ]
        },
    )


def _media(state: TurnState) -> Command:
    enabled = db.is_builtin_agent_enabled("media")
    return _specialist_result(
        state,
        "delegate_to_media",
        "media",
        run_media,
        None if enabled else "The Media agent is inactive and cannot be used.",
    )


def _knowledge(state: TurnState) -> Command:
    enabled = db.is_builtin_agent_enabled("knowledge")
    return _specialist_result(
        state,
        "delegate_to_knowledge",
        "knowledge",
        run_knowledge,
        None if enabled else "The Knowledge agent is inactive and cannot be used.",
    )


def _system(state: TurnState) -> Command:
    enabled = db.is_builtin_agent_enabled("system")
    return _specialist_result(
        state,
        "delegate_to_system",
        "system",
        run_system,
        None if enabled else "The System agent is inactive and cannot be used.",
    )


def _make_mcp_node(
    spec: dict,
    all_specs: list[dict],
    protected_attempts: set[str],
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
            ),
            unavailable,
        )

    return node


def _compile_graph(model: str, use_tools: bool):
    dynamic = mcp_agents.load()
    enabled_builtins = db.enabled_builtin_agent_keys()
    delegates = {
        name: node
        for name, node in _DELEGATES.items()
        if node in enabled_builtins
    }
    root_specs = [
        spec
        for spec in dynamic
        if spec.get("connected_to_supervisor")
        or (
            "connected_to_supervisor" not in spec
            and spec.get("parent_agent_id") is None
        )
    ]
    dynamic_tools = []
    for spec in root_specs:
        name = mcp_agents.delegate_tool_name(spec["name"])
        delegates[name] = mcp_agents.node_name(spec["name"])
        dynamic_tools.append(mcp_agents.delegate_tool(spec))

    builtin_tools = [
        item
        for item in tools.DELEGATE_TOOLS
        if item.name in delegates
    ]
    advertised_tools = [*tools.GENERAL_TOOLS, *builtin_tools, *dynamic_tools]

    declined_batch = threading.Event()
    general_tool_lock = threading.Lock()

    def execute_general_tool(request, execute):
        with general_tool_lock:
            if declined_batch.is_set():
                return ToolMessage(
                    content="Skipped — the user declined a command in this batch.",
                    name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"],
                )
            result = execute(request)
            if (
                isinstance(result, ToolMessage)
                and str(result.content) == tools.USER_DECLINED
            ):
                declined_batch.set()
            return result

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
            tools.GENERAL_TOOLS,
            handle_tool_errors=True,
            wrap_tool_call=execute_general_tool,
        ),
    )
    graph.add_node("declined", _declined)
    graph.add_node("force_final", lambda state: _force_final(state, model))
    if "media" in enabled_builtins:
        graph.add_node("media", _media)
    if "knowledge" in enabled_builtins:
        graph.add_node("knowledge", _knowledge)
    if "system" in enabled_builtins:
        graph.add_node("system", _system)

    protected_attempts: set[str] = set()
    for spec in root_specs:
        graph.add_node(
            mcp_agents.node_name(spec["name"]),
            _make_mcp_node(spec, dynamic, protected_attempts),
        )

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "tools",
        _after_tools,
        {
            "supervisor": "supervisor",
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

    def respond(self, user_input: str, *, voice: bool = False) -> Iterator[str]:
        """Run and stream one complete LangGraph turn."""

        if self._profile_managed_prompt:
            self.conversation.system_prompt = cfg.build_system_prompt(db.get_profile())
        self.conversation.add_user(user_input)
        messages = self.conversation.to_messages()
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
        }
        input_len = len(initial_messages)
        graph = _compile_graph(self.model, self.use_tools)
        result_state: TurnState | None = None
        streamed: list[str] = []

        try:
            for event in graph.stream(
                state,
                stream_mode=["custom", "values"],
                version="v2",
            ):
                if event["type"] == "custom":
                    chunk = str(event["data"])
                    streamed.append(chunk)
                    yield chunk
                elif event["type"] == "values":
                    result_state = event["data"]
        except Exception as exc:
            chunk = f"\n[agent error: {exc}]"
            streamed.append(chunk)
            yield chunk

        produced = (result_state or {}).get("messages", [])[input_len:]
        if produced:
            for message in produced:
                self.conversation.add_message(_stored_message(message))
        else:
            reply = "".join(streamed).strip()
            if reply:
                self.conversation.add_assistant(reply)


def build_graph():
    """Expose the current compiled graph for tests and topology inspection."""

    return _compile_graph(cfg.MODEL, True)
