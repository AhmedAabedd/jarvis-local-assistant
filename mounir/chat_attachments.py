"""Persistent image attachments for multimodal chat conversations."""

from __future__ import annotations

import base64
import io
import re
import uuid
import warnings
from pathlib import Path

from . import config


_FORMAT_DETAILS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _directory() -> Path:
    directory = config.CHAT_ATTACHMENT_DIR
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return directory.resolve()


def _safe_stem(filename: str) -> str:
    candidate = str(filename or "image").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = re.sub(r"[^\w .-]", "_", candidate, flags=re.UNICODE).strip(" .")
    return (Path(candidate).stem.strip(" .") or "image")[:120]


def _validated_format(data: bytes) -> tuple[str, str]:
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                details = _FORMAT_DETAILS.get(str(image.format or "").upper())
    except Exception as exc:
        raise ValueError("The uploaded file is not a readable image.") from exc
    if details is None:
        raise ValueError("Upload a JPEG, PNG, or WebP image.")
    return details


def save(data: bytes, filename: str = "image") -> dict:
    """Validate and privately store one uploaded chat image."""
    if not data:
        raise ValueError("Choose an image to upload.")
    if len(data) > config.CHAT_ATTACHMENT_MAX_BYTES:
        limit = config.CHAT_ATTACHMENT_MAX_BYTES / (1024 * 1024)
        raise ValueError(f"The image is too large. The limit is {limit:g} MiB.")
    mime_type, extension = _validated_format(data)
    attachment_id = uuid.uuid4().hex
    display_name = f"{_safe_stem(filename)}{extension}"
    path = _directory() / f"{attachment_id}--{display_name}"
    with path.open("xb") as handle:
        handle.write(data)
    path.chmod(0o600)
    return {
        "id": attachment_id,
        "filename": display_name,
        "mime_type": mime_type,
    }


def resolve(attachment_id: str) -> dict:
    """Resolve one opaque attachment ID without accepting arbitrary paths."""
    normalized = str(attachment_id or "").strip().lower()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid chat attachment.")
    matches = list(_directory().glob(f"{normalized}--*"))
    if len(matches) != 1 or not matches[0].is_file():
        raise ValueError("That chat attachment is no longer available.")
    path = matches[0].resolve()
    if path.parent != _directory():
        raise ValueError("Invalid chat attachment location.")
    mime_type, _extension = _validated_format(path.read_bytes())
    return {
        "id": normalized,
        "filename": path.name.split("--", 1)[1],
        "mime_type": mime_type,
        "path": str(path),
    }


def public(record: dict) -> dict:
    """Return attachment metadata safe to expose in the browser."""
    return {
        "id": record["id"],
        "filename": record["filename"],
        "mime_type": record["mime_type"],
        "url": f"/api/chat/attachments/{record['id']}",
    }


def reference(record: dict) -> dict:
    """Return the durable metadata stored with a conversation message."""
    resolved = resolve(str(record.get("id") or ""))
    return {
        "id": resolved["id"],
        "filename": resolved["filename"],
        "mime_type": resolved["mime_type"],
    }


def image_part(record: dict) -> dict:
    """Hydrate one stored reference into an OpenAI-compatible image block."""
    resolved = resolve(str(record.get("id") or ""))
    encoded = base64.b64encode(Path(resolved["path"]).read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{resolved['mime_type']};base64,{encoded}"},
    }
