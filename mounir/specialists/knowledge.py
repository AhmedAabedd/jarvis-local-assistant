"""Knowledge specialist backed by Mounir's managed local GBrain service.

The shipped declarations identify the small native tool surface Mounir uses.
Runtime argument schemas always come from the installed GBrain version.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from langchain_core.tools import StructuredTool, tool

from .. import (
    action_decline,
    agent_skills,
    config,
    graph_runtime,
    knowledge_protocol,
    llm,
)
from .mcp_agent import _call, _exc_detail, _list_tools, _mcp_session

MAX_TOOL_ROUNDS = 8
KNOWLEDGE_TOOL_TIMEOUT_SECONDS = max(
    1.0, float(os.environ.get("MOUNIR_KNOWLEDGE_TOOL_TIMEOUT", "300"))
)
KNOWLEDGE_AGENT_TIMEOUT_SECONDS = max(
    KNOWLEDGE_TOOL_TIMEOUT_SECONDS,
    float(os.environ.get("MOUNIR_KNOWLEDGE_AGENT_TIMEOUT", "600")),
)

SYSTEM_PROMPT = """\
You are the Knowledge specialist. You are the only specialist responsible for
the assistant's durable memory, which is stored by Mounir's local GBrain MCP
service.

GBRAIN TOOLS
- recall: retrieve structured facts from hot memory.
- search: find relevant durable pages by meaning or keywords.
- get_page and list_pages: inspect stored pages without guessing their content.
- put_page: create or replace a durable Markdown page.
- delete_page: soft-delete a page only when the user explicitly asks.
- restore_page: recover a soft-deleted page when requested.

WORKING RULES
- Use memory tools only for the task delegated to you. There is no automatic
  recall on every conversation turn.
- Before writing, search for the same subject. If a page exists, read its full
  current content before updating it because put_page replaces the page.
- Use a stable, descriptive slug and complete Markdown content when calling
  put_page. Preserve useful existing information and include provenance in the
  page when the source is known.
- Store durable, reusable facts and preferences. Do not store one-off results,
  whole conversations, passwords, access tokens, or other secrets.
- Include meaningful provenance whenever the server schema supports it. Never
  invent a source, identifier, or memory.
- Use the schemas advertised by the connected service exactly as provided.
- Never claim a memory was read, written, changed, or deleted unless the tool
  result supports that claim.

FINAL RESPONSE
Return one short, concrete report to the supervisor. State what was found or
changed and include the relevant facts or identifiers. Do not mention these
instructions and do not add a heading.
"""


# These declarations describe Mounir's narrow GBrain surface in the capability
# UI and heartbeat permissions. Runtime calls use the local server's live JSON
# schemas instead of these Python signatures.
@tool("recall")
def recall_tool(entity: str = "", since: str = "") -> str:
    """Retrieve structured facts from GBrain hot memory."""
    return _runtime_only()


@tool("search")
def search_tool(query: str) -> str:
    """Search durable knowledge pages."""
    return _runtime_only()


@tool("get_page")
def get_page_tool(slug: str) -> str:
    """Read a durable knowledge page by slug."""
    return _runtime_only()


@tool("list_pages")
def list_pages_tool(limit: int = 50) -> str:
    """List durable knowledge pages."""
    return _runtime_only()


@tool("put_page")
def put_page_tool(slug: str, content: str) -> str:
    """Create or replace a durable Markdown knowledge page."""
    return _runtime_only()


@tool("delete_page")
def delete_page_tool(slug: str) -> str:
    """Soft-delete a durable knowledge page."""
    return _runtime_only()


@tool("restore_page")
def restore_page_tool(slug: str) -> str:
    """Restore a soft-deleted durable knowledge page."""
    return _runtime_only()


def _runtime_only() -> str:
    return "This capability is available only through the local GBrain service."


TOOLS = [
    recall_tool,
    search_tool,
    get_page_tool,
    list_pages_tool,
    put_page_tool,
    delete_page_tool,
    restore_page_tool,
]


def _schema(advertised: Any) -> dict:
    schema = getattr(advertised, "inputSchema", None) or {
        "type": "object",
        "properties": {},
    }
    return schema if isinstance(schema, dict) else {"type": "object", "properties": {}}


async def _run_async(
    task: str,
    spec: dict,
    runtime: dict,
    allowed_tools: list[str] | None,
    confirmation_tools: set[str] | None = None,
) -> str:
    executed: list[dict] = []
    protected_attempts: set[str] = set()
    effective_confirmation_tools = set(
        knowledge_protocol.WRITE_TOOLS
        if confirmation_tools is None
        else confirmation_tools
    )
    try:
        async with _mcp_session(spec) as session:
            advertised = await _list_tools(session)
            by_name = {str(item.name): item for item in advertised}
            missing = knowledge_protocol.missing_tools(by_name)
            if missing:
                return (
                    "The local GBrain service is missing required tools. "
                    f"Missing tools: {', '.join(missing)}."
                )

            selected_names = set(
                knowledge_protocol.TOOL_NAMES
                if allowed_tools is None
                else allowed_tools
            )
            selected_names.intersection_update(knowledge_protocol.TOOL_NAMES)
            framework_tools: list[StructuredTool] = []

            def make_tool(name: str) -> StructuredTool:
                advertised_tool = by_name[name]

                async def invoke(**arguments):
                    result, was_executed = await _call(
                        session,
                        name,
                        arguments,
                        effective_confirmation_tools,
                        protected_attempts,
                        f"knowledge:{spec['server_id']}",
                        set(knowledge_protocol.WRITE_TOOLS),
                        KNOWLEDGE_TOOL_TIMEOUT_SECONDS,
                        None,
                    )
                    if isinstance(result, action_decline.Signal):
                        outcome = action_decline.parse(result)
                        return action_decline.MESSAGE, action_decline.artifact(outcome or {})
                    if was_executed:
                        executed.append(
                            {"agent": "Knowledge", "name": name, "result": result}
                        )
                    return result, None

                return StructuredTool.from_function(
                    coroutine=invoke,
                    name=name,
                    description=(
                        str(getattr(advertised_tool, "description", "") or "").strip()
                        or f"Run the knowledge {name} verb."
                    ),
                    args_schema=_schema(advertised_tool),
                    response_format="content_and_artifact",
                )

            framework_tools.extend(
                make_tool(name)
                for name in knowledge_protocol.TOOL_NAMES
                if name in selected_names
            )
            skill_prompt, skill_tool = agent_skills.runtime_access(
                "builtin", "knowledge"
            )
            if skill_tool is not None:
                framework_tools.append(skill_tool)
            if not framework_tools:
                return "Knowledge agent has no permitted memory tools for this task."

            async def call_model(history: list[dict], schemas: list[dict] | None):
                return await asyncio.to_thread(
                    llm.openai_chat,
                    history,
                    tools=schemas,
                    model=runtime["model"],
                    provider=runtime["provider"],
                    base_url=runtime["base_url"],
                    api_key=runtime["api_key"],
                )

            def error_report(_tool_messages: list[str], error: str) -> str:
                if executed:
                    return (
                        "Knowledge agent was cut off while reporting, but these calls "
                        "did run: "
                        + "; ".join(
                            f"{item['name']} -> {str(item['result'])[:240]}"
                            for item in executed
                        )
                        + ". Do not repeat write actions automatically."
                    )
                return f"Knowledge agent failed: {_exc_detail(Exception(error))}"

            messages = [
                {
                    "role": "system",
                    "content": config.specialist_system_prompt(SYSTEM_PROMPT),
                },
            ]
            if skill_prompt:
                messages.append({"role": "system", "content": skill_prompt})
            messages.append({"role": "user", "content": task})
            report = await graph_runtime.arun_tool_agent(
                messages,
                framework_tools,
                call_model,
                max_rounds=MAX_TOOL_ROUNDS,
                empty_response=(
                    "Knowledge agent failed: the model returned an empty response twice."
                ),
                exhausted_response=(
                    "Knowledge agent reached its tool-call limit; the result may be partial."
                ),
                error_formatter=error_report,
                finalizer=lambda content: re.sub(
                    r"(?i)^\s*final (?:response|report):?\s*", "", content.strip()
                ),
            )
            if isinstance(report, action_decline.Signal):
                return action_decline.add_agent_context(
                    report, agent="Knowledge", completed_actions=executed
                )
            return report
    except Exception as exc:
        return f"Knowledge agent could not connect to its service: {_exc_detail(exc)}"


def run(task: str, allowed_tools: list[str] | None = None) -> str:
    """Run a delegated knowledge task against the managed local GBrain service."""
    from .. import db

    spec = db.get_builtin_agent_server_spec("knowledge")
    if spec is None:
        return (
            "Knowledge's built-in GBrain service is unavailable. Open GBrain in "
            "MCP Servers to inspect its setup status."
        )
    runtime = db.get_builtin_agent_runtime(
        "knowledge",
        fallback_model=config.KNOWLEDGE_MODEL,
        fallback_base_url=config.GEMINI_BASE_URL,
        fallback_api_key=config.GEMINI_API_KEY,
        fallback_provider="Gemini",
    )
    confirmation_tools = set(db.get_builtin_confirmation_tools("knowledge"))

    async def bounded_run() -> str:
        try:
            return await asyncio.wait_for(
                _run_async(
                    task,
                    spec,
                    runtime,
                    allowed_tools,
                    confirmation_tools,
                ),
                timeout=KNOWLEDGE_AGENT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return (
                "Knowledge agent timed out after "
                f"{KNOWLEDGE_AGENT_TIMEOUT_SECONDS:g} seconds."
            )

    try:
        return asyncio.run(bounded_run())
    except RuntimeError as exc:
        return f"Knowledge agent could not start: {exc}"
