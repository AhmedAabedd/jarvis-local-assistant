from __future__ import annotations

import hashlib
import hmac
import tempfile
import threading
import unittest
from pathlib import Path

from mounir import db
from mounir.memory import Conversation
from mounir.whatsapp_bridge import WhatsAppBridge


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.text = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict:
        return self.payload


class FakeSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(
            {
                "id": "phone-id",
                "display_phone_number": "+216 00 000 000",
                "verified_name": "Mounir",
                "quality_rating": "GREEN",
            }
        )

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if url.endswith("/subscribed_apps"):
            return FakeResponse({"success": True})
        return FakeResponse({"messages": [{"id": "outbound-id"}]})


class FakeAgent:
    def __init__(self):
        self.conversation = Conversation()
        self.inputs = []

    def respond(self, text, **_kwargs):
        self.inputs.append(text)
        self.conversation.add_user(text)
        self.conversation.add_assistant(f"Reply to {text}")
        yield f"Reply to {text}"


class WhatsAppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temp_dir.cleanup()

    def _configured_settings(self, *, enabled: bool = True) -> dict:
        db.update_whatsapp_settings(
            access_token="secret-access-token",
            phone_number_id="phone-id",
            business_account_id="business-id",
            app_secret="app-secret",
            api_version="v25.0",
            enabled=enabled,
        )
        return db.get_whatsapp_settings(include_secret=True)

    def test_settings_hide_secrets_and_heartbeat_destinations_persist(self):
        private = self._configured_settings()
        public = db.get_whatsapp_settings()

        self.assertEqual(private["access_token"], "secret-access-token")
        self.assertNotIn("access_token", public)
        self.assertNotIn("app_secret", public)
        self.assertNotIn("paired_phone", public)
        self.assertTrue(public["credentials_configured"])
        self.assertTrue(public["verify_token"])

        heartbeat = db.update_heartbeat_settings(
            notify_telegram=False,
            notify_whatsapp=True,
        )
        self.assertFalse(heartbeat["notify_telegram"])
        self.assertTrue(heartbeat["notify_whatsapp"])

    def test_cloud_api_connection_and_signature_validation(self):
        settings = self._configured_settings()
        session = FakeSession()
        bridge = WhatsAppBridge(session=session)
        bridge.reconfigure(settings)

        identity = bridge.test_connection()
        self.assertEqual(identity["verified_name"], "Mounir")
        self.assertTrue(session.get_calls[0][0].endswith("/v25.0/phone-id"))
        self.assertTrue(session.post_calls[0][0].endswith("/business-id/subscribed_apps"))

        body = b'{"object":"whatsapp_business_account"}'
        signature = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
        self.assertTrue(bridge.verify_signature(body, f"sha256={signature}"))
        self.assertFalse(bridge.verify_signature(body + b"x", f"sha256={signature}"))

    def test_pairing_and_messages_stay_in_whatsapp_agent(self):
        settings = self._configured_settings()
        session = FakeSession()
        agent = FakeAgent()

        def paired(phone, name):
            db.pair_whatsapp_phone(phone, name)

        bridge = WhatsAppBridge(
            agent=agent,
            turn_lock=threading.Lock(),
            session=session,
            on_paired=paired,
            on_inbound=db.mark_whatsapp_inbound,
        )
        bridge.reconfigure(settings)
        pairing = bridge.create_pairing_code()
        bridge.handle_webhook(
            {
                "entry": [{
                    "changes": [{
                        "value": {
                            "contacts": [{"wa_id": "21611111111", "profile": {"name": "Ada"}}],
                            "messages": [{
                                "id": "pair-message",
                                "from": "21611111111",
                                "type": "text",
                                "text": {"body": pairing["command"]},
                            }],
                        }
                    }]
                }]
            }
        )
        self.assertTrue(db.get_whatsapp_settings()["paired"])

        bridge.handle_webhook(
            {
                "entry": [{
                    "changes": [{
                        "value": {
                            "contacts": [{"wa_id": "21611111111", "profile": {"name": "Ada"}}],
                            "messages": [{
                                "id": "request-message",
                                "from": "21611111111",
                                "type": "text",
                                "text": {"body": "Check my tasks"},
                            }],
                        }
                    }]
                }]
            }
        )
        self.assertEqual(agent.inputs, ["Check my tasks"])
        self.assertEqual(
            agent.conversation.display_messages(),
            [
                {"role": "user", "content": "Check my tasks"},
                {"role": "assistant", "content": "Reply to Check my tasks"},
            ],
        )

        # Meta may redeliver a webhook. The message ID guard prevents a second turn.
        duplicate_payload = {
            "entry": [{"changes": [{"value": {"messages": [{
                "id": "request-message",
                "from": "21611111111",
                "type": "text",
                "text": {"body": "Check my tasks"},
            }]}}]}]
        }
        bridge.handle_webhook(duplicate_payload)
        self.assertEqual(agent.inputs, ["Check my tasks"])

if __name__ == "__main__":
    unittest.main()
