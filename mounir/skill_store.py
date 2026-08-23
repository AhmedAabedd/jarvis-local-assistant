"""Provider-neutral skill catalog API with Tank as the default adapter."""

from __future__ import annotations

import base64
import hashlib
import io
import os
import tarfile
from pathlib import PurePosixPath
from urllib.parse import quote, urljoin

import httpx

from . import agent_skills

DEFAULT_PROVIDER = "tank"
TANK_API_URL = os.environ.get(
    "MOUNIR_TANK_REGISTRY_URL", "https://tankpkg.dev/api/v1"
).rstrip("/")
TANK_SITE_URL = os.environ.get(
    "MOUNIR_TANK_SITE_URL", TANK_API_URL.removesuffix("/api/v1")
).rstrip("/")
TANK_API_TOKEN = os.environ.get("MOUNIR_TANK_API_TOKEN", "").strip()
TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def providers() -> list[dict]:
    return [{"id": "tank", "name": "Tank", "supports_install": True}]


def _adapter(provider: str) -> str:
    normalized = str(provider or DEFAULT_PROVIDER).strip().lower()
    if normalized != "tank":
        raise ValueError("This skill store provider is not supported.")
    return normalized


def _headers(*, archive: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/gzip, application/zip"
        if archive
        else "application/json",
        "User-Agent": "Mounir-Agent-Studio/1",
    }
    if TANK_API_TOKEN:
        headers["Authorization"] = f"Bearer {TANK_API_TOKEN}"
    return headers


def _error(response: httpx.Response) -> RuntimeError:
    retry = response.headers.get("Retry-After")
    if response.status_code == 429:
        suffix = f" Try again in {retry} seconds." if retry else " Try again shortly."
        return RuntimeError("The skill store rate limit was reached." + suffix)
    try:
        payload = response.json()
        detail = (
            payload.get("message")
            or payload.get("detail")
            or payload.get("title")
            or payload.get("error")
            or response.text
        )
    except (ValueError, AttributeError):
        detail = response.text
    detail = str(detail or response.reason_phrase).strip()
    return RuntimeError(f"The skill store request failed: {detail[:300]}")


def _identity(reference: str) -> str:
    identity = str(reference or "").strip()
    if not identity:
        raise ValueError("Choose a skill from the store.")
    if any(char in identity for char in ("\x00", "\r", "\n")):
        raise ValueError("The skill store reference is invalid.")
    return identity


def _publisher(raw: dict, identity: str) -> str:
    publisher = raw.get("publisher") or raw.get("owner") or ""
    if isinstance(publisher, dict):
        publisher = (
            publisher.get("name")
            or publisher.get("handle")
            or publisher.get("displayName")
            or ""
        )
    if publisher:
        return str(publisher)
    if identity.startswith("@") and "/" in identity:
        return identity[1:].split("/", 1)[0]
    return ""


def _item(raw: dict, *, provider: str = "tank") -> dict:
    identity = str(raw.get("name") or raw.get("id") or "").strip()
    slug = identity.rsplit("/", 1)[-1].removeprefix("@")
    publisher = _publisher(raw, identity)
    verdict = str(
        raw.get("scanVerdict") or raw.get("verdict") or raw.get("auditStatus") or ""
    ).strip()
    audit_score = raw.get("auditScore")
    audit_label = verdict.replace("_", " ").title()
    if audit_score is not None:
        score_label = f"Audit {audit_score}/10"
        audit_label = f"{audit_label} · {score_label}" if audit_label else score_label
    return {
        "provider": provider,
        "provider_name": "Tank",
        "slug": slug,
        "reference": identity,
        "name": str(raw.get("displayName") or slug or identity),
        "description": str(raw.get("description") or raw.get("summary") or ""),
        "version": str(raw.get("latestVersion") or raw.get("version") or ""),
        "owner": publisher,
        "downloads": int(raw.get("downloads") or raw.get("downloadCount") or 0),
        "stars": int(raw.get("stars") or raw.get("starCount") or 0),
        "installs": int(raw.get("installs") or 0),
        "versions": int(raw.get("versionCount") or 0),
        "comments": 0,
        "bookmarks": 0,
        "rolling_installs": 0,
        "topics": [str(value) for value in raw.get("topics") or []],
        "categories": [str(value) for value in raw.get("categories") or []],
        "official": bool(raw.get("official")),
        "installability": audit_label,
        "visibility": str(raw.get("visibility") or "public"),
        "created_at": raw.get("createdAt") or raw.get("publishedAt"),
        "updated_at": raw.get("updatedAt") or raw.get("publishedAt"),
        "changelog": str(raw.get("changelog") or ""),
        "license": str(raw.get("license") or ""),
        "skill_md": str(raw.get("skill_md") or ""),
        "permissions": (
            raw.get("permissions") if isinstance(raw.get("permissions"), dict) else {}
        ),
        "dependencies": (
            raw.get("dependencies") if isinstance(raw.get("dependencies"), dict) else {}
        ),
        "scan_findings": (
            raw.get("scanFindings") if isinstance(raw.get("scanFindings"), list) else []
        ),
        "source_url": str(
            raw.get("repository")
            or raw.get("url")
            or f"{TANK_SITE_URL}/skills/{quote(identity, safe='@/')}"
        ),
    }


async def browse(provider: str, query: str = "", cursor: str = "", limit: int = 24) -> dict:
    provider = _adapter(provider)
    bounded_limit = max(1, min(int(limit), 50))
    try:
        page = max(1, int(cursor or "1"))
    except ValueError:
        page = 1
    params: dict[str, str | int] = {"page": page, "limit": bounded_limit}
    if query.strip():
        params["q"] = query.strip()
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            f"{TANK_API_URL}/search", params=params, headers=_headers()
        )
    if not response.is_success:
        raise _error(response)
    payload = response.json()
    raw_items = payload.get("results") or payload.get("items") or []
    total = int(payload.get("total") or len(raw_items))
    current_page = int(payload.get("page") or page)
    page_limit = int(payload.get("limit") or bounded_limit)
    next_cursor = str(current_page + 1) if current_page * page_limit < total else ""
    return {
        "provider": provider,
        "items": [_item(raw, provider=provider) for raw in raw_items],
        "next_cursor": next_cursor,
    }


async def _metadata(client: httpx.AsyncClient, reference: str) -> dict:
    identity = _identity(reference)
    response = await client.get(
        f"{TANK_API_URL}/skills/{quote(identity, safe='')}", headers=_headers()
    )
    if not response.is_success:
        raise _error(response)
    return response.json()


async def details(
    provider: str, reference: str, _owner_handle: str = ""
) -> dict:
    provider = _adapter(provider)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        metadata = await _metadata(client, reference)
        version = str(metadata.get("latestVersion") or "")
        version_data: dict = {}
        skill_md = ""
        if version:
            safe_identity = quote(_identity(reference), safe="")
            safe_version = quote(version, safe="")
            response = await client.get(
                f"{TANK_API_URL}/skills/{safe_identity}/{safe_version}",
                headers=_headers(),
            )
            if not response.is_success:
                raise _error(response)
            version_data = response.json()
            file_response = await client.get(
                f"{TANK_API_URL}/skills/{safe_identity}/{safe_version}/files/SKILL.md",
                headers={**_headers(), "Accept": "text/markdown, text/plain"},
            )
            if not file_response.is_success:
                raise _error(file_response)
            if len(file_response.content) > agent_skills.MAX_SKILL_MD_BYTES:
                raise RuntimeError("The store skill instructions are too large to preview.")
            try:
                skill_md = file_response.content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError("The store skill instructions are not valid UTF-8 text.") from exc
        merged = {
            **metadata,
            **version_data,
            "name": metadata.get("name") or reference,
            "skill_md": skill_md,
        }
    return _item(merged, provider=provider)


def _verify_integrity(content: bytes, integrity: str) -> None:
    value = str(integrity or "").strip()
    if not value:
        return
    if value.startswith("sha512-"):
        expected = value.removeprefix("sha512-")
        actual = base64.b64encode(hashlib.sha512(content).digest()).decode()
        if actual != expected:
            raise RuntimeError("The downloaded skill failed its SHA-512 integrity check.")
        return
    if value.startswith("sha256:"):
        expected = value.removeprefix("sha256:").lower()
        if hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError("The downloaded skill failed its SHA-256 integrity check.")


def _tar_files(content: bytes) -> list[tuple[str, bytes]]:
    uploaded: list[tuple[str, bytes]] = []
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise ValueError("The skill archive contains an unsupported link or device.")
                path = PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("The skill archive contains an unsafe file path.")
                total += int(member.size)
                if total > agent_skills.MAX_PACKAGE_BYTES:
                    raise ValueError("The skill package is too large.")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError("The skill archive contains an unreadable file.")
                uploaded.append((member.name, extracted.read()))
                if len(uploaded) > agent_skills.MAX_PACKAGE_FILES:
                    raise ValueError("The skill package contains too many files.")
    except tarfile.TarError as exc:
        raise ValueError("The skill store package is not a valid archive.") from exc
    return uploaded


def _safe_remote_file(value: str) -> str:
    path = PurePosixPath(str(value or "").replace("\\", "/"))
    if not path.parts or path.is_absolute() or ".." in path.parts:
        raise ValueError("The skill contains an unsafe file path.")
    return path.as_posix()


async def download(
    provider: str,
    reference: str,
    version: str = "",
    _owner_handle: str = "",
) -> dict:
    provider = _adapter(provider)
    identity = _identity(reference)
    safe_identity = quote(identity, safe="")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        metadata = await _metadata(client, identity)
        chosen_version = str(version or metadata.get("latestVersion") or "")
        if not chosen_version:
            raise RuntimeError("The skill store did not provide a published version.")
        version_response = await client.get(
            f"{TANK_API_URL}/skills/{safe_identity}/{quote(chosen_version, safe='')}",
            headers=_headers(),
        )
        if not version_response.is_success:
            raise _error(version_response)
        version_data = version_response.json()
        download_url = str(
            version_data.get("downloadUrl")
            or version_data.get("download_url")
            or version_data.get("tarballUrl")
            or ""
        )
        if download_url:
            archive_response = await client.get(
                urljoin(f"{TANK_API_URL}/", download_url), headers=_headers(archive=True)
            )
            if not archive_response.is_success:
                raise _error(archive_response)
            content = archive_response.content
            _verify_integrity(content, str(version_data.get("integrity") or ""))
            if content.startswith(b"PK"):
                uploaded = [(f"{identity.rsplit('/', 1)[-1]}.zip", content)]
            else:
                uploaded = _tar_files(content)
        else:
            listed = version_data.get("files") or ["SKILL.md"]
            paths = [
                _safe_remote_file(item.get("path") if isinstance(item, dict) else item)
                for item in listed
            ]
            if "SKILL.md" not in paths:
                paths.insert(0, "SKILL.md")
            uploaded = []
            for path in dict.fromkeys(paths):
                file_response = await client.get(
                    f"{TANK_API_URL}/skills/{safe_identity}/"
                    f"{quote(chosen_version, safe='')}/files/{quote(path, safe='/')}",
                    headers={**_headers(), "Accept": "*/*"},
                )
                if not file_response.is_success:
                    if path != "SKILL.md" and file_response.status_code == 404:
                        continue
                    raise _error(file_response)
                uploaded.append((path, file_response.content))
    merged = {**metadata, **version_data}
    item = _item(merged, provider=provider)
    return agent_skills.build_package(
        uploaded,
        source_type=provider,
        source_name="Tank",
        source_ref=identity,
        source_url=item.get("source_url") or "",
        version=chosen_version,
        external_name=identity,
    )
