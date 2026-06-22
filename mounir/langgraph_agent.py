"""LangGraph-backed orchestration for Mounir.

The graph keeps control flow separate from state:
    - routing is an external graph decision, not stored in the turn state
    - supervisor handles general assistant work with shared tools
    - coder handles coding-only work with the isolated coder specialist

The public API stays the same as the previous agent module: `Agent.respond()`
still yields reply chunks, so the CLI, voice loop, and web server can keep
using the assistant without knowing about the graph internals.
"""

from __future__ import annotations

from importlib import import_module
from typing import Iterator, TypedDict

from . import config, llm, tools
from .memory import Conversation
from .sentences import iter_sentences
from .specialists.coder import run as run_coder

_langgraph_graph = import_module("langgraph.graph")
END = _langgraph_graph.END
START = _langgraph_graph.START
StateGraph = _langgraph_graph.StateGraph

MAX_TOOL_ROUNDS = 10
ROUTER_MODEL = config.MODEL


class TurnState(TypedDict, total=False):
    messages: list[dict]
    memory_results: list[dict]
    tool_results: dict
    artifacts: list[dict]
    current_task: str
    completed_tasks: list[str]
    model: str
    use_tools: bool
    reply: str


_CODING_HINTS = (
    "code",
    "coding",
    "bug",
    "fix",
    "refactor",
    "implement",
    "write a script",
    "edit",
    "modify",
    "file",
    "module",
    "function",
    "class",
    "test",
    "patch",
    "repository",
    "langgraph",
    "python",
    "javascript",
    "typescript",
)


def _latest_user_message(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


def _recent_dialogue(messages: list[dict], limit: int = 6) -> str:
    recent = [m for m in messages if m.get("role") in {"user", "assistant"}][-limit:]
    lines: list[str] = []
    for message in recent:
        role = message.get("role", "")
        content = str(message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _looks_like_coding_task(text: str) -> bool:
    normalized = f" {text.lower()} "
    return any(f" {hint} " in normalized for hint in _CODING_HINTS)


def _route_request(state: TurnState) -> str:
    messages = state.get("messages", [])
    latest_user = _latest_user_message(messages)
    if not latest_user:
        return "supervisor"

    if _looks_like_coding_task(latest_user):
        return "coder"

    routing_prompt = [
        {
            "role": "system",
            "content": (
                "You route one user turn for Mounir. Decide whether the dedicated "
                "coder node should handle the turn. Reply with exactly one word: "
                "coder or supervisor. Use coder only for coding, debugging, "
                "refactoring, file edits, scripts, or code generation. Use "
                "supervisor for everything else."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Recent dialogue:\n{_recent_dialogue(messages)}\n\n"
                f"Latest request:\n{latest_user}\n\n"
                "Route:"
            ),
        },
    ]

    chunks: list[str] = []
    try:
        for chunk in llm.chat_stream(
            routing_prompt,
            model=str(state.get("model") or ROUTER_MODEL),
            tools=None,
            think=False,
        ):
            chunks.append(chunk)
    except Exception:
        return "supervisor"

    lowered = "".join(chunks).strip().lower()
    if "coder" in lowered:
        return "coder"
    return "supervisor"


def _run_tool_loop(
    messages: list[dict],
    *,
    model: str,
    schemas: list[dict],
) -> tuple[str, list[dict]]:
    conversation = [dict(message) for message in messages]
    tool_results: list[dict] = []

    for _ in range(MAX_TOOL_ROUNDS):
        tool_calls: list = []
        parts: list[str] = []

        for chunk in llm.chat_stream(
            conversation,
            model=model,
            tools=schemas,
            tool_calls_out=tool_calls,
        ):
            parts.append(chunk)

        if not tool_calls:
            return "".join(parts).strip(), tool_results

        conversation.append(
            {
                "role": "assistant",
                "content": "".join(parts),
                "tool_calls": [
                    {
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        }
                    }
                    for call in tool_calls
                ],
            }
        )

        for index, call in enumerate(tool_calls):
            result = tools.dispatch(call.function.name, dict(call.function.arguments))
            tool_results.append(
                {
                    "name": call.function.name,
                    "arguments": dict(call.function.arguments),
                    "result": result,
                }
            )
            conversation.append(
                {
                    "role": "tool",
                    "tool_name": call.function.name,
                    "tool_call_id": f"call_{index}",
                    "content": result,
                }
            )

    parts: list[str] = []
    for chunk in llm.chat_stream(conversation, model=model, tools=None):
        parts.append(chunk)
    return "".join(parts).strip(), tool_results


def _append_assistant_message(messages: list[dict], content: str) -> list[dict]:
    updated = [dict(message) for message in messages]
    updated.append({"role": "assistant", "content": content})
    return updated


def _ensure_task_tracking(state: TurnState) -> tuple[str, list[str]]:
    current_task = str(state.get("current_task") or "")
    completed_tasks = list(state.get("completed_tasks") or [])
    return current_task, completed_tasks


def _supervisor_turn(state: TurnState) -> TurnState:
    schemas = tools.SUPERVISOR_SCHEMAS if state.get("use_tools", True) else []
    reply, tool_results = _run_tool_loop(
        state.get("messages", []),
        model=str(state.get("model") or config.MODEL),
        schemas=schemas,
    )
    current_task, completed_tasks = _ensure_task_tracking(state)
    updated_messages = _append_assistant_message(state.get("messages", []), reply)
    return {
        "messages": updated_messages,
        "tool_results": {"general": tool_results},
        "completed_tasks": completed_tasks + ([current_task] if current_task else []),
        "reply": reply,
    }


def _build_coder_task(messages: list[dict]) -> str:
    latest_user = _latest_user_message(messages)
    recent = [m for m in messages if m.get("role") in {"user", "assistant"}][-8:]
    context_lines: list[str] = []
    for message in recent:
        role = message.get("role", "")
        content = str(message.get("content") or "").strip()
        if content:
            context_lines.append(f"{role}: {content}")

    return "\n".join(
        [
            "Coding task for the dedicated coder agent.",
            "",
            "Recent conversation:",
            *context_lines,
            "",
            "User request to implement:",
            latest_user,
            "",
            "Do the coding work only. Use the coder's file tools as needed.",
            "Return a short status summary with the files changed and what was done.",
        ]
    ).strip()


def _coder_turn(state: TurnState) -> TurnState:
    task = _build_coder_task(state.get("messages", []))
    reply = run_coder(task).strip()
    current_task, completed_tasks = _ensure_task_tracking(state)
    updated_messages = _append_assistant_message(state.get("messages", []), reply)
    return {
        "messages": updated_messages,
        "tool_results": {"coder": reply},
        "completed_tasks": completed_tasks + ([current_task] if current_task else []),
        "reply": reply,
    }


def _build_graph():
    graph = StateGraph(TurnState)
    graph.add_node("supervisor", _supervisor_turn)
    graph.add_node("coder", _coder_turn)
    graph.add_conditional_edges(
        START,
        _route_request,
        {
            "supervisor": "supervisor",
            "coder": "coder",
        },
    )
    graph.add_edge("supervisor", END)
    graph.add_edge("coder", END)
    return graph.compile()


_GRAPH = _build_graph()


class Agent:
    def __init__(
        self,
        conversation: Conversation | None = None,
        model: str = config.MODEL,
        use_tools: bool = True,
    ) -> None:
        system = config.SYSTEM_PROMPT
        if conversation is None:
            conversation = Conversation(system_prompt=system)
        self.conversation = conversation
        self.model = model
        self.use_tools = use_tools

    def respond(self, user_input: str) -> Iterator[str]:
        """Run one user turn through the LangGraph workflow."""
        self.conversation.add_user(user_input)
        state: TurnState = {
            "messages": self.conversation.to_messages(),
            "memory_results": [],
            "tool_results": {},
            "artifacts": [],
            "current_task": user_input,
            "completed_tasks": [],
            "model": self.model,
            "use_tools": self.use_tools,
        }
        result = _GRAPH.invoke(state)
        messages = result.get("messages") or []
        reply = str(result.get("reply") or "").strip()
        if not reply:
            for message in reversed(messages):
                if message.get("role") == "assistant":
                    reply = str(message.get("content") or "").strip()
                    if reply:
                        break

        if reply:
            yield from iter_sentences([reply])
            self.conversation.add_assistant(reply)


def build_graph():
    """Expose the compiled graph for tests or future integration."""
    return _GRAPH
