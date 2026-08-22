from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mounir import db, embedding_models


class EmbeddingModelConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        self.old_gbrain_home = os.environ.get("MOUNIR_GBRAIN_HOME")
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        os.environ["MOUNIR_GBRAIN_HOME"] = str(Path(self.temp_dir.name) / "gbrain")
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        if self.old_gbrain_home is None:
            os.environ.pop("MOUNIR_GBRAIN_HOME", None)
        else:
            os.environ["MOUNIR_GBRAIN_HOME"] = self.old_gbrain_home
        self.temp_dir.cleanup()

    def test_registry_is_empty_by_default_and_credentials_are_masked(self):
        self.assertEqual(db.list_embedding_models(), [])
        knowledge = next(
            item for item in db.list_builtin_agents() if item["key"] == "knowledge"
        )
        self.assertFalse(knowledge["embedding_enabled"])
        self.assertIsNone(knowledge["embedding_model_id"])

        saved = db.add_embedding_model(
            "Local embeddings",
            "local",
            "ollama",
            "custom-embedder",
            "http://localhost:11434/v1",
            "$MY_EMBEDDING_KEY",
        )
        public = db.embedding_model_for_api(saved)
        self.assertNotIn("api_key", public)
        self.assertTrue(public["api_key_configured"])

    def test_knowledge_requires_a_tested_model_and_persists_its_choice(self):
        saved = db.add_embedding_model(
            "Hosted embeddings",
            "cloud",
            "openai_compatible",
            "provider/model",
            "https://embedding.example/v1",
        )
        with self.assertRaisesRegex(ValueError, "test the embedding model"):
            db.update_builtin_agent(
                "knowledge",
                embedding_enabled=True,
                embedding_model_id=saved["id"],
            )

        db.save_embedding_test(saved["id"], dimensions=1024)
        knowledge = db.update_builtin_agent(
            "knowledge",
            embedding_enabled=True,
            embedding_model_id=saved["id"],
        )
        self.assertTrue(knowledge["embedding_enabled"])
        self.assertEqual(knowledge["embedding_model_id"], saved["id"])
        runtime = db.get_knowledge_embedding_runtime()
        self.assertEqual(runtime["model"], "provider/model")
        self.assertEqual(runtime["dimensions"], 1024)

        with self.assertRaisesRegex(ValueError, "Disable Knowledge embeddings"):
            db.update_embedding_model(saved["id"], model="replacement")
        self.assertEqual(
            db.delete_embedding_model_result(saved["id"]).status,
            "in_use",
        )

        db.update_builtin_agent(
            "knowledge",
            embedding_enabled=False,
            embedding_model_id=saved["id"],
        )
        self.assertTrue(db.delete_embedding_model_result(saved["id"]).deleted)
        knowledge = next(
            item for item in db.list_builtin_agents() if item["key"] == "knowledge"
        )
        self.assertIsNone(knowledge["embedding_model_id"])

    def test_gbrain_server_receives_only_the_selected_provider_environment(self):
        saved = db.add_embedding_model(
            "Universal endpoint",
            "cloud",
            "openai_compatible",
            "custom-embedding-model",
            "https://embedding.example/v1",
            "secret",
        )
        db.save_embedding_test(saved["id"], dimensions=640)
        db.update_builtin_agent(
            "knowledge",
            embedding_enabled=True,
            embedding_model_id=saved["id"],
        )
        server = db.get_builtin_gbrain_server()
        environment = db.build_server_spec(server["id"])["env"]
        self.assertEqual(environment["LITELLM_BASE_URL"], "https://embedding.example/v1")
        self.assertEqual(environment["LITELLM_API_KEY"], "secret")
        self.assertNotIn("OPENAI_API_KEY", environment)

    def test_management_api_tests_and_enables_a_saved_model(self):
        import httpx
        import server as web_server

        async def exercise_api():
            transport = httpx.ASGITransport(app=web_server.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost"
            ) as client:
                created = await client.post(
                    "/api/embedding-models",
                    json={
                        "name": "API embedding",
                        "location": "cloud",
                        "adapter": "openai_compatible",
                        "model": "embed-v1",
                        "base_url": "https://embedding.example/v1",
                        "api_key": "private",
                    },
                )
                self.assertEqual(created.status_code, 200)
                self.assertNotIn("api_key", created.json())
                model_id = created.json()["id"]

                with patch.object(
                    embedding_models, "test_connection", return_value=384
                ):
                    tested = await client.post(
                        f"/api/embedding-models/{model_id}/test"
                    )
                self.assertEqual(tested.status_code, 200)
                self.assertEqual(tested.json()["dimensions"], 384)

                with patch.object(embedding_models, "apply_to_gbrain") as apply:
                    enabled = await client.put(
                        "/api/builtin-agents/knowledge",
                        json={
                            "embedding_enabled": True,
                            "embedding_model_id": model_id,
                        },
                    )
                self.assertEqual(enabled.status_code, 200)
                self.assertTrue(enabled.json()["embedding_enabled"])
                apply.assert_called_once()

        asyncio.run(exercise_api())


class EmbeddingAdapterTests(unittest.TestCase):
    def test_openai_compatible_test_detects_dimensions(self):
        response = SimpleNamespace(
            ok=True,
            json=lambda: {"data": [{"embedding": [0.1, 0.2, 0.3]}]},
        )
        with patch.object(embedding_models.requests, "request", return_value=response) as request:
            dimensions = embedding_models.test_connection(
                {
                    "adapter": "openai_compatible",
                    "model": "my-model",
                    "base_url": "https://example.test/v1",
                    "api_key": "key",
                }
            )
        self.assertEqual(dimensions, 3)
        self.assertEqual(request.call_args.args[:2], ("POST", "https://example.test/v1/embeddings"))
        self.assertEqual(request.call_args.kwargs["json"]["model"], "my-model")

    def test_ollama_discovery_uses_the_native_catalog(self):
        response = SimpleNamespace(
            ok=True,
            json=lambda: {"models": [{"name": "z-model"}, {"name": "a-model"}]},
        )
        with patch.object(embedding_models.requests, "request", return_value=response) as request:
            models = embedding_models.discover_models(
                {"adapter": "ollama", "base_url": "http://localhost:11434/v1"}
            )
        self.assertEqual(models, ["a-model", "z-model"])
        self.assertEqual(request.call_args.args[:2], ("GET", "http://localhost:11434/api/tags"))

    def test_gbrain_uses_its_generic_openai_compatible_recipe(self):
        config = {
            "adapter": "openai_compatible",
            "model": "user-defined-model",
            "base_url": "https://example.test/v1",
            "api_key": "key",
        }
        self.assertEqual(embedding_models.gbrain_target(config), "litellm:user-defined-model")
        self.assertEqual(
            embedding_models.gbrain_provider_environment(config),
            {
                "LITELLM_BASE_URL": "https://example.test/v1",
                "LITELLM_API_KEY": "key",
            },
        )

    def test_applying_a_model_uses_gbrains_supported_migration_command(self):
        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory, ".gbrain")
            config_dir.mkdir()
            config_path = config_dir / "config.json"
            config_path.write_text(
                json.dumps({"engine": "pglite", "embedding_disabled": True}),
                encoding="utf-8",
            )
            completed = SimpleNamespace(returncode=0, stdout='{"status":"complete"}', stderr="")
            model = {
                "adapter": "ollama",
                "model": "my-embedder",
                "base_url": "http://localhost:11434/v1",
                "api_key": "",
                "dimensions": 768,
            }
            with (
                patch.dict(os.environ, {"MOUNIR_GBRAIN_HOME": directory}),
                patch.object(embedding_models, "ensure_local_gbrain"),
                patch.object(embedding_models, "_gbrain_executable", return_value="/opt/gbrain"),
                patch.object(embedding_models.subprocess, "run", return_value=completed) as run,
            ):
                embedding_models.apply_to_gbrain(True, model)
            self.assertEqual(
                run.call_args.args[0],
                [
                    "/opt/gbrain",
                    "migrate",
                    "embeddings",
                    "--to",
                    "ollama:my-embedder",
                    "--dim",
                    "768",
                    "--yes",
                    "--json",
                ],
            )
            saved_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertFalse(saved_config["embedding_disabled"])


if __name__ == "__main__":
    unittest.main()
