"""Official WhatsApp Business Cloud API transport for Mounir.

The FastAPI server owns this bridge. Incoming messages arrive through Meta's
signed webhook and replies are sent through the Graph API. WhatsApp keeps its
own Agent instance and conversation history.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone

import requests

from . import db, tools, trace
from .agent import Agent

MAX_MESSAGE_CHARS = 4096
CONFIRM_TIMEOUT_SECONDS = 120
SERVICE_WINDOW_SECONDS = 24 * 60 * 60


class WhatsAppBridge:
    """Receive signed Meta webhooks and send Cloud API messages."""

    def __init__(
        self,
        *,
        agent: Agent | None = None,
        turn_lock: threading.Lock | None = None,
        confirm_timeout: float = CONFIRM_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
        on_paired: Callable[[str, str], None] | None = None,
        on_inbound: Callable[[str, str], None] | None = None,
    ) -> None:
        self.agent = agent or Agent()
        self.turn_lock = turn_lock or threading.Lock()
        self.confirm_timeout = confirm_timeout
        self._session = session or requests.Session()
        self._on_paired = on_paired
        self._on_inbound = on_inbound
        self._pair_lock = threading.Lock()
        self._pair_code = ""
        self._pair_expires_at = 0.0
        self._send_lock = threading.Lock()
        self._confirm_lock = threading.Lock()
        self._confirm_event: threading.Event | None = None
        self._confirm_answer = False
        self._seen_lock = threading.Lock()
        self._seen_ids: set[str] = set()
        self._seen_order: deque[str] = deque()
        self.reconfigure(db.get_whatsapp_settings(include_secret=True))

    def reconfigure(self, settings: dict) -> None:
        self.enabled = bool(settings.get("enabled"))
        self.access_token = str(settings.get("access_token") or "").strip()
        self.phone_number_id = str(settings.get("phone_number_id") or "").strip()
        self.business_account_id = str(settings.get("business_account_id") or "").strip()
        self.app_secret = str(settings.get("app_secret") or "").strip()
        self.verify_token = str(settings.get("verify_token") or "").strip()
        self.api_version = str(settings.get("api_version") or "v25.0").strip()
        self.paired_phone = str(settings.get("paired_phone") or "").strip()
        self.heartbeat_template_name = str(
            settings.get("heartbeat_template_name") or ""
        ).strip()
        self.heartbeat_template_language = str(
            settings.get("heartbeat_template_language") or "en_US"
        ).strip()
        if not self.enabled:
            self.cancel_pairing()

    @property
    def configured(self) -> bool:
        return bool(
            self.access_token
            and self.phone_number_id
            and self.business_account_id
            and self.app_secret
            and self.verify_token
        )

    @property
    def webhook_url_path(self) -> str:
        return "/api/whatsapp/webhook"

    def configuration_error(self) -> str:
        if not self.access_token:
            return "WhatsApp access token is not configured"
        if not self.phone_number_id:
            return "WhatsApp phone number ID is not configured"
        if not self.business_account_id:
            return "WhatsApp Business Account ID is not configured"
        if not self.app_secret:
            return "Meta app secret is not configured"
        if not self.verify_token:
            return "WhatsApp webhook verification token is not configured"
        return ""

    def _graph_url(self, object_id: str, suffix: str = "") -> str:
        version = self.api_version if self.api_version.startswith("v") else f"v{self.api_version}"
        return f"https://graph.facebook.com/{version}/{object_id}{suffix}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _response_error(response: requests.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(error, dict):
                return str(error.get("message") or error.get("error_user_msg") or error)
            return str(payload)
        except Exception:
            return response.text.strip() or f"HTTP {response.status_code}"

    def test_connection(self) -> dict:
        error = self.configuration_error()
        if error:
            raise ValueError(error)
        response = self._session.get(
            self._graph_url(self.phone_number_id),
            headers=self._headers(),
            params={"fields": "display_phone_number,verified_name,quality_rating"},
            timeout=15,
        )
        if not response.ok:
            raise RuntimeError(self._response_error(response))
        identity = response.json()
        subscription = self._session.post(
            self._graph_url(self.business_account_id, "/subscribed_apps"),
            headers=self._headers(),
            timeout=15,
        )
        if not subscription.ok:
            raise RuntimeError(
                "The phone number is valid, but Mounir could not subscribe the Meta app: "
                + self._response_error(subscription)
            )
        return {
            "display_phone_number": str(identity.get("display_phone_number") or ""),
            "verified_name": str(identity.get("verified_name") or ""),
            "quality_rating": str(identity.get("quality_rating") or ""),
            "subscribed": bool(subscription.json().get("success", True)),
        }

    def verify_webhook(self, mode: str, token: str, challenge: str) -> str | None:
        if (
            self.enabled
            and mode == "subscribe"
            and self.verify_token
            and hmac.compare_digest(str(token or ""), self.verify_token)
        ):
            return str(challenge or "")
        return None

    def verify_signature(self, body: bytes, signature_header: str) -> bool:
        if not self.enabled or not self.app_secret:
            return False
        prefix, separator, supplied = str(signature_header or "").partition("=")
        if separator != "=" or prefix.lower() != "sha256" or not supplied:
            return False
        expected = hmac.new(
            self.app_secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(supplied.lower(), expected.lower())

    def create_pairing_code(self, lifetime_seconds: int = 600) -> dict:
        if not self.enabled or not self.configured:
            raise ValueError("enable and configure WhatsApp before pairing")
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = time.time() + max(60, min(int(lifetime_seconds), 1800))
        with self._pair_lock:
            self._pair_code = code
            self._pair_expires_at = expires_at
        return {
            "code": code,
            "command": f"/pair {code}",
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
        }

    def cancel_pairing(self) -> None:
        with self._pair_lock:
            self._pair_code = ""
            self._pair_expires_at = 0.0

    @staticmethod
    def split_message(text: str) -> list[str]:
        chunks: list[str] = []
        text = str(text or "")
        while len(text) > MAX_MESSAGE_CHARS:
            cut = text.rfind("\n", 0, MAX_MESSAGE_CHARS)
            if cut < MAX_MESSAGE_CHARS // 2:
                cut = MAX_MESSAGE_CHARS
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        if text:
            chunks.append(text)
        return chunks

    def _post_message(self, payload: dict) -> dict:
        error = self.configuration_error()
        if error:
            raise ValueError(error)
        with self._send_lock:
            response = self._session.post(
                self._graph_url(self.phone_number_id, "/messages"),
                headers=self._headers(),
                json=payload,
                timeout=20,
            )
        if not response.ok:
            raise RuntimeError(self._response_error(response))
        return response.json()

    def send_text(self, phone: str, text: str) -> bool:
        target = "".join(character for character in str(phone or "") if character.isdigit())
        message = str(text or "").strip()
        if not target or not message:
            return False
        for chunk in self.split_message(message):
            self._post_message(
                {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": target,
                    "type": "text",
                    "text": {"preview_url": False, "body": chunk},
                }
            )
        return True

    @staticmethod
    def _inside_service_window(last_inbound_at: str | None) -> bool:
        if not last_inbound_at:
            return False
        try:
            received = datetime.fromisoformat(last_inbound_at)
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - received).total_seconds() < SERVICE_WINDOW_SECONDS
        except (TypeError, ValueError):
            return False

    def send_notification(self, text: str) -> bool:
        settings = db.get_whatsapp_settings(include_secret=True)
        target = settings.get("paired_phone") or self.paired_phone
        if not target or not str(text or "").strip():
            return False
        if self._inside_service_window(settings.get("last_inbound_at")):
            return self.send_text(target, text)
        template_name = str(settings.get("heartbeat_template_name") or "").strip()
        if not template_name:
            raise RuntimeError(
                "WhatsApp requires an approved Heartbeat template outside the 24-hour reply window."
            )
        self._post_message(
            {
                "messaging_product": "whatsapp",
                "to": target,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": str(
                            settings.get("heartbeat_template_language") or "en_US"
                        )
                    },
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": str(text).strip()}],
                        }
                    ],
                },
            }
        )
        return True

    def _remember_message(self, message_id: str) -> bool:
        if not message_id:
            return True
        with self._seen_lock:
            if message_id in self._seen_ids:
                return False
            self._seen_ids.add(message_id)
            self._seen_order.append(message_id)
            while len(self._seen_order) > 500:
                oldest = self._seen_order.popleft()
                self._seen_ids.discard(oldest)
        return True

    @staticmethod
    def _incoming_messages(payload: dict) -> list[tuple[str, str, str, str]]:
        incoming: list[tuple[str, str, str, str]] = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                contacts = value.get("contacts") or []
                names = {
                    str(contact.get("wa_id") or ""): str(
                        (contact.get("profile") or {}).get("name") or ""
                    )
                    for contact in contacts
                }
                for message in value.get("messages") or []:
                    phone = str(message.get("from") or "")
                    message_id = str(message.get("id") or "")
                    if message.get("type") == "text":
                        text = str((message.get("text") or {}).get("body") or "").strip()
                    else:
                        text = ""
                    incoming.append((message_id, phone, names.get(phone, ""), text))
        return incoming

    def handle_webhook(self, payload: dict) -> None:
        if not self.enabled:
            return
        for message_id, phone, name, text in self._incoming_messages(payload):
            if not self._remember_message(message_id):
                continue
            if not text:
                if phone == self.paired_phone:
                    self.send_text(phone, "I can only read text messages here for now.")
                continue
            try:
                self._handle_text(phone, name, text)
            except Exception as exc:
                trace.kv("whatsapp", f"message failed: {exc}")
                try:
                    self.send_text(phone, f"[error] {exc}")
                except Exception:
                    pass

    def _whatsapp_confirm(self, action: str) -> bool:
        target = self.paired_phone
        if not target:
            return False
        event = threading.Event()
        with self._confirm_lock:
            self._confirm_event = event
            self._confirm_answer = False
        self.send_text(target, f'⚠ Needs your approval:\n{action}\n\nReply "yes" to approve.')
        answered = event.wait(self.confirm_timeout)
        with self._confirm_lock:
            answer = self._confirm_answer
            if self._confirm_event is event:
                self._confirm_event = None
        if not answered:
            self.send_text(target, "No answer received — action cancelled.")
            return False
        return answer

    def _handle_text(self, phone: str, name: str, text: str) -> None:
        normalized_phone = "".join(character for character in phone if character.isdigit())
        if text.lower().startswith("/pair"):
            supplied = text.partition(" ")[2].strip()
            with self._pair_lock:
                valid = bool(
                    self._pair_code
                    and time.time() <= self._pair_expires_at
                    and hmac.compare_digest(supplied, self._pair_code)
                )
                if valid:
                    self._pair_code = ""
                    self._pair_expires_at = 0.0
            if not valid:
                self.send_text(
                    normalized_phone,
                    "That pairing code is invalid or expired. Generate a new one in Agent Studio.",
                )
                return
            self.paired_phone = normalized_phone
            if self._on_paired is not None:
                self._on_paired(normalized_phone, name)
            self.send_text(normalized_phone, "WhatsApp is now connected to Mounir.")
            return

        if not self.paired_phone:
            self.send_text(
                normalized_phone,
                "This number is not paired yet. Generate a pairing code in Agent Studio.",
            )
            return
        if normalized_phone != self.paired_phone:
            self.send_text(normalized_phone, "Sorry, this is a private assistant.")
            return
        if self._on_inbound is not None:
            self._on_inbound(normalized_phone, name)

        with self._confirm_lock:
            pending = self._confirm_event
            if pending is not None:
                self._confirm_answer = text.lower() in ("y", "yes")
                pending.set()
                return

        if text == "/start":
            self.send_text(normalized_phone, f"{db.get_profile()['assistant_name']} here. Say the word.")
            return
        if text == "/reset":
            with self.turn_lock:
                self.agent.reset()
            self.send_text(normalized_phone, "Conversation cleared.")
            return

        with self.turn_lock:
            trace.node("whatsapp")
            trace.event(f"← {text[:120]}")
            with tools.use_confirmation_handler(self._whatsapp_confirm):
                reply = "".join(self.agent.respond(text)).strip()
        self.send_text(normalized_phone, reply or "(no reply)")
        trace.event(f"→ {len(reply)} chars")
