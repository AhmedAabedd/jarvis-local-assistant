from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import httpx

from mounir import builtin_agents, db, langgraph_agent, meta_social, tools, whatsapp_business


class MetaSocialTests(unittest.TestCase):
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

    def _connection(self, platform: str = "facebook", **overrides) -> dict:
        defaults = {
            "platform": platform,
            "name": f"{platform.title()} app",
            "auth_strategy": {
                "facebook": "facebook_login",
                "messenger": "facebook_login",
                "instagram": "instagram_login",
                "threads": "threads_oauth",
            }[platform],
            "app_id": "app-123",
            "app_secret": "secret-456",
            "api_version": "v1.0" if platform == "threads" else "v26.0",
            "requested_capabilities": [],
        }
        defaults.update(overrides)
        return db.create_meta_connection(**defaults)

    def test_connection_secrets_are_masked_and_accounts_are_multi_account(self):
        connection = self._connection(
            requested_capabilities=["read_content", "ads_read"]
        )
        public = db.get_meta_connection(connection["id"])
        private = db.get_meta_connection(connection["id"], include_secrets=True)

        self.assertNotIn("app_secret", public)
        self.assertNotIn("access_token", public)
        self.assertTrue(public["app_secret_configured"])
        self.assertEqual(private["app_secret"], "secret-456")

        accounts = db.replace_meta_accounts(
            connection["id"],
            [
                {
                    "external_id": "page-1",
                    "name": "Main Page",
                    "account_type": "facebook_page",
                    "access_token": "page-token",
                },
                {
                    "external_id": "act_9",
                    "name": "Ads",
                    "account_type": "meta_ad_account",
                    "metadata": {"currency": "USD"},
                },
            ],
        )
        self.assertEqual(len(accounts), 2)
        page = next(item for item in accounts if item["external_id"] == "page-1")
        self.assertNotIn("access_token", page)
        self.assertTrue(page["token_configured"])

        db.update_meta_account(page["id"], enabled=False)
        refreshed = db.replace_meta_accounts(
            connection["id"],
            [{"external_id": "page-1", "name": "Renamed", "account_type": "facebook_page"}],
        )
        self.assertFalse(refreshed[0]["enabled"])

    def test_oauth_state_is_hashed_expiring_and_one_use(self):
        connection = self._connection("threads")
        db.begin_meta_oauth(connection["id"], "private-state", "https://app/callback", time.time() + 60)

        with db._connect() as conn:
            stored = conn.execute("SELECT state_hash FROM meta_oauth_states").fetchone()[0]
        self.assertNotEqual(stored, "private-state")
        self.assertIsNone(db.consume_meta_oauth_state(connection["id"], "wrong", time.time()))
        self.assertEqual(
            db.consume_meta_oauth_state(connection["id"], "private-state", time.time()),
            "https://app/callback",
        )
        self.assertIsNone(db.consume_meta_oauth_state(connection["id"], "private-state", time.time()))

        db.begin_meta_oauth(connection["id"], "second", "https://app/callback", time.time() + 60)
        self.assertEqual(
            db.consume_meta_oauth_state(connection["id"], "second", time.time()),
            "https://app/callback",
        )
        self.assertIsNone(db.consume_meta_oauth_state(connection["id"], "second", time.time()))

    def test_scope_selection_and_authorization_hosts_are_official(self):
        instagram = {
            "platform": "instagram",
            "auth_strategy": "instagram_login",
            "app_id": "123",
            "api_version": "v26.0",
            "requested_capabilities": ["publish", "messages"],
        }
        url = meta_social.authorization_url(instagram, "https://app/callback", "state")
        parsed = urlparse(url)
        scopes = set(parse_qs(parsed.query)["scope"][0].split(","))
        self.assertEqual(parsed.netloc, "www.instagram.com")
        self.assertIn("instagram_business_basic", scopes)
        self.assertIn("instagram_business_content_publish", scopes)
        self.assertNotIn("instagram_business_manage_messages", scopes)
        self.assertFalse(
            next(
                item for item in meta_social.platform_definition("instagram")["capabilities"]
                if item["id"] == "messages"
            )["available"]
        )

        facebook = meta_social.platform_definition("facebook")
        self.assertIn("personal profiles", facebook["excluded"])
        self.assertIn("Facebook Groups", facebook["excluded"])
        self.assertTrue(any(item["id"] == "ads_read" for item in facebook["capabilities"]))

    def test_threads_code_exchange_uses_short_then_long_lived_token(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.method == "POST":
                return httpx.Response(200, json={"access_token": "short", "expires_in": 3600})
            return httpx.Response(200, json={"access_token": "long", "expires_in": 5000})

        connection = {
            "platform": "threads",
            "auth_strategy": "threads_oauth",
            "app_id": "id",
            "app_secret": "secret",
            "api_version": "v1.0",
        }
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            token = meta_social.exchange_code(
                connection, "code", "https://app/callback", client=client
            )
        self.assertEqual(token["access_token"], "long")
        self.assertIn("graph.threads.net/oauth/access_token", calls[0])
        self.assertIn("graph.threads.net/access_token", calls[1])

    def test_facebook_discovery_includes_pages_and_requested_ad_accounts(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/me/accounts"):
                return httpx.Response(
                    200,
                    json={"data": [{"id": "page-1", "name": "Page", "access_token": "page-token", "tasks": ["CREATE_CONTENT"]}]},
                )
            return httpx.Response(
                200,
                json={"data": [{"id": "act_8", "account_id": "8", "name": "Ads", "currency": "EUR"}]},
            )

        connection = {
            "platform": "facebook",
            "auth_strategy": "facebook_login",
            "api_version": "v26.0",
            "access_token": "user-token",
            "requested_capabilities": ["ads_read"],
        }
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            accounts = meta_social.discover_accounts(connection, client=client)
        self.assertEqual({item["account_type"] for item in accounts}, {"facebook_page", "meta_ad_account"})

    def test_agent_confirmation_defaults_keep_outbound_actions_gated(self):
        expectations = {
            "facebook": {"publish_page_post", "set_ad_campaign_status"},
            "messenger": set(),
            "instagram": {"publish_image"},
            "threads": {"publish_text"},
            "whatsapp": {"send_message", "reply_to_message", "send_attachment"},
        }
        for key, expected in expectations.items():
            self.assertEqual(set(builtin_agents.default_confirmation_tools(key)), expected)
        nodes = set(langgraph_agent.build_graph().get_graph().nodes)
        self.assertTrue(set(expectations) <= nodes)
        delegate_names = {item.name for item in tools.DELEGATE_TOOLS}
        self.assertTrue(
            {f"delegate_to_{key}" for key in expectations} <= delegate_names
        )

    def test_meta_api_create_and_oauth_callback_keep_exchange_server_side(self):
        import server as web_server

        async def inline_thread(callback, *args, **kwargs):
            return callback(*args, **kwargs)

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                definitions = await client.get("/api/meta/platforms")
                self.assertEqual(definitions.status_code, 200)
                self.assertEqual(definitions.json()[0]["id"], "facebook")

                unavailable = await client.post(
                    "/api/meta/connections",
                    json={
                        "platform": "messenger",
                        "name": "Unsafe Messenger request",
                        "auth_strategy": "facebook_login",
                        "app_id": "app-id",
                        "app_secret": "app-secret",
                        "api_version": "v26.0",
                        "requested_capabilities": ["conversations"],
                    },
                )
                self.assertEqual(unavailable.status_code, 400)

                created = await client.post(
                    "/api/meta/connections",
                    json={
                        "platform": "threads",
                        "name": "Threads API test",
                        "auth_strategy": "threads_oauth",
                        "app_id": "app-id",
                        "app_secret": "app-secret",
                        "api_version": "v1.0",
                        "redirect_uri": "https://mounir.test/api/meta/threads/callback",
                        "requested_capabilities": ["publish"],
                    },
                )
                self.assertEqual(created.status_code, 200)
                connection = created.json()
                self.assertNotIn("app_secret", connection)
                self.assertFalse(connection["token_configured"])

                started = await client.post(
                    f"/api/meta/connections/{connection['id']}/oauth/start"
                )
                self.assertEqual(started.status_code, 200)
                oauth = started.json()
                state = parse_qs(urlparse(oauth["authorization_url"]).query)["state"][0]

                with (
                    patch.object(
                        web_server.meta_social,
                        "exchange_code",
                        return_value={
                            "access_token": "server-token",
                            "token_type": "bearer",
                            "expires_at": None,
                        },
                    ),
                    patch.object(
                        web_server.meta_social,
                        "discover_accounts",
                        return_value=[{
                            "external_id": "threads-user",
                            "name": "Mounir Threads",
                            "username": "mounir",
                            "account_type": "threads_profile",
                        }],
                    ),
                    patch.object(web_server.asyncio, "to_thread", inline_thread),
                ):
                    callback = await client.get(
                        f"/api/meta/connections/{connection['id']}/oauth/callback",
                        params={"code": "official-code", "state": state},
                        follow_redirects=False,
                    )
                self.assertEqual(callback.status_code, 303)
                self.assertEqual(callback.headers["location"], "/admin/meta/threads?oauth=connected")

                saved = (
                    await client.get(
                        "/api/meta/connections", params={"platform": "threads"}
                    )
                ).json()[0]
                self.assertTrue(saved["token_configured"])
                self.assertEqual(saved["accounts"][0]["username"], "mounir")

        asyncio.run(exercise_api())

    def test_whatsapp_business_agent_is_separate_and_rejects_cold_recipient(self):
        db.update_whatsapp_settings(
            enabled=True,
            access_token="channel-token",
            phone_number_id="channel-phone-id",
            business_account_id="channel-business-id",
            app_secret="channel-secret",
            api_version="v25.0",
        )
        db.update_whatsapp_connection("connected", webhook_verified=True)
        db.pair_whatsapp_phone("21611111111", "Ada")
        connection = db.create_meta_whatsapp_connection(
            name="Support",
            app_id="business-app-id",
            access_token="business-token",
            phone_number_id="business-phone-id",
            business_account_id="business-account-id",
            app_secret="business-secret",
        )
        db.update_meta_whatsapp_connection_status(
            connection["id"],
            "connected",
            webhook_verified=True,
            granted_permissions=[
                "whatsapp_business_management",
                "whatsapp_business_messaging",
            ],
        )
        db.record_meta_whatsapp_message(
            connection_id=connection["id"],
            message_id="inbound-1",
            contact_phone="21622222222",
            contact_name="Grace",
            direction="inbound",
            body="Hello",
            occurred_at=datetime.now(timezone.utc).isoformat(),
        )
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"messages": [{"id": "sent"}]})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = whatsapp_business.send_text(
                connection["id"], "21622222222", "Hello Grace", client=client
            )
        self.assertEqual(result["messages"][0]["id"], "sent")
        self.assertIn("business-phone-id/messages", str(requests[0].url))
        self.assertNotIn("channel-phone-id", str(requests[0].url))
        self.assertIn(b'"to":"21622222222"', requests[0].content)
        db.update_meta_whatsapp_connection(
            connection["id"], requested_capabilities=["conversations"]
        )
        with self.assertRaisesRegex(ValueError, "Enable the Send and reply"):
            whatsapp_business.send_text(
                connection["id"], "21622222222", "Disabled capability"
            )
        db.update_meta_whatsapp_connection(
            connection["id"],
            requested_capabilities=["conversations", "send_messages", "send_attachments"],
        )
        with self.assertRaisesRegex(ValueError, "not present"):
            whatsapp_business.send_text(
                connection["id"], "21699999999", "Cold message"
            )
        with db._connect() as conn:
            conn.execute(
                "UPDATE meta_whatsapp_messages SET occurred_at = ? WHERE message_id = 'inbound-1'",
                ("2020-01-01T00:00:00+00:00",),
            )
            conn.commit()
        with self.assertRaisesRegex(ValueError, "24-hour"):
            whatsapp_business.send_text(
                connection["id"], "21622222222", "This must not send"
            )

    def test_whatsapp_business_webhook_persists_inbox_messages(self):
        connection = db.create_meta_whatsapp_connection(
            name="Inbox",
            app_id="business-app-id",
            access_token="token",
            phone_number_id="phone-id",
            business_account_id="business-id",
            app_secret="secret",
        )
        saved = whatsapp_business.handle_webhook(
            connection["id"],
            {
                "entry": [{
                    "changes": [{
                        "value": {
                            "contacts": [{"wa_id": "21633333333", "profile": {"name": "Lin"}}],
                            "messages": [{
                                "id": "wamid.1",
                                "from": "21633333333",
                                "timestamp": "1770000000",
                                "type": "document",
                                "document": {
                                    "id": "media-1",
                                    "filename": "report.pdf",
                                    "mime_type": "application/pdf",
                                },
                            }],
                        }
                    }]
                }]
            },
        )
        self.assertEqual(saved, 1)
        conversations = db.list_meta_whatsapp_conversations(connection["id"])
        self.assertEqual(conversations[0]["contact_name"], "Lin")
        messages = db.list_meta_whatsapp_messages(
            connection["id"], contact_phone="21633333333"
        )
        self.assertEqual(messages[0]["message_type"], "document")
        self.assertEqual(messages[0]["media_id"], "media-1")

    def test_whatsapp_manifest_and_token_permissions_are_official_and_verified(self):
        definition = whatsapp_business.platform_definition()
        permission_ids = {item["id"] for item in definition["permissions"]}
        self.assertTrue(
            {"whatsapp_business_messaging", "whatsapp_business_management"}
            <= permission_ids
        )
        unavailable = {
            item["id"]
            for item in definition["capabilities"]
            if item.get("available") is False
        }
        self.assertIn("template_messages", unavailable)
        self.assertEqual(
            whatsapp_business.validate_capabilities(["send_messages"]),
            ["conversations", "send_messages"],
        )
        with self.assertRaisesRegex(ValueError, "Unsupported WhatsApp capability"):
            whatsapp_business.validate_capabilities(["template_messages"])

        connection = db.create_meta_whatsapp_connection(
            name="Permission test",
            app_id="app-id",
            access_token="user-token",
            phone_number_id="phone-id",
            business_account_id="business-id",
            app_secret="app-secret",
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/debug_token"):
                self.assertEqual(request.headers["authorization"], "Bearer app-id|app-secret")
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "is_valid": True,
                            "scopes": [
                                "whatsapp_business_management",
                                "whatsapp_business_messaging",
                            ],
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "phone-id",
                    "display_phone_number": "+216 22 222 222",
                    "verified_name": "Mounir Support",
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            identity = whatsapp_business.test_connection(connection["id"], client=client)
        self.assertEqual(
            identity["granted_permissions"],
            ["whatsapp_business_management", "whatsapp_business_messaging"],
        )

        db.update_meta_whatsapp_connection(
            connection["id"], requested_capabilities=["conversations"]
        )
        refreshed = db.get_meta_whatsapp_connection(connection["id"])
        self.assertEqual(refreshed["requested_capabilities"], ["conversations"])

    def test_whatsapp_business_api_does_not_change_private_channel(self):
        import server as web_server

        db.update_whatsapp_settings(
            access_token="channel-token",
            phone_number_id="channel-phone",
            business_account_id="channel-business",
            app_secret="channel-secret",
        )

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                definition = (await client.get("/api/meta/whatsapp/definition")).json()
                self.assertEqual(definition["id"], "whatsapp")
                self.assertTrue(
                    any(
                        item["id"] == "whatsapp_business_messaging"
                        for item in definition["permissions"]
                    )
                )
                unavailable = await client.post(
                    "/api/meta/whatsapp/connections",
                    json={"requested_capabilities": ["template_messages"]},
                )
                self.assertEqual(unavailable.status_code, 400)
                response = await client.post(
                    "/api/meta/whatsapp/connections",
                    json={
                        "name": "Sales",
                        "app_id": "business-app-id",
                        "access_token": "business-token",
                        "phone_number_id": "business-phone",
                        "business_account_id": "business-account",
                        "app_secret": "business-secret",
                        "api_version": "v26.0",
                        "requested_capabilities": ["send_messages"],
                    },
                )
                self.assertEqual(response.status_code, 200)
                business = response.json()
                self.assertEqual(business["phone_number_id"], "business-phone")
                self.assertNotIn("access_token", business)
                self.assertNotIn("app_secret", business)
                self.assertEqual(
                    business["requested_capabilities"],
                    ["conversations", "send_messages"],
                )
                self.assertIn(
                    f"/api/meta/whatsapp/connections/{business['id']}/webhook",
                    business["webhook_path"],
                )
                channel = (await client.get("/api/whatsapp")).json()
                self.assertEqual(channel["phone_number_id"], "channel-phone")
                await client.delete(
                    f"/api/meta/whatsapp/connections/{business['id']}"
                )
                channel_after = (await client.get("/api/whatsapp")).json()
                self.assertEqual(channel_after["phone_number_id"], "channel-phone")

        asyncio.run(exercise_api())


if __name__ == "__main__":
    unittest.main()
