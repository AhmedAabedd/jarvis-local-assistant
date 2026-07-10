"""LangGraph orchestration for Mounir: a supervisor that also does the general
work, plus an isolated coder node, wired together with ``Command`` handoffs.

Graph shape::

    START ─> supervisor ─┬─(answers)──────────────> END
                         └─(needs code)──> coder ─> supervisor ─> END

The supervisor is a single node that runs its own tool loop (web, browser,
shell, email …). When it decides code is needed it calls the ``delegate_to_coder``
tool — instead of running that inline we hand off to the ``coder`` node via
``Command(goto="coder")``. The coder does its isolated work and hands back with
``Command(goto="supervisor")``, returning ONLY its structured report. The
supervisor then sees that report and finishes the turn.

State is just the shared ``messages`` bus (concatenated via a reducer). Routing
is never stored in state — ``Command.goto`` carries it. The coder's internal
file-tool chatter stays local to that node and never enters ``messages``; only
its report crosses back, keeping the orchestrator's context small.

The public API is unchanged: ``Agent.respond()`` still yields reply chunks, so
the CLI, voice loop, and web server keep working without knowing about the graph.
"""

from __future__ import annotations

import json
import operator
import queue
import threading
from importlib import import_module
from typing import Annotated, Iterator, TypedDict

from . import config as cfg, llm, tools
from .memory import Conversation
from .specialists.coder import run as run_coder
from .specialists.knowledge import run as run_knowledge
from .specialists.media import run as run_media
from .specialists.researcher import run as run_researcher
from .specialists.system import run as run_system
from .specialists.email import run as run_email
from . import trace

_langgraph_graph = import_module("langgraph.graph")
END = _langgraph_graph.END
START = _langgraph_graph.START
StateGraph = _langgraph_graph.StateGraph
Command = import_module("langgraph.types").Command

# Safety cap on tool rounds inside one supervisor invocation.
MAX_TOOL_ROUNDS = 10
# Safety cap on supervisor -> specialist -> supervisor hand-offs per turn, so a
# model that keeps re-delegating can't loop forever.
MAX_DELEGATIONS = 3

# Delegation tools mapped to the node they hand off to.
_DELEGATES = {
    "delegate_to_coder": "coder",
    "delegate_to_researcher": "researcher",
    "delegate_to_media": "media",
    "delegate_to_knowledge": "knowledge",
    "delegate_to_system": "system",
    "delegate_to_email": "email",
}


class TurnState(TypedDict):
    # Single shared bus. Each node returns only the NEW messages it produced;
    # the reducer concatenates them. (operator.add keeps our raw dict messages
    # intact rather than coercing them to LangChain message objects.)
    messages: Annotated[list[dict], operator.add]


# --- helpers ----------------------------------------------------------------

def _count_delegations(messages: list[dict]) -> int:
    return sum(
        1
        for m in messages
        if m.get("role") == "tool" and m.get("tool_name") in _DELEGATES
    )


def _extract_delegate(messages: list[dict], tool_name: str) -> tuple[str, str]:
    """Find the most recent call to `tool_name`; return (task, call_id)."""
    for message in reversed(messages):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        for call in message["tool_calls"]:
            fn = call.get("function", {})
            if fn.get("name") != tool_name:
                continue
            args = fn.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            return str((args or {}).get("task", "")), call.get("id", "call_0")
    return "", "call_0"


# --- supervisor node --------------------------------------------------------

def _supervisor(
    state: TurnState,
    stream_q: queue.Queue | None,
    model: str,
    use_tools: bool,
) -> Command:
    schemas = list(tools.SCHEMAS) if use_tools else []
    # Stop offering specialists once we've delegated enough this turn.
    if _count_delegations(state["messages"]) >= MAX_DELEGATIONS:
        schemas = [s for s in schemas if s["function"]["name"] not in _DELEGATES]

    convo = [dict(m) for m in state["messages"]]
    new_messages: list[dict] = []

    for _ in range(MAX_TOOL_ROUNDS):
        tool_calls: list = []
        parts: list[str] = []
        for chunk in llm.chat_stream(
            convo, model=model, tools=schemas or None, tool_calls_out=tool_calls
        ):
            parts.append(chunk)
            if stream_q is not None:
                stream_q.put(chunk)

        if not tool_calls:
            final = "".join(parts).strip()
            new_messages.append({"role": "assistant", "content": final})
            return Command(goto=END, update={"messages": new_messages})

        # Hand off to a specialist node instead of running the delegate inline.
        delegate = next(
            (tc for tc in tool_calls if tc.function.name in _DELEGATES), None
        )
        if delegate is not None:
            target = _DELEGATES[delegate.function.name]
            new_messages.append(
                {
                    "role": "assistant",
                    "content": "".join(parts),
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "function": {
                                "name": delegate.function.name,
                                "arguments": delegate.function.arguments,
                            },
                        }
                    ],
                }
            )
            trace.gap()  # separate the delegation from the thinking spinner
            trace.event(f"→ delegating to {target}")
            return Command(goto=target, update={"messages": new_messages})

        # Otherwise run the general tools inline and loop with their results.
        assistant_msg = {
            "role": "assistant",
            "content": "".join(parts),
            "tool_calls": [
                {
                    "id": f"call_{i}",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for i, tc in enumerate(tool_calls)
            ],
        }
        convo.append(assistant_msg)
        new_messages.append(assistant_msg)
        declined = False
        for i, call in enumerate(tool_calls):
            args = dict(call.function.arguments)
            # After a decline, don't run the rest of the batch — but every
            # tool_call still needs a result message for the history to be valid.
            result = (
                "Skipped — the user declined a command in this batch."
                if declined
                else tools.dispatch(call.function.name, args)
            )
            trace.tool(call.function.name, args, result)
            tool_msg = {
                "role": "tool",
                "tool_name": call.function.name,
                "tool_call_id": f"call_{i}",
                "content": result,
            }
            convo.append(tool_msg)
            new_messages.append(tool_msg)
            if result == tools.USER_DECLINED:
                declined = True
        if declined:
            # End the turn with a FIXED reply instead of letting the model
            # react to the decline (it tends to retry the command or argue).
            notice = "Okay, I didn't run it — you declined the command. Tell me how you'd like to proceed."
            if stream_q is not None:
                stream_q.put(notice)
            new_messages.append({"role": "assistant", "content": notice})
            return Command(goto=END, update={"messages": new_messages})

    # Tool-round cap hit — force a final, tool-free answer.
    parts = []
    for chunk in llm.chat_stream(convo, model=model, tools=None):
        parts.append(chunk)
        if stream_q is not None:
            stream_q.put(chunk)
    final = "".join(parts).strip()
    new_messages.append({"role": "assistant", "content": final})
    return Command(goto=END, update={"messages": new_messages})


# --- coder node -------------------------------------------------------------

def _coder(state: TurnState) -> Command:
    task, call_id = _extract_delegate(state["messages"], "delegate_to_coder")
    trace.node("coder")
    trace.block("received  ← supervisor", task)

    report = run_coder(task).strip() if task else "No task was provided to the coder."

    trace.block("returned  → supervisor", report)
    trace.gap()  # breathing room before the supervisor's reply streams in
    # The report is the result of the delegate_to_coder tool call, handed back
    # to the supervisor. The coder's own file-tool chatter never leaves the node.
    return Command(
        goto="supervisor",
        update={
            "messages": [
                {
                    "role": "tool",
                    "tool_name": "delegate_to_coder",
                    "tool_call_id": call_id,
                    "content": report,
                }
            ]
        },
    )


# --- researcher node --------------------------------------------------------

def _researcher(state: TurnState) -> Command:
    task, call_id = _extract_delegate(state["messages"], "delegate_to_researcher")
    trace.node("researcher")
    trace.block("received  ← supervisor", task)

    report = run_researcher(task).strip() if task else "No task was provided to the researcher."

    trace.block("returned  → supervisor", report)
    trace.gap()  # breathing room before the supervisor's reply streams in
    # Only the synthesized report (with sources) crosses back; the search/fetch
    # chatter and raw page text stay inside this node.
    return Command(
        goto="supervisor",
        update={
            "messages": [
                {
                    "role": "tool",
                    "tool_name": "delegate_to_researcher",
                    "tool_call_id": call_id,
                    "content": report,
                }
            ]
        },
    )


# --- media node -------------------------------------------------------------

def _media(state: TurnState) -> Command:
    task, call_id = _extract_delegate(state["messages"], "delegate_to_media")
    trace.node("media")
    trace.block("received  ← supervisor", task)

    report = run_media(task).strip() if task else "No task was provided to the media agent."

    trace.block("returned  → supervisor", report)
    trace.gap()  # breathing room before the supervisor's reply streams in
    # Only the text report crosses back; the raw image/audio bytes loaded inside
    # the node never enter `messages`, keeping the orchestrator's context small.
    return Command(
        goto="supervisor",
        update={
            "messages": [
                {
                    "role": "tool",
                    "tool_name": "delegate_to_media",
                    "tool_call_id": call_id,
                    "content": report,
                }
            ]
        },
    )


# --- knowledge agent node ---------------------------------------------------------

def _knowledge(state: TurnState) -> Command:
    task, call_id = _extract_delegate(state["messages"], "delegate_to_knowledge")
    trace.node("knowledge")
    trace.block("received  ← supervisor", task)

    report = run_knowledge(task).strip() if task else "No task was provided to the knowledge agent."

    trace.block("returned  → supervisor", report)
    trace.gap()  # breathing room before the supervisor's reply streams in
    # Only the short curation report crosses back; the knowledge agent's file reads
    # and index bookkeeping stay inside this node.
    return Command(
        goto="supervisor",
        update={
            "messages": [
                {
                    "role": "tool",
                    "tool_name": "delegate_to_knowledge",
                    "tool_call_id": call_id,
                    "content": report,
                }
            ]
        },
    )


# --- system node ------------------------------------------------------------

def _system(state: TurnState) -> Command:
    task, call_id = _extract_delegate(state["messages"], "delegate_to_system")
    trace.node("system")
    trace.block("received  ← supervisor", task)

    report = run_system(task).strip() if task else "No task was provided to the system agent."

    trace.block("returned  → supervisor", report)
    trace.gap()  # breathing room before the supervisor's reply streams in
    # Only the short hardware report crosses back; the command chatter stays here.
    return Command(
        goto="supervisor",
        update={
            "messages": [
                {
                    "role": "tool",
                    "tool_name": "delegate_to_system",
                    "tool_call_id": call_id,
                    "content": report,
                }
            ]
        },
    )


# --- email node ---------------------------------------------------------------

def _email(state: TurnState) -> Command:
    task, call_id = _extract_delegate(state["messages"], "delegate_to_email")
    trace.node("email")
    trace.block("received  ← supervisor", task)

    report = run_email(task).strip() if task else "No task was provided to the email agent."

    trace.block("returned  → supervisor", report)
    trace.gap()  # breathing room before the supervisor's reply streams in
    # Only the short report crosses back; the Gmail payloads stay here.
    return Command(
        goto="supervisor",
        update={
            "messages": [
                {
                    "role": "tool",
                    "tool_name": "delegate_to_email",
                    "tool_call_id": call_id,
                    "content": report,
                }
            ]
        },
    )


# --- graph ------------------------------------------------------------------

def _compile_graph(stream_q: queue.Queue | None, model: str, use_tools: bool):
    """Compile the supervisor/coder graph, binding this turn's runtime values.

    The runtime values (stream queue, model, tool toggle) are captured by the
    node closures rather than threaded through state or config — simplest and
    most robust with LangGraph's node-signature handling. The supervisor and the
    specialist nodes route dynamically via Command(goto=...).
    """
    graph = StateGraph(TurnState)
    graph.add_node(
        "supervisor",
        lambda state: _supervisor(state, stream_q, model, use_tools),
    )
    graph.add_node("coder", _coder)
    graph.add_node("researcher", _researcher)
    graph.add_node("media", _media)
    graph.add_node("knowledge", _knowledge)
    graph.add_node("system", _system)
    graph.add_node("email", _email)
    graph.add_edge(START, "supervisor")
    return graph.compile()


class Agent:
    def __init__(
        self,
        conversation: Conversation | None = None,
        model: str = cfg.MODEL,
        use_tools: bool = True,
    ) -> None:
        if conversation is None:
            conversation = Conversation(system_prompt=cfg.SYSTEM_PROMPT)
        self.conversation = conversation
        self.model = model
        self.use_tools = use_tools

    def respond(self, user_input: str) -> Iterator[str]:
        """Run one user turn through the graph, streaming the reply chunks."""
        self.conversation.add_user(user_input)
        stream_q: queue.Queue[str | None] = queue.Queue()
        state: TurnState = {"messages": self.conversation.to_messages()}
        input_len = len(state["messages"])  # everything after this is new this turn
        graph = _compile_graph(stream_q, self.model, self.use_tools)
        result: dict = {}

        def _run_graph() -> None:
            try:
                result["state"] = graph.invoke(state)
            except Exception as exc:  # surface failures to the stream
                stream_q.put(f"\n[agent error: {exc}]")
            finally:
                stream_q.put(None)

        worker = threading.Thread(target=_run_graph, daemon=True)
        worker.start()

        chunks: list[str] = []
        while True:
            chunk = stream_q.get()
            if chunk is None:
                break
            chunks.append(chunk)
            yield chunk
        worker.join()

        # Persist the FULL turn — every assistant tool-call and tool result this
        # turn produced, not just the final reply — so the next turn replays the
        # real history and the supervisor keeps the paths/values/sources it found.
        # Specialist internals (file/web chatter) never entered `messages`, so only
        # their compact reports cross over. to_messages() trims this to the window
        # and re-pairs any tool result whose call got trimmed off.
        produced = (result.get("state") or {}).get("messages", [])[input_len:]
        if produced:
            for message in produced:
                self.conversation.add_message(message)
        else:  # graph errored before returning state — at least keep the reply
            reply = "".join(chunks).strip()
            if reply:
                self.conversation.add_assistant(reply)


def build_graph():
    """Expose a compiled graph for tests/inspection (no streaming bound)."""
    return _compile_graph(None, cfg.MODEL, True)
