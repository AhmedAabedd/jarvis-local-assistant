from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mounir import config, db, path_search, tools as mounir_tools
from mounir.specialists import artifacts, media


class MediaArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_public_tools_stay_compact_and_hide_frame_sampling(self):
        self.assertEqual(
            [tool.name for tool in media.TOOLS],
            [
                "read_file", "create_file", "edit_file", "load_media",
                "generate_media", "find_files",
            ],
        )

    def test_supervisor_no_longer_exposes_direct_file_tools(self):
        names = {tool.name for tool in mounir_tools.GENERAL_TOOLS}
        self.assertTrue(
            {"read_file", "write_file", "edit_file", "list_directory"}.isdisjoint(names)
        )
        self.assertIn(
            "any local file or media",
            mounir_tools.delegate_to_media_tool.description.casefold(),
        )

    def test_create_and_read_workbook(self):
        target = self.root / "sales.xlsx"
        content = json.dumps(
            {
                "sheets": [
                    {
                        "name": "Sales",
                        "rows": [["Month", "Revenue"], ["January", 1200], ["February", 1400]],
                    }
                ]
            }
        )
        self.assertTrue(artifacts.create_file(str(target), content).startswith("Created "))

        summary, parts = artifacts.read_file(str(target))

        self.assertEqual(parts, [])
        self.assertIn("Sheet: Sales", summary)
        self.assertIn("February\t1400", summary)

    def test_create_and_read_pdf_and_word_document(self):
        report = "Quarterly report. " * 20
        pdf_path = self.root / "report.pdf"
        docx_path = self.root / "report.docx"
        self.assertTrue(artifacts.create_file(str(pdf_path), report).startswith("Created "))
        self.assertTrue(artifacts.create_file(str(docx_path), report).startswith("Created "))

        pdf_summary, pdf_parts = artifacts.read_file(str(pdf_path))
        docx_summary, docx_parts = artifacts.read_file(str(docx_path))

        self.assertEqual(pdf_parts, [])
        self.assertEqual(docx_parts, [])
        self.assertIn("Quarterly report", pdf_summary)
        self.assertIn("Quarterly report", docx_summary)

    def test_presentations_are_generated_and_loaded_as_media(self):
        target = self.root / "briefing.pptx"
        specification = json.dumps(
            {
                "title": "Launch plan",
                "subtitle": "August",
                "slides": [
                    {"title": "Goals", "bullets": ["Ship safely", "Measure adoption"]}
                ],
            }
        )
        result = artifacts.generate_media(str(target), "Create a launch deck", specification)
        self.assertTrue(result.startswith("Generated "))

        summary, parts = artifacts.load_media(str(target))

        self.assertEqual(parts, [])
        self.assertIn("Launch plan", summary)
        self.assertIn("Measure adoption", summary)

    def test_loading_video_uses_internal_sampler(self):
        target = self.root / "clip.mp4"
        target.write_bytes(b"fixture")
        with patch.object(
            artifacts, "_sample_video", return_value=("sampled internally", [{"type": "image_url"}])
        ) as sampler:
            result = artifacts.load_media(str(target))
        self.assertEqual(result[0], "sampled internally")
        sampler.assert_called_once_with(target)

    def test_find_files_filters_file_and_media_groups(self):
        (self.root / "report.pdf").write_bytes(b"pdf")
        (self.root / "photo.png").write_bytes(b"png")
        files, _ = artifacts.find_files(str(self.root), group="file")
        media_files, _ = artifacts.find_files(str(self.root), group="media")
        self.assertIn("report.pdf", files)
        self.assertNotIn("photo.png", files)
        self.assertIn("photo.png", media_files)
        self.assertNotIn("report.pdf", media_files)

    def test_generated_artifacts_respect_knowledge_folder_guard(self):
        knowledge = self.root / "knowledge"
        target = knowledge / "report.pdf"
        with patch.object(config, "KNOWLEDGE_DIR", knowledge):
            result = artifacts.create_file(str(target), "blocked")
        self.assertIn("only the knowledge agent may change", result)
        self.assertFalse(target.exists())

    def test_edit_file_preserves_read_before_edit_and_supports_append(self):
        target = self.root / "notes.py"
        target.write_text("idea = 'first'\n", encoding="utf-8")
        blocked = artifacts.edit_file(str(target), "append", "print(idea)")
        self.assertIn("Read", blocked)

        artifacts.read_file(str(target))
        replaced = artifacts.edit_file(
            str(target), "replace", "idea = 'better'", "idea = 'first'"
        )
        appended = artifacts.edit_file(str(target), "append", "print(idea)")

        self.assertIn("Replaced", replaced)
        self.assertIn("Appended", appended)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "idea = 'better'\nprint(idea)",
        )

    def test_image_generation_uses_saved_compatible_model(self):
        target = self.root / "generated.png"
        from PIL import Image

        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "blue").save(image_buffer, format="PNG")
        response = Mock(status_code=200)
        response.json.return_value = {
            "data": [{"b64_json": base64.b64encode(image_buffer.getvalue()).decode("ascii")}]
        }
        with (
            patch.object(
                db,
                "get_builtin_agent_generation_runtime",
                return_value={
                    "model": "configured-image-model",
                    "provider": "Compatible",
                    "base_url": "https://images.example/v1",
                    "api_key": "secret",
                },
            ),
            patch.object(artifacts.requests, "post", return_value=response) as post,
        ):
            result = artifacts.generate_media(str(target), "a blue circle")
        self.assertTrue(result.startswith("Generated "))
        with Image.open(target) as generated:
            self.assertEqual(generated.size, (2, 2))
        self.assertEqual(post.call_args.args[0], "https://images.example/v1/images/generations")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "configured-image-model")

    def test_mistral_image_generation_uses_builtin_chat_tool(self):
        target = self.root / "mistral-generated.png"
        from PIL import Image

        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_buffer, format="PNG")
        image_url = (
            "data:image/png;base64,"
            + base64.b64encode(image_buffer.getvalue()).decode("ascii")
        )
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [
                {
                    "messages": [
                        {"content": [{"type": "image_url", "image_url": image_url}]}
                    ]
                }
            ]
        }
        with (
            patch.object(
                db,
                "get_builtin_agent_generation_runtime",
                return_value={
                    "model": "mistral-small-latest",
                    "provider": "Mistral",
                    "base_url": "https://api.mistral.ai/v1",
                    "api_key": "secret",
                },
            ),
            patch.object(artifacts.requests, "post", return_value=response) as post,
        ):
            result = artifacts.generate_media(str(target), "a red circle")

        self.assertTrue(result.startswith("Generated "))
        self.assertEqual(
            post.call_args.args[0], "https://api.mistral.ai/v1/chat/completions"
        )
        self.assertEqual(
            post.call_args.kwargs["json"]["tools"], [{"type": "image_generation"}]
        )


class PathSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "home"
        self.documents = self.home / "Documents partages"
        self.idea = self.documents / "Ideas 2026"
        self.idea.mkdir(parents=True)
        self.config_home = self.home / ".config"
        self.config_home.mkdir(parents=True)
        (self.config_home / "user-dirs.dirs").write_text(
            'XDG_DOCUMENTS_DIR="$HOME/Documents partages"\n',
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _patches(self):
        return (
            patch.object(Path, "home", return_value=self.home),
            patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.config_home)}),
        )

    def test_natural_location_uses_xdg_and_parent_terms(self):
        note = self.idea / "Notes.py"
        note.write_text("print('idea')\n", encoding="utf-8")
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            resolution = path_search.resolve_existing(
                "notes py in my idea folder in documents", "file"
            )
        self.assertEqual(resolution.path, note)

    def test_find_files_resolves_known_folder_and_fuzzy_directory_name(self):
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            summary, _parts = artifacts.find_files(
                "documents", "idea", "directory"
            )
        self.assertIn(str(self.idea), summary)

    def test_output_path_resolves_localized_xdg_parent(self):
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            resolution = path_search.resolve_output(
                "Documents/ideas 2026/report.xlsx"
            )
        self.assertEqual(resolution.path, self.idea / "report.xlsx")

    def test_output_path_creates_new_tree_below_xdg_folder(self):
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            resolution = path_search.resolve_output(
                "Documents/New project/reports/report.pdf"
            )
        self.assertEqual(
            resolution.path,
            self.documents / "New project" / "reports" / "report.pdf",
        )

    def test_ambiguous_fuzzy_match_returns_candidates(self):
        (self.documents / "Idea Alpha").mkdir()
        (self.documents / "Idea Beta").mkdir()
        self.idea.rmdir()
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            resolution = path_search.resolve_existing(
                "idea folder in documents", "directory"
            )
        self.assertIsNone(resolution.path)
        self.assertIn("ambiguous", resolution.message)
        self.assertEqual(len(resolution.candidates), 2)

    def test_file_search_returns_multiple_close_and_typo_matches(self):
        first = self.documents / "Idea New"
        second = self.documents / "Idew 2027"
        first.mkdir()
        second.mkdir()
        self.idea.rmdir()
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            summary, _parts = artifacts.find_files("documents", "idea", "directory")
            resolution = path_search.resolve_existing(
                "idea folder in documents", "directory"
            )
        self.assertIn(str(first), summary)
        self.assertIn(str(second), summary)
        self.assertIsNone(resolution.path)
        self.assertEqual(set(resolution.candidates), {first, second})

    def test_wrong_parent_recovers_one_exact_filename_before_fuzzy_search(self):
        screenshot = self.idea / "Screenshot from 2026-08-19 22-19-07.png"
        screenshot.write_bytes(b"image")
        wrong = self.home / "wrong" / screenshot.name
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            resolution = path_search.resolve_existing(str(wrong), "file")
        self.assertEqual(resolution.path, screenshot)
        self.assertEqual(resolution.candidates, (screenshot,))

    def test_duplicate_exact_filenames_remain_ambiguous(self):
        first = self.idea / "report-final.pdf"
        second_folder = self.documents / "Archive"
        second_folder.mkdir()
        second = second_folder / first.name
        first.write_bytes(b"first")
        second.write_bytes(b"second")
        wrong = self.home / "wrong" / first.name
        home_patch, env_patch = self._patches()
        with home_patch, env_patch:
            resolution = path_search.resolve_existing(str(wrong), "file")
        self.assertIsNone(resolution.path)
        self.assertEqual(set(resolution.candidates), {first, second})
        self.assertIn("multiple locations", resolution.message)


class MediaGenerationDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temporary.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temporary.name) / "legacy.json"
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temporary.cleanup()

    def test_media_generation_model_is_saved_and_resolved(self):
        model = db.add_model(
            "Image model",
            "image-model",
            "OpenAI compatible",
            "https://images.example/v1",
            "$TEST_IMAGE_KEY",
        )
        with patch.dict("os.environ", {"TEST_IMAGE_KEY": "resolved-key"}):
            updated = db.update_builtin_agent("media", generation_model_id=model["id"])
            runtime = db.get_builtin_agent_generation_runtime("media")

        self.assertEqual(updated["generation_model_id"], model["id"])
        self.assertEqual(runtime["model"], "image-model")
        self.assertEqual(runtime["api_key"], "resolved-key")
        self.assertEqual(
            db.delete_model_result(model["id"]).status,
            "in_use",
        )
        cleared = db.update_builtin_agent("media", generation_model_id=None)
        self.assertIsNone(cleared["generation_model_id"])
        self.assertIsNone(db.get_builtin_agent_generation_runtime("media"))
        self.assertTrue(db.delete_model(model["id"]))


if __name__ == "__main__":
    unittest.main()
