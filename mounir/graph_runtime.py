"""Shared LangGraph runtime for tool-using specialist agents.

Provider adapters in :mod:`mounir.llm` deliberately stay small and return the
OpenAI-compatible message shape supported by all configured endpoints.  This
module owns everything around the provider call: LangChain message conversion,
tool schema generation, argument validation/execution through ``ToolNode``, and
the bounded model/tool graph.

Keeping that workflow here means specialists only define typed Python tools and
their model call.  They no longer need to maintain JSON schemas, registries,
dispatchers, or subtly different copies of the same agent loop.
"""

from __future__ import annotations

import asyncio
import json
import operator
import threading
from collections.abc import Awaitable, Callable, Sequence
from typing import Annotated

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
    convert_to_messages,
    convert_to_openai_messages,
)
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from . import action_decline, tool_outcome, trace, tools as mounir_tools

ModelCall = Callable[[list[dict], list[dict] | None], dict]
AsyncModelCall = Callable[[list[dict], list[dict] | None], Awaitable[dict]]
ErrorFormatter = Callable[[list[str], str], str]
Finalizer = Callable[[str], str]


class ToolAgentState(MessagesState):
    """State shared by every bounded specialist graph."""

    model_rounds: Annotated[int, operator.add]
    empty_responses: Annotated[int, operator.add]
    error: str


def tool_schemas(tools: Sequence[BaseTool]) -> list[dict]:
    """Return provider-ready schemas generated from typed LangChain tools."""

    return [convert_to_openai_tool(item) for item in tools]


def select_tools(
    tools: Sequence[BaseTool], allowed_names: Sequence[str] | None
) -> list[BaseTool]:
    """Apply a code-enforced allowlist while preserving declared tool order."""

    if allowed_names is None:
        return list(tools)
    allowed = {str(name) for name in allowed_names}
    return [item for item in tools if item.name in allowed]


def message_dicts(messages: Sequence[BaseMessage | dict]) -> list[dict]:
    """Convert LangGraph messages to the common provider message format."""

    converted = convert_to_openai_messages(list(messages))
    return converted if isinstance(converted, list) else [converted]


def ai_message(raw: dict, *, call_prefix: str = "call") -> AIMessage:
    """Normalize a provider response into LangChain's canonical ``AIMessage``."""

    calls = []
    for index, item in enumerate(raw.get("tool_calls") or []):
        function = item.get("function") if isinstance(item, dict) else None
        if function is None:
            function = getattr(item, "function", None)
        name = (
            function.get("name")
            if isinstance(function, dict)
            else getattr(function, "name", "")
        )
        arguments = (
            function.get("arguments", {})
            if isinstance(function, dict)
            else getattr(function, "arguments", {})
        )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except (TypeError, ValueError):
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        call_id = (
            item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        ) or f"{call_prefix}_{index}"
        calls.append(
            {
                "name": str(name or ""),
                "args": arguments,
                "id": str(call_id),
                "type": "tool_call",
            }
        )
    return AIMessage(content=raw.get("content") or "", tool_calls=calls)


def _executed_tools(messages: Sequence[BaseMessage]) -> list[str]:
    return [
        f"{message.name or 'tool'} -> {str(message.content)[:200]}"
        for message in messages
        if isinstance(message, ToolMessage)
    ]


def trace_tool_messages(messages: Sequence[BaseMessage]) -> None:
    """Trace tool results with the typed arguments from their matching calls."""

    arguments = {
        str(call.get("id")): dict(call.get("args") or {})
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls
    }
    for message in messages:
        if isinstance(message, ToolMessage):
            result = (
                action_decline.MESSAGE
                if action_decline.from_artifact(
                    getattr(message, "artifact", None)
                )
                else str(message.content)
            )
            trace.tool(
                message.name or "tool",
                arguments.get(message.tool_call_id, {}),
                result,
            )


def run_tool_agent(
    messages: Sequence[BaseMessage | dict],
    tools: Sequence[BaseTool],
    model_call: ModelCall,
    *,
    max_rounds: int,
    empty_response: str,
    exhausted_response: str,
    error_formatter: ErrorFormatter,
    finalizer: Finalizer | None = None,
    confirmation_tools: Sequence[str] | None = None,
) -> str:
    """Run a bounded model -> tools -> model workflow with LangGraph.

    ``ToolNode`` provides argument validation, parallel execution, error
    conversion, and correctly paired ``ToolMessage`` objects.  The model
    adapter remains injectable because this project supports several compatible
    providers without forcing a provider-specific LangChain integration.
    """

    available_tools = list(tools)
    available_tool_names = {tool.name for tool in available_tools}
    schemas = tool_schemas(available_tools)
    confirmation_rules = {str(name) for name in confirmation_tools or []}
    tool_lock = threading.Lock()
    declined_signal: dict[str, dict | None] = {"value": None}

    def call_model(state: ToolAgentState) -> dict | Command:
        try:
            response = model_call(message_dicts(state["messages"]), schemas or None)
        except Exception as exc:
            return Command(goto="failure", update={"error": str(exc)})
        message = ai_message(
            response, call_prefix=f"call_{state.get('model_rounds', 0)}"
        )
        empty = not str(message.content).strip() and not message.tool_calls
        return {
            "messages": [message],
            "model_rounds": 1,
            "empty_responses": 1 if empty else 0,
        }

    def after_model(state: ToolAgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        if isinstance(last, AIMessage) and str(last.content).strip():
            return END
        return "empty" if state.get("empty_responses", 0) >= 2 else "model"

    def failure(state: ToolAgentState) -> dict:
        report = error_formatter(
            _executed_tools(state["messages"]), state.get("error", "unknown error")
        )
        return {"messages": [AIMessage(content=report)]}

    def fixed_message(content: str) -> Callable[[ToolAgentState], dict]:
        return lambda _state: {"messages": [AIMessage(content=content)]}

    def find_decline(state: ToolAgentState) -> dict | None:
        for message in reversed(state["messages"]):
            if isinstance(message, AIMessage):
                break
            if isinstance(message, ToolMessage):
                outcome = action_decline.from_artifact(
                    getattr(message, "artifact", None)
                )
                if outcome is not None:
                    return outcome
        return None

    def after_tools(state: ToolAgentState) -> str:
        if find_decline(state):
            return "declined"
        return (
            "exhausted"
            if state.get("model_rounds", 0) >= max_rounds
            else "model"
        )

    def declined(state: ToolAgentState) -> dict:
        outcome = find_decline(state) or declined_signal["value"]
        return {
            "messages": [AIMessage(content=action_decline.encode(outcome or {}))]
        }

    def execute_sequentially(request, execute):
        """Confirm configured calls and stop the remaining batch on refusal."""
        with tool_lock:
            if declined_signal["value"]:
                return tool_outcome.ToolOutcome.skipped(
                    "Skipped — an earlier action was declined."
                ).as_tool_message(
                    name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"],
                )
            name = str(request.tool_call["name"])
            if name in available_tool_names and (
                "*" in confirmation_rules or name in confirmation_rules
            ):
                arguments = dict(request.tool_call.get("args") or {})
                summary = f"{name} {json.dumps(arguments, ensure_ascii=False)[:400]}"
                if not mounir_tools.request_confirmation(summary):
                    signal = action_decline.create(name)
                    outcome = action_decline.parse(signal) or {}
                    declined_signal["value"] = outcome
                    return tool_outcome.ToolOutcome.declined(
                        action_decline.MESSAGE,
                        action_decline.artifact(outcome),
                    ).as_tool_message(
                        name=name,
                        tool_call_id=request.tool_call["id"],
                    )
            result = execute(request)
            if isinstance(result, ToolMessage):
                outcome = action_decline.from_artifact(
                    getattr(result, "artifact", None)
                )
                if outcome is not None:
                    declined_signal["value"] = outcome
                result = tool_outcome.normalize(
                    result,
                    outcome_status="declined" if outcome is not None else None,
                )
            return result

    def normalize_execution(request, execute):
        result = execute(request)
        return (
            tool_outcome.normalize(result)
            if isinstance(result, ToolMessage)
            else result
        )

    tool_node = ToolNode(
        available_tools,
        handle_tool_errors=True,
        wrap_tool_call=(
            execute_sequentially
            if confirmation_tools is not None
            else normalize_execution
        ),
    )

    graph = StateGraph(ToolAgentState)
    graph.add_node("model", call_model)
    graph.add_node("tools", tool_node)
    graph.add_node("failure", failure)
    graph.add_node("empty", fixed_message(empty_response))
    graph.add_node("exhausted", fixed_message(exhausted_response))
    graph.add_node("declined", declined)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model",
        after_model,
        {"model": "model", "tools": "tools", "empty": "empty", END: END},
    )
    graph.add_conditional_edges(
        "tools",
        after_tools,
        {"model": "model", "declined": "declined", "exhausted": "exhausted"},
    )
    graph.add_edge("failure", END)
    graph.add_edge("empty", END)
    graph.add_edge("exhausted", END)
    graph.add_edge("declined", END)

    initial = {
        "messages": convert_to_messages(list(messages)),
        "model_rounds": 0,
        "empty_responses": 0,
        "error": "",
    }
    result = graph.compile().invoke(initial)

    trace_tool_messages(result["messages"])
    trace.event(f"{result.get('model_rounds', 0)} round(s)")

    final = next(
        (
            str(message.content)
            for message in reversed(result["messages"])
            if isinstance(message, AIMessage) and str(message.content).strip()
        ),
        empty_response,
    )
    if declined_signal["value"] is not None:
        return action_decline.Signal(final.strip())
    return finalizer(final) if finalizer else final.strip()


async def arun_tool_agent(
    messages: Sequence[BaseMessage | dict],
    tools: Sequence[BaseTool],
    model_call: AsyncModelCall,
    *,
    max_rounds: int,
    empty_response: str,
    exhausted_response: str,
    error_formatter: ErrorFormatter,
    finalizer: Finalizer | None = None,
) -> str:
    """Async counterpart used for stateful MCP sessions and async tools."""

    available_tools = list(tools)
    schemas = tool_schemas(available_tools)
    tool_lock = asyncio.Lock()
    declined_signal: dict[str, dict | None] = {"value": None}

    async def call_model(state: ToolAgentState) -> dict | Command:
        try:
            response = await model_call(
                message_dicts(state["messages"]), schemas or None
            )
        except Exception as exc:
            return Command(goto="failure", update={"error": str(exc)})
        message = ai_message(
            response, call_prefix=f"call_{state.get('model_rounds', 0)}"
        )
        empty = not str(message.content).strip() and not message.tool_calls
        return {
            "messages": [message],
            "model_rounds": 1,
            "empty_responses": 1 if empty else 0,
        }

    def after_model(state: ToolAgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        if isinstance(last, AIMessage) and str(last.content).strip():
            return END
        return "empty" if state.get("empty_responses", 0) >= 2 else "model"

    def failure(state: ToolAgentState) -> dict:
        report = error_formatter(
            _executed_tools(state["messages"]), state.get("error", "unknown error")
        )
        return {"messages": [AIMessage(content=report)]}

    def fixed_message(content: str) -> Callable[[ToolAgentState], dict]:
        return lambda _state: {"messages": [AIMessage(content=content)]}

    def find_decline(state: ToolAgentState) -> dict | None:
        for message in reversed(state["messages"]):
            if isinstance(message, AIMessage):
                break
            if isinstance(message, ToolMessage):
                outcome = action_decline.from_artifact(
                    getattr(message, "artifact", None)
                )
                if outcome is not None:
                    return outcome
        return None

    def after_tools(state: ToolAgentState) -> str:
        if find_decline(state):
            return "declined"
        return (
            "exhausted"
            if state.get("model_rounds", 0) >= max_rounds
            else "model"
        )

    def declined(state: ToolAgentState) -> dict:
        outcome = find_decline(state) or declined_signal["value"]
        return {
            "messages": [AIMessage(content=action_decline.encode(outcome or {}))]
        }

    async def execute_sequentially(request, execute):
        """Preserve tool order and skip every remaining call after a refusal."""
        async with tool_lock:
            if declined_signal["value"]:
                return tool_outcome.ToolOutcome.skipped(
                    "Skipped — an earlier action was declined."
                ).as_tool_message(
                    name=request.tool_call["name"],
                    tool_call_id=request.tool_call["id"],
                )
            result = await execute(request)
            if isinstance(result, ToolMessage):
                outcome = action_decline.from_artifact(
                    getattr(result, "artifact", None)
                )
                if outcome is not None:
                    declined_signal["value"] = outcome
                result = tool_outcome.normalize(
                    result,
                    outcome_status="declined" if outcome is not None else None,
                )
            return result

    graph = StateGraph(ToolAgentState)
    graph.add_node("model", call_model)
    graph.add_node(
        "tools",
        ToolNode(
            available_tools,
            handle_tool_errors=True,
            awrap_tool_call=execute_sequentially,
        ),
    )
    graph.add_node("failure", failure)
    graph.add_node("empty", fixed_message(empty_response))
    graph.add_node("exhausted", fixed_message(exhausted_response))
    graph.add_node("declined", declined)
    graph.add_edge(START, "model")
    graph.add_conditional_edges(
        "model",
        after_model,
        {
            "model": "model",
            "tools": "tools",
            "empty": "empty",
            "exhausted": "exhausted",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "tools",
        after_tools,
        {"model": "model", "declined": "declined", "exhausted": "exhausted"},
    )
    graph.add_edge("failure", END)
    graph.add_edge("empty", END)
    graph.add_edge("exhausted", END)
    graph.add_edge("declined", END)

    result = await graph.compile().ainvoke(
        {
            "messages": convert_to_messages(list(messages)),
            "model_rounds": 0,
            "empty_responses": 0,
            "error": "",
        }
    )
    trace_tool_messages(result["messages"])
    trace.event(f"{result.get('model_rounds', 0)} round(s)")
    final = next(
        (
            str(message.content)
            for message in reversed(result["messages"])
            if isinstance(message, AIMessage) and str(message.content).strip()
        ),
        empty_response,
    )
    if declined_signal["value"] is not None:
        return action_decline.Signal(final.strip())
    return finalizer(final) if finalizer else final.strip()
