"""Media and file artifact specialist.

The public tool surface deliberately stays small. Format-specific parsing,
video frame sampling, transcription, and rendering live in ``artifacts`` so
the model chooses an intent instead of micromanaging codecs and libraries.
Raw multimodal content remains isolated here; only the final report returns to
the supervisor.
"""

from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.tools import tool

from .. import agent_skills, config, context_history, graph_runtime, llm
from . import artifacts

MAX_TOOL_ROUNDS = 8


SYSTEM_PROMPT = """\
You are the file and media specialist. You inspect and create local
artifacts without exposing their raw contents to the supervisor.

PATH DISCOVERY — MANDATORY
- Never stop after one guessed literal path fails. A phrase such as "my idea
  folder in Documents" is a location hint, not necessarily a literal path.
- Use find_files with the most likely known folder and a short name query.
  Known folders are discovered from the operating system's XDG configuration,
  so Documents may be localized or stored somewhere unexpected.
- Resolution proceeds from exact paths to known-folder aliases, case-insensitive
  traversal, then bounded fuzzy recursive search. Do not invent additional paths.
- If several plausible candidates are returned, report their full paths and ask
  the supervisor to clarify. Never silently choose an ambiguous match.
- Once resolved, use the full path returned by the tool for later operations.

OPERATION RULES
- If the task names an exact input path, call read_file or load_media immediately.
- Use read_file for source code and ordinary text as well as structured documents.
- Read an existing text file before edit_file. Use append to preserve its current
  content or replace with exact old_text for a surgical change.
- Infer the requested output format from the destination extension.
- For XLSX content, pass JSON with either {"sheets":[{"name":"...","rows":[...]}]}
  or an object mapping sheet names to row arrays.
- For DOCX content, plain text works; structured reports may use title, sections,
  and tables in JSON.
- For PPTX, pass a JSON specification with title, optional subtitle, and slides;
  each slide contains a title and bullets.
- Never guess content that could not be loaded. Report missing dependencies or
  unsupported formats exactly.

REPORT REQUIREMENTS
Include the direct result, exact output path for generated files, key content,
and any limitation the caller needs to know.
"""


def _content(result: artifacts.MediaResult) -> list[dict]:
    summary, parts = result
    return [{"type": "text", "text": summary}, *parts]


@tool("read_file")
def read_file_tool(
    path: Annotated[str, "Path to a PDF, spreadsheet, Word document, or text-like file."],
    start_line: Annotated[int, "First text/source line, starting at 1."] = 1,
    end_line: Annotated[int | None, "Optional inclusive final text/source line."] = None,
) -> list[dict]:
    """Read any text/source file or a supported structured document with bounded output."""

    return _content(artifacts.read_file(path, start_line, end_line))


@tool("create_file")
def create_file_tool(
    path: Annotated[str, "Exact output path including extension."],
    content: Annotated[str, "Complete text or JSON-encoded structured content."],
) -> str:
    """Create a PDF, XLSX, DOCX, CSV, TSV, JSON, or text file."""

    return artifacts.create_file(path, content)


@tool("edit_file")
def edit_file_tool(
    path: Annotated[str, "Existing text/source file path or location hint."],
    operation: Literal["append", "replace"],
    content: Annotated[str, "Text to append or replacement text."],
    old_text: Annotated[str, "Exact existing text required for replace."] = "",
    replace_all: Annotated[bool, "Replace every exact occurrence."] = False,
) -> str:
    """Append to or exactly replace content in a file that was read first."""

    return artifacts.edit_file(path, operation, content, old_text, replace_all)


@tool("load_media")
def load_media_tool(
    path: Annotated[str, "Path to an image, audio file, video, or PPTX presentation."],
) -> list[dict]:
    """Load media for analysis; video sampling and transcription are automatic."""

    return _content(artifacts.load_media(path))


@tool("generate_media")
def generate_media_tool(
    path: Annotated[str, "Exact image, video, or PPTX output path."],
    prompt: Annotated[str, "Generation instructions or presentation summary."],
    specification: Annotated[
        str,
        "Optional JSON presentation specification; omit for image generation.",
    ] = "",
) -> str:
    """Generate an image or presentation through the configured backend."""

    return artifacts.generate_media(path, prompt, specification)


@tool("find_files")
def find_files_tool(
    directory: Annotated[str, "Directory path, known-folder name, or location hint."] = ".",
    query: Annotated[str, "Optional filename fragment."] = "",
    group: Literal["file", "media", "directory", "any"] = "any",
    recursive: Annotated[
        bool,
        "Search below the directory; an empty direct match also triggers this automatically.",
    ] = False,
) -> list[dict]:
    """Find or list files and folders using XDG aliases and bounded fuzzy search."""

    return _content(artifacts.find_files(directory, query, group, recursive))


TOOLS = [
    read_file_tool,
    create_file_tool,
    edit_file_tool,
    load_media_tool,
    generate_media_tool,
    find_files_tool,
]


def run(
    task: str,
    allowed_tools: list[str] | None = None,
    *,
    context_history_store: context_history.ContextHistory | None = None,
) -> str:
    """Run the artifact specialist and return its plain-text report."""
    from .. import db

    artifacts.reset_task_state()
    runtime = db.get_builtin_agent_runtime(
        "media",
        fallback_model=config.MEDIA_MODEL,
        fallback_base_url=config.NVIDIA_BASE_URL,
        fallback_api_key=config.NVIDIA_API_KEY,
        fallback_provider="NVIDIA",
    )
    selected_tools = graph_runtime.select_tools(TOOLS, allowed_tools)
    skill_prompt, skill_tool = agent_skills.runtime_access("builtin", "media")
    if skill_tool is not None:
        selected_tools.append(skill_tool)
    confirmation_tools = db.get_builtin_confirmation_tools("media")
    messages = [
        {"role": "system", "content": config.specialist_system_prompt(SYSTEM_PROMPT)},
    ]
    if skill_prompt:
        messages.append({"role": "system", "content": skill_prompt})
    messages.extend(
        context_history.messages(context_history_store, builtin_key="media")
    )
    messages.append({"role": "user", "content": task})
    report = graph_runtime.run_tool_agent(
        messages,
        selected_tools,
        lambda history, schemas: llm.openai_chat(
            history,
            tools=schemas,
            model=runtime["model"],
            provider=runtime["provider"],
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
        ),
        max_rounds=MAX_TOOL_ROUNDS,
        empty_response="Nothing to report.",
        exhausted_response="Files and Media reached max tool rounds — partial result only.",
        error_formatter=lambda _executed, error: f"Files and Media failed: {error}",
        confirmation_tools=confirmation_tools,
    )
    context_history.remember(
        context_history_store, task, report, builtin_key="media"
    )
    return report
