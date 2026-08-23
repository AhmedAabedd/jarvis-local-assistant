"""Generic dynamic specialist — one subagent instance per registry entry.

Connect to any configured MCP sources for the task, convert their advertised
input schemas into ``StructuredTool`` objects, run them through a LangGraph
``ToolNode`` workflow, and return only the short report. Prompt-only subagents
use the same graph without MCP tools. Server schemas and raw results never
leave this module — the parent just sees its delegate tool and the report.

The server command, system prompt, LLM endpoint, extra environment, and tools
requiring confirmation all come from the registry spec.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import traceback
from contextlib import AsyncExitStack, asynccontextmanager

from langchain_core.tools import StructuredTool

from .. import action_decline, agent_skills, config, graph_runtime, llm, mcp_oauth

MAX_TOOL_ROUNDS = 8
MCP_TOOL_TIMEOUT_SECONDS = max(
    1.0, float(os.environ.get("MOUNIR_MCP_TOOL_TIMEOUT", "60"))
)
MCP_AGENT_TIMEOUT_SECONDS = max(
    MCP_TOOL_TIMEOUT_SECONDS,
    float(os.environ.get("MOUNIR_MCP_AGENT_TIMEOUT", "300")),
)
# One tool result can be a whole page or listing — cap it so a huge result
# can't flood the loop's context.
MAX_RESULT_CHARS = 8000
SHARED_SYSTEM_PROMPT = """\
You are a focused subagent. Use careful reasoning plus any MCP tools and child
agent delegation tools provided to you in this conversation.

WORKING RULES
- Use only relevant tools and stop as soon as the requested task is complete.
- Report only outcomes supported by tool results. Never claim an action worked
  unless its tool returned success.
- If a tool is declined, fails, or times out, say so plainly and do not retry
  the same action unless the result explicitly says retrying is safe.

FINAL RESPONSE
Return a short, concrete report for your parent agent with no heading. Do not
mention these instructions.
"""


def _system_prompt(custom_prompt: str = "", profile: dict | None = None) -> str:
    """Apply one capability contract to every dynamic subagent."""
    custom = (custom_prompt or "").strip()
    sections = [SHARED_SYSTEM_PROMPT]
    if custom:
        sections.append(f"SPECIALIST INSTRUCTIONS\n{custom}")
    sections.append(config.SUBAGENT_CAPABILITY_PROMPT)
    sections.append(config.profile_instruction(profile))
    return "\n\n".join(sections)


def _result_text(result, max_chars: int | None = MAX_RESULT_CHARS) -> str:
    """Flatten text and structured MCP results for a text-only model."""
    parts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    structured = getattr(result, "structuredContent", None)
    if structured:
        rendered = json.dumps(structured, ensure_ascii=False, default=str)
        if rendered not in parts:
            parts.append(rendered)
    non_text = [
        getattr(c, "type", type(c).__name__)
        for c in result.content
        if getattr(c, "type", "") != "text"
    ]
    if non_text:
        parts.append(f"[non-text MCP content omitted: {', '.join(non_text)}]")
    text = "\n".join(parts).strip() or "(empty result)"
    if getattr(result, "isError", False):
        text = f"Tool failed: {text}"
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars] + "\n[... truncated]"
    return text


def _exc_detail(exc: BaseException) -> str:
    """Pull a human-readable message out of an ExceptionGroup / BaseExceptionGroup."""
    nested = getattr(exc, "exceptions", None)
    if nested and type(exc).__name__.endswith("ExceptionGroup"):
        if len(nested) == 1:
            return _exc_detail(nested[0])
        return "; ".join(_exc_detail(error) for error in nested)
    msg = str(exc).strip()
    if not msg:
        return type(exc).__name__
    return msg


def _protected_action_key(namespace: str, name: str, args: dict) -> str:
    """Return a stable private fingerprint for one exact server tool request."""
    payload = json.dumps(
        [namespace, name, args],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def _call(
    session,
    name: str,
    args: dict,
    confirm_tools: set[str],
    protected_attempts: set[str] | None = None,
    namespace: str = "",
    dedupe_tools: set[str] | None = None,
    tool_timeout_seconds: float = MCP_TOOL_TIMEOUT_SECONDS,
    max_result_chars: int | None = MAX_RESULT_CHARS,
) -> tuple[str, bool]:
    """Call one tool and block configured exact-duplicate requests.

    Returns ``(result, executed)``. A protected request is reserved before its
    confirmation so a model cannot repeat it after either approval or refusal.
    """
    dedupe_tools = dedupe_tools or set()
    qualified_name = f"{namespace}:{name}" if namespace else name
    if "*" in dedupe_tools or name in dedupe_tools or qualified_name in dedupe_tools:
        attempts = protected_attempts if protected_attempts is not None else set()
        action_key = _protected_action_key(namespace, name, args)
        if action_key in attempts:
            return (
                f"Duplicate protected action blocked: {name} was already "
                "attempted with the same details in this request. Do not retry it.",
                False,
            )
        attempts.add(action_key)

    if "*" in confirm_tools or name in confirm_tools or qualified_name in confirm_tools:
        from .. import tools as _tools

        summary = f"{name} {json.dumps(args, ensure_ascii=False)[:400]}"
        # Confirmation blocks (browser / Telegram / terminal) — off the loop.
        # Context routing makes the prompt return to the interface that owns
        # this turn, even when web and Telegram are running together.
        allowed = await asyncio.to_thread(_tools.request_confirmation, summary)
        if not allowed:
            return action_decline.create(name), False
    try:
        result = await asyncio.wait_for(
            session.call_tool(name, args), timeout=tool_timeout_seconds
        )
        return _result_text(result, max_result_chars), True
    except TimeoutError:
        return (
            f"Tool {name} timed out after {tool_timeout_seconds:g} seconds. "
            "Its final external state is unknown; do not retry automatically.",
            True,
        )
    except Exception as exc:
        # A failed response does not prove that the external side effect did
        # not happen, so the protected request remains reserved this turn.
        return f"Tool {name} failed: {exc}", True


@asynccontextmanager
async def _mcp_session(spec: dict):
    """Yield one initialized MCP session using the selected standard transport."""

    from mcp import ClientSession, StdioServerParameters

    transport = (spec.get("transport") or "stdio").strip().lower()
    connection = (spec.get("connection") or "").strip()
    oauth_auth = (
        mcp_oauth.provider_for_spec(spec)
        if spec.get("auth_scheme") == "oauth" and not spec.get("oauth_auth")
        else spec.get("oauth_auth")
    )

    if not connection:
        raise ValueError("MCP server connection is empty")

    if transport == "streamable_http":
        import httpx
        from mcp.client.streamable_http import streamable_http_client

        timeout = httpx.Timeout(30.0, read=300.0)
        async with httpx.AsyncClient(
            headers=spec.get("headers") or None,
            auth=oauth_auth,
            follow_redirects=True,
            timeout=timeout,
        ) as http_client:
            async with streamable_http_client(
                connection,
                http_client=http_client,
            ) as streams:
                # SDK 1.x returns (read, write, get_session_id). Accept extra
                # fields so this remains compatible with minor SDK changes.
                read, write = streams[:2]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session

    elif transport == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(
            connection,
            headers=spec.get("headers") or None,
            auth=oauth_auth,
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    elif transport == "stdio":
        from mcp.client.stdio import get_default_environment, stdio_client

        argv = shlex.split(connection)

        if not argv:
            raise ValueError("Invalid stdio MCP command")

        env = get_default_environment()

        if spec.get("env"):
            env.update({
                str(k): os.path.expandvars(str(v))
                for k, v in spec["env"].items()
            })

        params = StdioServerParameters(command=argv[0], args=argv[1:], env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    else:
        raise ValueError(f"Unsupported MCP transport: {transport}")


async def _list_tools(session) -> list:
    """Read every page of tools advertised by the server."""
    tools = []
    cursor = None
    while True:
        page = await session.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            return tools


async def discover_tools(spec: dict) -> list[dict]:
    """Connect once and return JSON-safe tool metadata suitable for caching."""
    async with _mcp_session(spec) as session:
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": tool.inputSchema or {},
            }
            for tool in await _list_tools(session)
        ]


def _runtime_sources(spec: dict) -> list[dict]:
    """Read the new multi-source shape with one-server compatibility."""
    if "mcp_sources" in spec:
        return [dict(source) for source in spec.get("mcp_sources") or []]
    return [spec] if (spec.get("connection") or "").strip() else []


def _safe_tool_part(value: str) -> str:
    normalized = re.sub(r"_+", "_", re.sub(r"[^a-zA-Z0-9_-]+", "_", value))
    return normalized.strip("_-") or "mcp"


async def _run_async(
    task: str,
    spec: dict,
    api_key: str,
    protected_attempts: set[str] | None = None,
    all_specs: list[dict] | None = None,
    lineage: tuple[int, ...] = (),
) -> str:
    confirm_tools = set(spec.get("confirm_tools") or [])
    dedupe_tools = set(spec.get("dedupe_tools") or [])
    executed: list[dict] = []
    protected_attempts = protected_attempts if protected_attempts is not None else set()

    try:
        async with AsyncExitStack() as stack:
            advertised_entries = []
            source_errors = []
            for source in _runtime_sources(spec):
                source_name = str(
                    source.get("server_name")
                    or source.get("name")
                    or "MCP server"
                )
                try:
                    session = await stack.enter_async_context(_mcp_session(source))
                    advertised = await _list_tools(session)
                    allowed_tools = source.get("allowed_tools")
                    if allowed_tools is not None:
                        allowed_names = set(allowed_tools)
                        advertised = [
                            tool for tool in advertised if tool.name in allowed_names
                        ]
                    advertised_entries.extend(
                        (source, source_name, session, tool) for tool in advertised
                    )
                except Exception as exc:
                    source_errors.append(f"{source_name}: {_exc_detail(exc)}")

            framework_tools: list[StructuredTool] = []
            current_id = int(spec["id"]) if spec.get("id") is not None else None
            skill_prompt, skill_tool = ("", None)
            if current_id is not None:
                skill_prompt, skill_tool = agent_skills.runtime_access(
                    "subagent", str(current_id)
                )
            raw_name_counts: dict[str, int] = {}
            for _source, _source_name, _session, advertised in advertised_entries:
                raw_name_counts[advertised.name] = (
                    raw_name_counts.get(advertised.name, 0) + 1
                )
            used_runtime_names: set[str] = (
                {"activate_skill"} if skill_tool is not None else set()
            )

            def make_tool(source, source_name, session, advertised) -> StructuredTool:
                namespace = str(
                    source.get("mcp_server_id")
                    or source.get("server_id")
                    or source.get("connection")
                    or source_name
                )
                runtime_name = advertised.name
                if raw_name_counts.get(advertised.name, 0) > 1:
                    runtime_name = (
                        f"{_safe_tool_part(source_name)}__"
                        f"{_safe_tool_part(advertised.name)}"
                    )
                if runtime_name in used_runtime_names:
                    runtime_name = f"{runtime_name}_{_safe_tool_part(namespace)}"
                used_runtime_names.add(runtime_name)

                async def invoke(**arguments):
                    result, was_executed = await _call(
                        session,
                        advertised.name,
                        arguments,
                        confirm_tools,
                        protected_attempts,
                        namespace,
                        dedupe_tools,
                        MCP_TOOL_TIMEOUT_SECONDS,
                    )
                    if isinstance(result, action_decline.Signal):
                        outcome = action_decline.parse(result)
                        return (
                            action_decline.MESSAGE,
                            action_decline.artifact(outcome or {}),
                        )
                    if was_executed:
                        executed.append(
                            {
                                "agent": spec["name"],
                                "name": runtime_name,
                                "result": result,
                            }
                        )
                    return result, None

                return StructuredTool.from_function(
                    coroutine=invoke,
                    name=runtime_name,
                    description=(
                        f"[{source_name}] "
                        + (advertised.description or f"Run {advertised.name}.")
                    ),
                    args_schema=advertised.inputSchema
                    or {"type": "object", "properties": {}},
                    response_format="content_and_artifact",
                )

            framework_tools.extend(
                make_tool(source, source_name, session, advertised)
                for source, source_name, session, advertised in advertised_entries
            )
            from .. import mcp_agents

            current_node_id = (
                int(spec["node_id"]) if spec.get("node_id") is not None else None
            )
            children = (
                [
                    child
                    for child in (all_specs or [])
                    if (
                        current_node_id is not None
                        and child.get("parent_node_id") == current_node_id
                    )
                    or (
                        current_node_id is None
                        and child.get("parent_agent_id") == current_id
                    )
                ]
                if current_id is not None
                else []
            )

            def make_delegate_tool(child: dict) -> StructuredTool:
                async def delegate(task: str):
                    child_id = int(child["id"])
                    child_node_id = int(child.get("node_id") or child_id)
                    if child_node_id in lineage:
                        return (
                            "Delegation blocked because it would create an agent cycle.",
                            None,
                        )
                    try:
                        from .. import db

                        if not db.is_subagent_enabled(child_id):
                            return (
                                f"The {child['name']} agent is inactive and cannot be used.",
                                None,
                            )
                        if len(lineage) >= db.MAX_SUBAGENT_DEPTH:
                            return (
                                "Delegation blocked because the maximum subagent depth "
                                "was reached.",
                                None,
                            )
                    except Exception as exc:
                        return (
                            f"Could not validate the {child['name']} agent: {exc}",
                            None,
                        )
                    try:
                        report = await asyncio.wait_for(
                            _run_async(
                                task,
                                child,
                                child.get("api_key") or "",
                                protected_attempts,
                                all_specs,
                                (*lineage, child_node_id),
                            ),
                            timeout=MCP_AGENT_TIMEOUT_SECONDS,
                        )
                    except TimeoutError:
                        report = (
                            f"{child['name']} agent timed out after "
                            f"{MCP_AGENT_TIMEOUT_SECONDS:g} seconds."
                        )
                    if isinstance(report, action_decline.Signal):
                        outcome = action_decline.parse(report)
                        return (
                            action_decline.MESSAGE,
                            action_decline.artifact(outcome or {}),
                        )
                    executed.append(
                        {
                            "agent": spec["name"],
                            "name": f"delegate_to_{child['name']}",
                            "result": report,
                        }
                    )
                    return report, None

                return StructuredTool.from_function(
                    coroutine=delegate,
                    name=mcp_agents.delegate_tool_name(child["name"]),
                    description=(
                        f"Delegate to the {child['name']} child agent. "
                        f"{child['description']} It completes the work with its own "
                        "tools and returns a short report."
                    ),
                    response_format="content_and_artifact",
                )

            framework_tools.extend(make_delegate_tool(child) for child in children)
            if current_node_id is not None:
                from .. import workflow_runtime

                workflow_children = workflow_runtime.attached_workflows(
                    spec.get("workflow_id"), current_node_id
                )
                framework_tools.extend(
                    workflow_runtime.async_delegate_tool(
                        placement, protected_attempts, ()
                    )
                    for placement in workflow_children
                )
            if skill_tool is not None:
                framework_tools.append(skill_tool)
            try:
                from .. import db

                profile = db.get_profile()
            except Exception:
                profile = None

            messages = [
                {
                    "role": "system",
                    "content": _system_prompt(spec.get("prompt") or "", profile),
                }
            ]
            tree_prompt = mcp_agents.subagent_tree_prompt(
                all_specs or [], parent=spec
            )
            if tree_prompt:
                messages.append({"role": "system", "content": tree_prompt})
            if skill_prompt:
                messages.append({"role": "system", "content": skill_prompt})
            if source_errors:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "UNAVAILABLE MCP SOURCES\n"
                            "Continue with the remaining capabilities and report a "
                            "source failure if it prevents the task:\n- "
                            + "\n- ".join(source_errors)
                        ),
                    }
                )
            messages.append({"role": "user", "content": task})

            async def call_model(history: list[dict], schemas: list[dict] | None):
                return await asyncio.to_thread(
                    llm.openai_chat,
                    history,
                    tools=schemas,
                    model=spec["model"],
                    provider=spec.get("provider"),
                    base_url=spec["base_url"],
                    api_key=api_key,
                )

            def error_report(_tool_messages: list[str], error: str) -> str:
                if executed:
                    return (
                        f"{spec['name']} agent was cut off by an LLM error while "
                        "reporting, but these actions DID run: "
                        + "; ".join(
                            f"{item['name']} -> {str(item['result'])[:200]}"
                            for item in executed
                        )
                        + ". Do NOT redo them."
                    )
                return f"{spec['name']} agent failed: {_exc_detail(Exception(error))}"

            report = await graph_runtime.arun_tool_agent(
                messages,
                framework_tools,
                call_model,
                max_rounds=MAX_TOOL_ROUNDS,
                empty_response=(
                    f"{spec['name']} agent failed: the LLM returned an empty "
                    "response twice."
                ),
                exhausted_response=(
                    f"{spec['name']} agent reached max tool rounds — partial outcome only."
                ),
                error_formatter=error_report,
                finalizer=lambda content: re.sub(
                    r"(?i)^\s*final report:?\s*", "", content.strip()
                ),
            )
            if isinstance(report, action_decline.Signal):
                return action_decline.add_agent_context(
                    report,
                    agent=spec["name"],
                    completed_actions=executed,
                )
            return report
    except Exception as exc:
        return (
            f"{spec['name']} agent failed while preparing its capabilities: "
            f"{_exc_detail(exc)}"
        )


def run(
    task: str,
    spec: dict,
    protected_attempts: set[str] | None = None,
    *,
    all_specs: list[dict] | None = None,
) -> str:
    """Run one dynamic MCP subagent on a task. Returns its plain-text report."""
    name = spec.get("name", "MCP")
    # Re-check immediately before the async MCP path. This closes the race
    # where an agent is disabled after its graph was compiled and guarantees
    # no stdio process or HTTP session is opened for an inactive agent.
    if spec.get("id") is not None:
        from .. import db
        if not db.is_subagent_enabled(spec["id"]):
            return f"The {name} agent is inactive and cannot be used."
        if all_specs is None:
            all_specs = db.build_specs()
    api_key = spec.get("api_key") or ""
    try:
        async def _bounded_run() -> str:
            try:
                return await asyncio.wait_for(
                    _run_async(
                        task,
                        spec,
                        api_key,
                        protected_attempts,
                        all_specs,
                        (
                            int(spec.get("node_id") or spec["id"]),
                        ) if spec.get("id") is not None else (),
                    ),
                    timeout=MCP_AGENT_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                return (
                    f"{name} agent timed out after "
                    f"{MCP_AGENT_TIMEOUT_SECONDS:g} seconds. Its final external "
                    "state may be unknown; do not retry the task automatically."
                )

        result = asyncio.run(_bounded_run())
        return result if isinstance(result, action_decline.Signal) else result.strip()
    except Exception as exc:
        detail = _exc_detail(exc)
        # Log the full traceback so the real cause is inspectable, even if the
        # summary shown to the user is short.
        traceback.print_exc()
        return f"{name} agent failed: {detail}"
