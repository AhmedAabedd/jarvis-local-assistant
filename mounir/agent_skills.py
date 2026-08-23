"""Portable Agent Skills packages and their read-only runtime activation tool."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import stat
import zipfile
from pathlib import PurePosixPath
from typing import Iterable

import yaml
from langchain_core.tools import StructuredTool

MAX_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_SKILL_MD_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_FILES = 256
_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _safe_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("The skill package contains an unsafe file path.")
    if len(raw) > 500 or "\x00" in raw:
        raise ValueError("The skill package contains an invalid file path.")
    return path.as_posix()


def _ignored_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "__MACOSX" in parts or (parts and parts[-1] == ".DS_Store")


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter.")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("SKILL.md must start with YAML frontmatter.")
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        raise ValueError("SKILL.md has incomplete YAML frontmatter.")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML object.")
    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name or len(name) > 64 or not _NAME_RE.fullmatch(name):
        raise ValueError(
            "Skill name must use 1–64 lowercase letters, numbers, and single hyphens."
        )
    if not description or len(description) > 1024:
        raise ValueError("Skill description must contain 1–1024 characters.")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise ValueError("SKILL.md must contain instructions after its frontmatter.")
    return metadata, body


def _external_name(value: str) -> str:
    """Convert a provider package name to an Agent Skills-compatible local name."""
    unscoped = str(value or "").strip().rsplit("/", 1)[-1].lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", unscoped).strip("-")
    normalized = re.sub(r"-+", "-", normalized)[:64].rstrip("-")
    if not normalized or not _NAME_RE.fullmatch(normalized):
        raise ValueError("The skill store package does not provide a usable skill name.")
    return normalized


def _normalize_external_frontmatter_name(text: str, fallback_name: str) -> str:
    """Repair only an incompatible external name while preserving its instructions."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        return text
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        return text
    if not isinstance(metadata, dict):
        return text
    current_name = str(metadata.get("name") or "").strip()
    if current_name and len(current_name) <= 64 and _NAME_RE.fullmatch(current_name):
        return text
    metadata["name"] = _external_name(fallback_name)
    header = yaml.safe_dump(
        metadata,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).strip()
    body = "\n".join(lines[end + 1 :]).strip()
    return f"---\n{header}\n---\n{body}\n"


def read_zip(raw: bytes) -> dict[str, bytes]:
    if len(raw) > MAX_PACKAGE_BYTES:
        raise ValueError("The skill package must be 10 MB or smaller.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError("Choose a valid ZIP skill package.") from exc
    files: dict[str, bytes] = {}
    total = 0
    with archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) > MAX_PACKAGE_FILES:
            raise ValueError(f"A skill package can contain at most {MAX_PACKAGE_FILES} files.")
        for entry in entries:
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError("Symbolic links are not allowed in skill packages.")
            if entry.flag_bits & 0x1:
                raise ValueError("Encrypted skill packages are not supported.")
            path = _safe_path(entry.filename)
            if _ignored_path(path):
                continue
            if path in files:
                raise ValueError("The skill package contains duplicate file paths.")
            total += entry.file_size
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("The extracted skill package must be 10 MB or smaller.")
            files[path] = archive.read(entry)
    return files


def build_package(
    uploaded: Iterable[tuple[str, bytes]],
    *,
    source_type: str = "import",
    source_name: str = "",
    source_ref: str = "",
    source_url: str = "",
    version: str = "",
    external_name: str = "",
) -> dict:
    items = list(uploaded)
    if len(items) == 1 and str(items[0][0]).lower().endswith(".zip"):
        files = read_zip(items[0][1])
    else:
        if len(items) > MAX_PACKAGE_FILES:
            raise ValueError(f"A skill package can contain at most {MAX_PACKAGE_FILES} files.")
        files = {}
        total = 0
        for path, content in items:
            safe = _safe_path(path)
            if _ignored_path(safe):
                continue
            if safe in files:
                raise ValueError("The skill package contains duplicate file paths.")
            total += len(content)
            if total > MAX_PACKAGE_BYTES:
                raise ValueError("The skill package must be 10 MB or smaller.")
            files[safe] = bytes(content)
    if not files:
        raise ValueError("The selected skill package is empty.")

    skill_paths = [path for path in files if PurePosixPath(path).name == "SKILL.md"]
    if len(skill_paths) != 1:
        raise ValueError("A skill package must contain exactly one SKILL.md file.")
    skill_path = skill_paths[0]
    root = PurePosixPath(skill_path).parent
    normalized: dict[str, bytes] = {}
    for path, content in files.items():
        candidate = PurePosixPath(path)
        if root != PurePosixPath("."):
            try:
                candidate = candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError("Every package file must be inside the skill folder.") from exc
        normalized[candidate.as_posix()] = content
    skill_bytes = normalized["SKILL.md"]
    if len(skill_bytes) > MAX_SKILL_MD_BYTES:
        raise ValueError("SKILL.md must be 2 MB or smaller.")
    try:
        skill_md = skill_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md must use UTF-8 text.") from exc
    if external_name:
        skill_md = _normalize_external_frontmatter_name(skill_md, external_name)
        normalized["SKILL.md"] = skill_md.encode("utf-8")
    metadata, _body = _frontmatter(skill_md)

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(normalized):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, normalized[path])
    package_blob = output.getvalue()
    return {
        "name": str(metadata["name"]).strip(),
        "description": str(metadata["description"]).strip(),
        "skill_md": skill_md,
        "package_blob": package_blob,
        "files": [
            {"path": path, "size": len(normalized[path])} for path in sorted(normalized)
        ],
        "metadata": metadata,
        "source_type": source_type,
        "source_name": source_name,
        "source_ref": source_ref,
        "source_url": source_url,
        "version": version or str(metadata.get("version") or ""),
        "content_hash": hashlib.sha256(package_blob).hexdigest(),
    }


def catalog_prompt(skills: list[dict]) -> str:
    if not skills:
        return ""
    lines = [
        "AVAILABLE SKILLS",
        "Skills provide task instructions, not additional tools or permissions. "
        "When a task matches a skill, call activate_skill before doing the work.",
    ]
    for skill in skills:
        lines.append(f'- {skill["name"]}: {skill["description"]}')
    return "\n".join(lines)


def runtime_access(agent_type: str, agent_key: str) -> tuple[str, StructuredTool | None]:
    from . import db

    try:
        skills = db.list_agent_skills(agent_type, str(agent_key))
    except sqlite3.OperationalError as exc:
        # A reduced legacy/test schema may predate the optional skills tables.
        if "no such table" not in str(exc).lower():
            raise
        skills = []
    prompt = catalog_prompt(skills)
    if not skills:
        return prompt, None
    by_name = {str(skill["name"]): skill for skill in skills}
    names = sorted(by_name)
    activated: set[str] = set()

    def activate_skill(name: str) -> str:
        skill = by_name.get(str(name or "").strip())
        if skill is None:
            return "Unknown skill. Choose one of: " + ", ".join(names)
        if skill["name"] in activated:
            return f'Skill "{skill["name"]}" is already active for this task.'
        activated.add(skill["name"])
        _metadata, body = _frontmatter(skill["skill_md"])
        notice = ""
        if skill.get("has_supporting_files"):
            notice = (
                "\n\nCompatibility note: this package contains supporting files. "
                "This version of Mounir can read only SKILL.md and cannot read or execute them."
            )
        return f'<skill name="{skill["name"]}">\n{body}\n</skill>{notice}'

    tool = StructuredTool.from_function(
        func=activate_skill,
        name="activate_skill",
        description=(
            "Load the complete instructions for one assigned skill before using it. "
            "Valid skill names: " + ", ".join(names)
        ),
        args_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": names,
                    "description": "The exact assigned skill name.",
                }
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    )
    return prompt, tool


def paths_from_json(raw: str, filenames: list[str]) -> list[str]:
    if not raw:
        return filenames
    try:
        paths = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("The uploaded skill paths are invalid.") from exc
    if not isinstance(paths, list) or len(paths) != len(filenames):
        raise ValueError("The uploaded skill paths do not match the selected files.")
    return [str(path) for path in paths]
