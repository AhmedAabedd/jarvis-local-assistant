from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


_IMPORT_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["MOUNIR_DATA_DIR"] = _IMPORT_DATA_DIR.name

from mounir import db, mcp_registry


class McpRegistryProjectionTests(unittest.TestCase):
    def test_registry_entry_exposes_remote_and_package_configuration(self):
        item = mcp_registry._item(
            {
                "server": {
                    "name": "io.example/calendar",
                    "title": "Calendar",
                    "description": "Calendar tools.",
                    "version": "1.2.3",
                    "repository": {
                        "url": "https://example.test/calendar",
                        "source": "github",
                        "subfolder": "packages/server",
                    },
                    "remotes": [
                        {
                            "type": "streamable-http",
                            "url": "https://mcp.example.test",
                            "headers": [
                                {"name": "Authorization", "isRequired": True}
                            ],
                        },
                        {
                            "type": "streamable-http",
                            "url": "{base_url}/mcp",
                            "variables": {
                                "base_url": {
                                    "isRequired": True,
                                    "description": "Your organization endpoint.",
                                }
                            },
                        },
                    ],
                    "packages": [
                        {
                            "registryType": "npm",
                            "identifier": "@example/calendar-mcp",
                            "version": "1.2.3",
                            "environmentVariables": [
                                {"name": "CALENDAR_TOKEN", "isRequired": True}
                            ],
                        }
                    ],
                    "_meta": {
                        "io.modelcontextprotocol.registry/publisher-provided": {
                            "contactEmail": "maintainer@example.test"
                        }
                    },
                },
                "_meta": {
                    "io.modelcontextprotocol.registry/official": {
                        "publishedAt": "2026-01-02T00:00:00Z",
                        "status": "active",
                        "isLatest": True,
                    }
                },
            }
        )

        self.assertEqual(item["reference"], "io.example/calendar")
        self.assertEqual(item["repository_url"], "https://example.test/calendar")
        self.assertEqual(item["repository_source"], "github")
        self.assertEqual(item["repository_subfolder"], "packages/server")
        self.assertEqual(item["publisher_contact"], "maintainer@example.test")
        self.assertEqual(item["status"], "active")
        self.assertTrue(item["is_latest"])
        self.assertEqual(len(item["install_options"]), 2)
        self.assertEqual(len(item["published_options"]), 3)
        self.assertFalse(item["published_options"][1]["configurable"])
        self.assertEqual(
            item["published_options"][1]["requirements"],
            ["base_url — Your organization endpoint."],
        )

        remote, package = item["install_options"]
        self.assertEqual(remote["transport"], "streamable_http")
        self.assertEqual(remote["headers"], {"Authorization": ""})
        self.assertEqual(remote["requirements"], ["Authorization"])
        self.assertEqual(package["transport"], "stdio")
        self.assertIn("@example/calendar-mcp@1.2.3", package["connection"])
        self.assertEqual(package["env"], {"CALENDAR_TOKEN": ""})


class McpRegistryDatabaseTests(unittest.TestCase):
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

    def test_registry_source_is_persisted_and_returned_by_api_projection(self):
        server = db.add_server(
            "Calendar",
            "npx -y @example/calendar-mcp@1.2.3",
            source_type="registry",
            source_name="Official MCP Registry",
            source_ref="io.example/calendar",
            source_version="1.2.3",
            source_url="https://example.test/calendar",
        )

        public = db.server_for_api(db.get_server(server["id"]))
        self.assertEqual(public["source_type"], "registry")
        self.assertEqual(public["source_name"], "Official MCP Registry")
        self.assertEqual(public["source_ref"], "io.example/calendar")
        self.assertEqual(public["source_version"], "1.2.3")
        self.assertEqual(public["source_url"], "https://example.test/calendar")


if __name__ == "__main__":
    unittest.main()
