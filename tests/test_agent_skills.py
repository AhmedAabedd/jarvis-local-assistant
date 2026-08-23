from __future__ import annotations

import base64
import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from mounir import agent_skills, db, skill_store


SKILL_MD = b"""---
name: review-code
description: Review code with a consistent checklist.
---
Read the change carefully and report concrete findings.
"""


class AgentSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temporary.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temporary.name) / "legacy.json"
        db.init()
        self.model = db.add_model(
            "Skill test model",
            "test/model",
            "OpenAI compatible",
            "http://localhost:11434/v1",
            "",
        )

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temporary.cleanup()

    def _package(self, name: str = "review-code", description: str = "Review code."):
        skill_md = (
            f"---\nname: {name}\ndescription: {description}\n---\n"
            "Follow the saved review instructions.\n"
        ).encode()
        return agent_skills.build_package(
            [
                (f"{name}/SKILL.md", skill_md),
                (f"{name}/references/checklist.md", b"Stored for future support."),
            ]
        )

    def test_package_is_normalized_and_full_folder_is_preserved(self):
        package = agent_skills.build_package(
            [
                ("review-code/SKILL.md", SKILL_MD),
                ("review-code/references/checklist.md", b"Check tests."),
            ]
        )
        self.assertEqual(package["name"], "review-code")
        self.assertEqual(
            [item["path"] for item in package["files"]],
            ["SKILL.md", "references/checklist.md"],
        )
        with zipfile.ZipFile(io.BytesIO(package["package_blob"])) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["SKILL.md", "references/checklist.md"],
            )

    def test_zip_rejects_traversal_and_invalid_frontmatter(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("../SKILL.md", SKILL_MD)
        with self.assertRaisesRegex(ValueError, "unsafe"):
            agent_skills.build_package([("bad.zip", raw.getvalue())])
        with self.assertRaisesRegex(ValueError, "lowercase"):
            agent_skills.build_package(
                [("SKILL.md", SKILL_MD.replace(b"review-code", b"Review Code", 1))]
            )

    def test_external_package_repairs_only_an_incompatible_scoped_name(self):
        scoped = SKILL_MD.replace(
            b"name: review-code", b'name: "@team/review-code"'
        )
        package = agent_skills.build_package(
            [("SKILL.md", scoped)],
            source_type="tank",
            source_ref="@team/review-code",
            external_name="@team/review-code",
        )
        self.assertEqual(package["name"], "review-code")
        self.assertIn("name: review-code", package["skill_md"])
        self.assertEqual(package["source_ref"], "@team/review-code")

    def test_assignments_are_persisted_for_every_agent_kind(self):
        saved = db.add_skill_package(self._package())
        targets = db.list_skill_targets()
        supervisor = next(item for item in targets if item["agent_type"] == "supervisor")
        builtin = next(item for item in targets if item["agent_type"] == "builtin")
        updated = db.set_skill_assignments(saved["id"], [supervisor, builtin])
        self.assertEqual(updated["assignment_count"], 2)
        self.assertEqual(
            db.list_agent_skills("builtin", builtin["agent_key"])[0]["name"],
            "review-code",
        )

    def test_duplicate_skill_name_is_blocked_per_agent_only(self):
        first = db.add_skill_package(self._package(description="First instructions."))
        second = db.add_skill_package(self._package(description="Different instructions."))
        target = next(
            item for item in db.list_skill_targets() if item["agent_type"] == "supervisor"
        )
        db.set_skill_assignments(first["id"], [target])
        with self.assertRaisesRegex(ValueError, "already has a skill"):
            db.set_skill_assignments(second["id"], [target])

    def test_activation_returns_only_skill_md_instructions(self):
        saved = db.add_skill_package(self._package())
        db.set_skill_assignments(
            saved["id"],
            [{"agent_type": "supervisor", "agent_key": "supervisor"}],
        )
        prompt, tool = agent_skills.runtime_access("supervisor", "supervisor")
        self.assertIn("review-code", prompt)
        result = tool.invoke({"name": "review-code"})
        self.assertIn("Follow the saved review instructions.", result)
        self.assertNotIn("Stored for future support.", result)
        self.assertIn("supporting files", result)

    def test_subagent_form_skill_ids_are_created_updated_and_returned(self):
        first = db.add_skill_package(self._package("review-code", "Review code."))
        second = db.add_skill_package(self._package("write-tests", "Write tests."))
        agent = db.add_subagent(
            "Reviewer",
            "Reviews implementation work.",
            "Use the selected skills.",
            self.model["id"],
            skill_ids=[first["id"]],
        )
        self.assertEqual(agent["skill_ids"], [first["id"]])
        self.assertEqual(
            [skill["id"] for skill in db.list_agent_skills("subagent", str(agent["id"]))],
            [first["id"]],
        )

        updated = db.update_subagent(agent["id"], skill_ids=[second["id"]])
        self.assertEqual(updated["skill_ids"], [second["id"]])
        self.assertEqual(
            [skill["id"] for skill in db.list_agent_skills("subagent", str(agent["id"]))],
            [second["id"]],
        )


class SkillStoreTests(unittest.IsolatedAsyncioTestCase):
    def test_catalog_projection_preserves_owner_scoped_identity_and_stats(self):
        item = skill_store._item(
            {
                "name": "@team/review-code",
                "description": "Review code consistently.",
                "downloads": 120,
                "stars": 9,
                "publisher": "team",
                "latestVersion": "1.0.0",
                "auditScore": 9,
                "scanVerdict": "pass_with_notes",
            }
        )
        self.assertEqual(item["reference"], "@team/review-code")
        self.assertEqual(item["owner"], "team")
        self.assertEqual(item["stars"], 9)
        self.assertEqual(item["downloads"], 120)
        self.assertEqual(item["installs"], 0)
        self.assertEqual(item["installability"], "Pass With Notes · Audit 9/10")
        self.assertEqual(
            item["source_url"], "https://tankpkg.dev/skills/@team/review-code"
        )

    async def test_tank_download_uses_scoped_identity_and_verifies_archive(self):
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("review-code/SKILL.md", SKILL_MD)
            archive.writestr("review-code/reference.md", "kept")
        archive_content = archive_buffer.getvalue()
        integrity = "sha512-" + base64.b64encode(
            hashlib.sha512(archive_content).digest()
        ).decode()

        class FakeClient:
            calls = []

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, **kwargs):
                params = kwargs.get("params") or {}
                self.calls.append((url, params))
                request = httpx.Request("GET", url)
                if url.endswith("/skills/%40team%2Freview-code"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={
                            "name": "@team/review-code",
                            "description": "Review code consistently.",
                            "latestVersion": "1.0.0",
                            "publisher": {"name": "team"},
                        },
                    )
                if url.endswith("/skills/%40team%2Freview-code/1.0.0"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={
                            "name": "@team/review-code",
                            "version": "1.0.0",
                            "downloadUrl": "https://cdn.example.test/review-code.zip",
                            "integrity": integrity,
                            "auditScore": 9,
                            "verdict": "PASS",
                        },
                    )
                if url.endswith(
                    "/skills/%40team%2Freview-code/1.0.0/files/SKILL.md"
                ):
                    return httpx.Response(200, request=request, content=SKILL_MD)
                return httpx.Response(200, request=request, content=archive_content)

        with patch.object(skill_store.httpx, "AsyncClient", FakeClient):
            detail = await skill_store.details("tank", "@team/review-code")
            package = await skill_store.download("tank", "@team/review-code")
        self.assertEqual(detail["reference"], "@team/review-code")
        self.assertEqual(detail["skill_md"], SKILL_MD.decode())
        self.assertEqual(package["source_type"], "tank")
        self.assertEqual(package["source_ref"], "@team/review-code")
        self.assertTrue(
            any(
                "/skills/%40team%2Freview-code/1.0.0" in url
                for url, _params in FakeClient.calls
            )
        )
        self.assertEqual(
            [item["path"] for item in package["files"]],
            ["SKILL.md", "reference.md"],
        )

    async def test_tank_download_falls_back_to_documented_file_endpoint(self):
        class FakeClient:
            calls = []

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, **kwargs):
                self.calls.append(url)
                request = httpx.Request("GET", url)
                if url.endswith("/skills/%40team%2Freview-code"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={
                            "name": "@team/review-code",
                            "latestVersion": "1.0.0",
                        },
                    )
                if url.endswith("/skills/%40team%2Freview-code/1.0.0"):
                    return httpx.Response(
                        200,
                        request=request,
                        json={
                            "name": "@team/review-code",
                            "version": "1.0.0",
                            "files": [{"path": "SKILL.md"}],
                        },
                    )
                if url.endswith(
                    "/skills/%40team%2Freview-code/1.0.0/files/SKILL.md"
                ):
                    return httpx.Response(200, request=request, content=SKILL_MD)
                return httpx.Response(404, request=request)

        with patch.object(skill_store.httpx, "AsyncClient", FakeClient):
            package = await skill_store.download("tank", "@team/review-code")

        self.assertEqual(package["source_ref"], "@team/review-code")
        self.assertEqual([item["path"] for item in package["files"]], ["SKILL.md"])
        self.assertTrue(
            any(url.endswith("/1.0.0/files/SKILL.md") for url in FakeClient.calls)
        )


if __name__ == "__main__":
    unittest.main()
