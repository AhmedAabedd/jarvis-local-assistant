"""Official WhatsApp Business agent adapter.

This module is intentionally independent from ``whatsapp_bridge``. The bridge
is a private chat channel for one paired controller; this adapter owns business
connections, inbox persistence, and agent-requested outbound operations.
"""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import db


class WhatsAppBusinessError(RuntimeError):
    """A sanitized failure returned by the official Cloud API."""


_DEFINITION = {
    "id": "whatsapp",
    "label": "WhatsApp",
    "description": "Serve WhatsApp Business inbox conversations through the official Cloud API.",
    "account_kind": "WhatsApp Business phone number",
    "default_api_version": "v26.0",
    "permissions": [
        {
            "id": "whatsapp_business_messaging",
            "label": "Business messaging",
            "description": "Send and receive customer messages, media, replies, and templates.",
            "required": True,
        },
        {
            "id": "whatsapp_business_management",
            "label": "Business account management",
            "description": "Access the WABA, sender number, templates, profile, and app subscription.",
            "required": True,
        },
        {
            "id": "business_management",
            "label": "Business portfolio management",
            "description": "Needed only for portfolio-level or Embedded Signup workflows; Mounir does not request it here.",
            "required": False,
        },
    ],
    "capabilities": [
        {
            "id": "conversations",
            "label": "Business conversations",
            "description": "Receive signed webhooks, list known contacts, and read persisted messages.",
            "permissions": ["whatsapp_business_messaging"],
            "required": True,
        },
        {
            "id": "send_messages",
            "label": "Send and reply",
            "description": "Send text or reply to a stored inbound message during its open 24-hour window.",
            "permissions": ["whatsapp_business_messaging"],
        },
        {
            "id": "send_attachments",
            "label": "Send attachments",
            "description": "Send supported public-URL or local-file media during an open service window.",
            "permissions": ["whatsapp_business_messaging"],
        },
        {
            "id": "mark_read",
            "label": "Mark messages as read",
            "description": "Update the official read status for a selected inbound message.",
            "permissions": ["whatsapp_business_messaging"],
            "available": False,
        },
        {
            "id": "template_messages",
            "label": "Approved template messages",
            "description": "Initiate or reopen an opted-in conversation with an approved template.",
            "permissions": ["whatsapp_business_messaging"],
            "available": False,
        },
        {
            "id": "manage_templates",
            "label": "Manage message templates",
            "description": "Discover, create, and manage templates owned by the selected WABA.",
            "permissions": ["whatsapp_business_management"],
            "available": False,
        },
        {
            "id": "business_profile",
            "label": "Manage business profile",
            "description": "Read or update the official profile for the business sender number.",
            "permissions": ["whatsapp_business_management"],
            "available": False,
        },
        {
            "id": "interactive_messages",
            "label": "Flows and interactive messages",
            "description": "Send supported buttons, lists, products, and WhatsApp Flows.",
            "permissions": ["whatsapp_business_messaging"],
            "available": False,
        },
    ],
    "excluded": ["personal WhatsApp accounts", "cold automated DMs", "arbitrary recipients"],
}


def platform_definition() -> dict:
    return deepcopy(_DEFINITION)


def validate_capabilities(values) -> list[str]:
    available = {
        item["id"]
        for item in _DEFINITION["capabilities"]
        if item.get("available", True)
    }
    required = {
        item["id"]
        for item in _DEFINITION["capabilities"]
        if item.get("required") and item.get("available", True)
    }
    requested = list(
        dict.fromkeys(
            str(value).strip() for value in (values or []) if str(value).strip()
        )
    )
    unknown = [value for value in requested if value not in available]
    if unknown:
        raise ValueError(f"Unsupported WhatsApp capability: {unknown[0]}")
    return sorted(set(requested) | required)


def _error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return str(error.get("error_user_msg") or error.get("message") or error)[:600]
        return str(payload)[:600]
    except Exception:
        return (response.text.strip() or f"HTTP {response.status_code}")[:600]


def _request_json(
    connection: dict,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    data: dict | None = None,
    files: dict | None = None,
    authorization_token: str = "",
    client: httpx.Client | None = None,
) -> dict:
    owned = client is None
    client = client or httpx.Client(timeout=30.0)
    url = f"https://graph.facebook.com/{connection['api_version']}/{path.lstrip('/')}"
    try:
        response = client.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {authorization_token or connection['access_token']}"
            },
            params=params,
            json=json_body,
            data=data,
            files=files,
        )
    except httpx.HTTPError as exc:
        raise WhatsAppBusinessError(f"Could not reach Meta: {exc}") from exc
    finally:
        if owned:
            client.close()
    if not response.is_success:
        raise WhatsAppBusinessError(_error_message(response))
    try:
        payload = response.json()
    except ValueError as exc:
        raise WhatsAppBusinessError("Meta returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise WhatsAppBusinessError("Meta returned an unexpected response")
    return payload


def _runtime_connection(connection_id: int, *, require_webhook: bool = False) -> dict:
    connection = db.get_meta_whatsapp_connection_runtime(connection_id)
    if connection is None:
        raise ValueError("WhatsApp Business connection does not exist")
    if not connection["enabled"] or not connection["credentials_configured"]:
        raise ValueError("WhatsApp Business connection is not enabled and configured")
    if require_webhook and not connection["webhook_verified"]:
        raise ValueError("WhatsApp Business webhook is not verified")
    if require_webhook:
        required = {
            item["id"] for item in _DEFINITION["permissions"] if item.get("required")
        }
        if not connection.get("permissions_checked_at"):
            raise ValueError("Test this WhatsApp connection to verify its token permissions first")
        missing = sorted(required - set(connection.get("granted_permissions") or []))
        if missing:
            raise ValueError(
                f"The WhatsApp access token is missing required permission: {missing[0]}"
            )
    return connection


def _require_capability(connection: dict, capability: str) -> None:
    if capability in set(connection.get("requested_capabilities") or []):
        return
    item = next(
        (entry for entry in _DEFINITION["capabilities"] if entry["id"] == capability),
        {"label": capability},
    )
    raise ValueError(
        f"Enable the {item['label']} capability on this WhatsApp connection first"
    )


def list_connections() -> list[dict]:
    """Return safe metadata for configured WhatsApp Business senders."""

    return [item for item in db.list_meta_whatsapp_connections() if item["enabled"]]


def test_connection(connection_id: int, *, client: httpx.Client | None = None) -> dict:
    connection = _runtime_connection(connection_id)
    payload = _request_json(
        connection,
        "GET",
        connection["phone_number_id"],
        params={"fields": "id,display_phone_number,verified_name"},
        client=client,
    )
    token_data = _request_json(
        connection,
        "GET",
        "debug_token",
        params={"input_token": connection["access_token"]},
        authorization_token=f"{connection['app_id']}|{connection['app_secret']}",
        client=client,
    ).get("data") or {}
    if token_data.get("is_valid") is False:
        raise WhatsAppBusinessError("The WhatsApp access token is invalid")
    granted = {
        str(scope) for scope in token_data.get("scopes") or [] if str(scope)
    }
    granted.update(
        str(item.get("scope"))
        for item in token_data.get("granular_scopes") or []
        if item.get("scope")
    )
    required = {
        item["id"] for item in _DEFINITION["permissions"] if item.get("required")
    }
    missing = sorted(required - granted)
    if missing:
        raise WhatsAppBusinessError(
            f"The access token is missing required permission: {missing[0]}"
        )
    return {
        "id": str(payload.get("id") or connection["phone_number_id"]),
        "display_phone_number": str(payload.get("display_phone_number") or ""),
        "verified_name": str(payload.get("verified_name") or ""),
        "granted_permissions": sorted(granted),
    }


def verify_signature(connection: dict, body: bytes, signature: str) -> bool:
    secret = str(connection.get("app_secret") or "").encode()
    if not secret or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def verify_webhook(connection: dict, mode: str, token: str, challenge: str) -> str | None:
    if mode != "subscribe" or not challenge:
        return None
    expected = str(connection.get("verify_token") or "")
    if not expected or not hmac.compare_digest(str(token or ""), expected):
        return None
    return str(challenge)


def _event_time(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).isoformat()


def handle_webhook(connection_id: int, payload: dict) -> int:
    """Persist inbound messages and delivery updates from a signed webhook."""

    saved = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            contacts = {
                str(item.get("wa_id") or ""): str(
                    (item.get("profile") or {}).get("name") or ""
                )
                for item in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                phone = str(message.get("from") or "")
                message_type = str(message.get("type") or "unknown")
                content = message.get(message_type) or {}
                body = (
                    str(content.get("body") or "")
                    if message_type == "text"
                    else str(content.get("caption") or content.get("filename") or "")
                )
                context = message.get("context") or {}
                db.record_meta_whatsapp_message(
                    connection_id=connection_id,
                    message_id=str(message.get("id") or ""),
                    contact_phone=phone,
                    contact_name=contacts.get(phone, ""),
                    direction="inbound",
                    message_type=message_type,
                    body=body,
                    media_id=str(content.get("id") or ""),
                    mime_type=str(content.get("mime_type") or ""),
                    reply_to_message_id=str(context.get("id") or ""),
                    occurred_at=_event_time(message.get("timestamp")),
                )
                saved += 1
            for status in value.get("statuses") or []:
                db.update_meta_whatsapp_message_status(
                    connection_id,
                    str(status.get("id") or ""),
                    str(status.get("status") or ""),
                )
    return saved


def list_conversations(connection_id: int) -> list[dict]:
    connection = _runtime_connection(connection_id, require_webhook=True)
    _require_capability(connection, "conversations")
    return db.list_meta_whatsapp_conversations(connection_id)


def read_messages(
    connection_id: int, contact_phone: str, limit: int = 50
) -> list[dict]:
    connection = _runtime_connection(connection_id, require_webhook=True)
    _require_capability(connection, "conversations")
    conversation = db.get_meta_whatsapp_conversation(connection_id, contact_phone)
    if conversation is None:
        raise ValueError("That contact is not present in this WhatsApp Business inbox")
    return db.list_meta_whatsapp_messages(
        connection_id, contact_phone=conversation["contact_phone"], limit=limit
    )


def _open_conversation(
    connection_id: int, contact_phone: str, capability: str
) -> tuple[dict, dict]:
    connection = _runtime_connection(connection_id, require_webhook=True)
    _require_capability(connection, capability)
    conversation = db.get_meta_whatsapp_conversation(connection_id, contact_phone)
    if conversation is None:
        raise ValueError("That contact is not present in this WhatsApp Business inbox")
    if not conversation["service_window_open"]:
        raise ValueError(
            "The 24-hour WhatsApp service window is closed; an approved template and recorded opt-in are required"
        )
    return connection, conversation


def _record_sent(
    connection_id: int,
    contact_phone: str,
    payload: dict,
    *,
    message_type: str,
    body: str = "",
    media_id: str = "",
    mime_type: str = "",
    reply_to_message_id: str = "",
) -> dict:
    messages = payload.get("messages") or []
    message_id = str((messages[0] if messages else {}).get("id") or "")
    if not message_id:
        raise WhatsAppBusinessError("Meta accepted the request without returning a message ID")
    db.record_meta_whatsapp_message(
        connection_id=connection_id,
        message_id=message_id,
        contact_phone=contact_phone,
        direction="outbound",
        message_type=message_type,
        body=body,
        media_id=media_id,
        mime_type=mime_type,
        status="accepted",
        reply_to_message_id=reply_to_message_id,
        occurred_at=datetime.now(timezone.utc).isoformat(),
    )
    return payload


def send_text(
    connection_id: int,
    contact_phone: str,
    message: str,
    *,
    reply_to_message_id: str = "",
    client: httpx.Client | None = None,
) -> dict:
    connection, conversation = _open_conversation(
        connection_id, contact_phone, "send_messages"
    )
    text = str(message or "").strip()
    if not text:
        raise ValueError("WhatsApp message text is required")
    if len(text) > 4096:
        raise ValueError("WhatsApp text messages cannot exceed 4096 characters")
    body: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": conversation["contact_phone"],
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    if reply_to_message_id:
        body["context"] = {"message_id": str(reply_to_message_id)}
    payload = _request_json(
        connection,
        "POST",
        f"{connection['phone_number_id']}/messages",
        json_body=body,
        client=client,
    )
    return _record_sent(
        connection_id,
        conversation["contact_phone"],
        payload,
        message_type="text",
        body=text,
        reply_to_message_id=reply_to_message_id,
    )


def reply_to_message(
    connection_id: int,
    message_id: str,
    message: str,
    *,
    client: httpx.Client | None = None,
) -> dict:
    source = db.get_meta_whatsapp_message(connection_id, message_id)
    if source is None or source["direction"] != "inbound":
        raise ValueError("Reply target must be an inbound message from this business inbox")
    return send_text(
        connection_id,
        source["contact_phone"],
        message,
        reply_to_message_id=source["message_id"],
        client=client,
    )


def _media_kind(mime_type: str) -> str:
    prefix = str(mime_type or "").split("/", 1)[0]
    return prefix if prefix in {"image", "audio", "video"} else "document"


def send_attachment(
    connection_id: int,
    contact_phone: str,
    source: str,
    caption: str = "",
    *,
    client: httpx.Client | None = None,
) -> dict:
    """Send a public URL or upload a local file inside an open service window."""

    connection, conversation = _open_conversation(
        connection_id, contact_phone, "send_attachments"
    )
    source = str(source or "").strip()
    if not source:
        raise ValueError("WhatsApp attachment source is required")
    is_url = source.startswith(("http://", "https://"))
    filename = Path(urlparse(source).path).name if is_url else Path(source).name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    kind = _media_kind(mime_type)
    media_reference: dict[str, str]
    media_id = ""
    if is_url:
        media_reference = {"link": source}
    else:
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise ValueError("WhatsApp attachment file does not exist")
        with path.open("rb") as handle:
            uploaded = _request_json(
                connection,
                "POST",
                f"{connection['phone_number_id']}/media",
                data={"messaging_product": "whatsapp", "type": mime_type},
                files={"file": (path.name, handle, mime_type)},
                client=client,
            )
        media_id = str(uploaded.get("id") or "")
        if not media_id:
            raise WhatsAppBusinessError("Meta did not return an uploaded media ID")
        media_reference = {"id": media_id}
    if kind == "document" and filename:
        media_reference["filename"] = filename
    if kind in {"image", "video", "document"} and str(caption or "").strip():
        media_reference["caption"] = str(caption).strip()
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": conversation["contact_phone"],
        "type": kind,
        kind: media_reference,
    }
    payload = _request_json(
        connection,
        "POST",
        f"{connection['phone_number_id']}/messages",
        json_body=body,
        client=client,
    )
    return _record_sent(
        connection_id,
        conversation["contact_phone"],
        payload,
        message_type=kind,
        body=str(caption or "").strip() or filename,
        media_id=media_id,
        mime_type=mime_type,
    )
