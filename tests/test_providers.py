from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from mounir import db


class ProviderConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        self.old_api_key = os.environ.get("EXAMPLE_PROVIDER_KEY")
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        os.environ["EXAMPLE_PROVIDER_KEY"] = "resolved-secret"
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        if self.old_api_key is None:
            os.environ.pop("EXAMPLE_PROVIDER_KEY", None)
        else:
            os.environ["EXAMPLE_PROVIDER_KEY"] = self.old_api_key
        self.temp_dir.cleanup()

    def test_provider_connections_are_reused_masked_and_propagated(self):
        provider = db.add_provider(
            "Example provider",
            "Shared hosted endpoints.",
            [
                {"name": "LLM", "value": "https://api.example.test/v1"},
                {"name": "Voice", "value": "https://voice.example.test/v1"},
            ],
            [{"name": "Primary", "value": "$EXAMPLE_PROVIDER_KEY"}],
        )
        self.assertEqual(provider["api_keys"][0]["value"], "")
        self.assertEqual(provider["api_keys"][0]["preview"], "$EXA.....")
        self.assertTrue(provider["api_keys"][0]["configured"])

        model = db.add_model(
            "Provider-backed model",
            "example/model",
            "",
            "",
            "",
            "cloud",
            provider["id"],
            provider["base_urls"][0]["id"],
            provider["api_keys"][0]["id"],
        )
        public = db.model_for_api(model)
        self.assertEqual(public["provider_name"], "Example provider")
        self.assertEqual(public["provider_base_url_name"], "LLM")
        self.assertEqual(public["provider_api_key_name"], "Primary")
        self.assertTrue(public["api_key_configured"])
        runtime = db.get_model_runtime(model["id"])
        self.assertEqual(runtime["base_url"], "https://api.example.test/v1")
        self.assertEqual(runtime["api_key"], "resolved-secret")

        updated = db.update_provider(
            provider["id"],
            name="Example renamed",
            description="Updated.",
            base_urls=[
                {
                    "id": provider["base_urls"][0]["id"],
                    "name": "LLM primary",
                    "value": "https://api2.example.test/v1",
                },
                provider["base_urls"][1],
            ],
            api_keys=[
                {
                    "id": provider["api_keys"][0]["id"],
                    "name": "Primary renamed",
                    "value": "",
                }
            ],
        )
        self.assertTrue(updated["api_keys"][0]["configured"])
        refreshed = db.get_model_runtime(model["id"])
        self.assertEqual(refreshed["provider"], "Example renamed")
        self.assertEqual(refreshed["base_url"], "https://api2.example.test/v1")
        self.assertEqual(refreshed["api_key"], "resolved-secret")

        with self.assertRaisesRegex(ValueError, "used by"):
            db.update_provider(
                provider["id"],
                base_urls=[provider["base_urls"][1]],
                api_keys=updated["api_keys"],
            )
        self.assertEqual(db.delete_provider_result(provider["id"]).status, "in_use")

    def test_same_model_name_can_be_used_by_different_providers(self):
        first_provider = db.add_provider(
            "First service",
            base_urls=[{"name": "LLM", "value": "https://first.example/v1"}],
        )
        second_provider = db.add_provider(
            "Second service",
            base_urls=[{"name": "LLM", "value": "https://second.example/v1"}],
        )

        first = db.add_model(
            "Shared model",
            "vendor/model",
            "",
            "",
            "",
            "cloud",
            first_provider["id"],
            first_provider["base_urls"][0]["id"],
        )
        second = db.add_model(
            "Shared model",
            "vendor/model",
            "",
            "",
            "",
            "cloud",
            second_provider["id"],
            second_provider["base_urls"][0]["id"],
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(
            [model["name"] for model in db.list_models()],
            ["Shared model", "Shared model"],
        )
        self.assertIsNone(db.get_model_by_name("Shared model"))
        labels = [option["label"] for option in db.get_supervisor_config()["model_options"]]
        self.assertTrue(any("First service" in label for label in labels))
        self.assertTrue(any("Second service" in label for label in labels))

    def test_embedding_and_voice_models_keep_adapters_separate_from_provider(self):
        provider = db.add_provider(
            "Universal service",
            base_urls=[{"name": "API", "value": "https://service.example.test/v1"}],
            api_keys=[{"name": "Account", "value": "secret"}],
        )
        url_id = provider["base_urls"][0]["id"]
        key_id = provider["api_keys"][0]["id"]
        embedding = db.add_embedding_model(
            "Hosted embedding",
            "cloud",
            "openai_compatible",
            "embed-v1",
            "",
            "",
            provider["id"],
            url_id,
            key_id,
        )
        voice = db.add_voice_model(
            "Hosted transcription",
            "stt",
            provider="openai_compatible",
            model="whisper-v1",
            language="auto",
            provider_id=provider["id"],
            provider_base_url_id=url_id,
            provider_api_key_id=key_id,
        )
        self.assertEqual(embedding["adapter"], "openai_compatible")
        self.assertEqual(embedding["provider_name"], "Universal service")
        self.assertEqual(voice["provider"], "openai_compatible")
        self.assertEqual(voice["provider_name"], "Universal service")

    def test_management_api_never_returns_provider_key_values(self):
        import httpx
        import server as web_server

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                created = await client.post(
                    "/api/providers",
                    json={
                        "name": "API provider",
                        "description": "Created through the API.",
                        "base_urls": [
                            {"name": "LLM", "value": "https://api.example.test/v1"}
                        ],
                        "api_keys": [{"name": "Primary", "value": "private"}],
                    },
                )
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()["api_keys"][0]["value"], "")
                self.assertEqual(created.json()["api_keys"][0]["preview"], "priv.....")
                listed = await client.get("/api/providers")
                saved = next(
                    provider
                    for provider in listed.json()
                    if provider["name"] == "API provider"
                )
                self.assertEqual(saved["api_keys"][0]["value"], "")
                self.assertEqual(saved["api_keys"][0]["preview"], "priv.....")
                self.assertTrue(saved["api_keys"][0]["configured"])

        asyncio.run(exercise_api())


if __name__ == "__main__":
    unittest.main()
