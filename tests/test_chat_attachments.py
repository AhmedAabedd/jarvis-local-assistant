from __future__ import annotations

import io
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mounir import chat_attachments, config, llm
from mounir.memory import Conversation


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (12, 8), (20, 80, 140)).save(output, format="PNG")
    return output.getvalue()


class ChatAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.directory_patch = patch.object(
            config, "CHAT_ATTACHMENT_DIR", self.root / "attachments"
        )
        self.directory_patch.start()

    def tearDown(self):
        self.directory_patch.stop()
        self.temporary.cleanup()

    def test_image_is_validated_stored_privately_and_resolved_by_opaque_id(self):
        record = chat_attachments.save(_png_bytes(), "../../My screenshot.PNG")
        resolved = chat_attachments.resolve(record["id"])
        path = Path(resolved["path"])

        self.assertEqual(record["filename"], "My screenshot.png")
        self.assertEqual(record["mime_type"], "image/png")
        self.assertEqual(path.parent, (self.root / "attachments").resolve())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(path.read_bytes(), _png_bytes())
        self.assertNotIn("path", chat_attachments.public(resolved))

        with self.assertRaisesRegex(ValueError, "Invalid chat attachment"):
            chat_attachments.resolve("../../secret")
        with self.assertRaisesRegex(ValueError, "readable image"):
            chat_attachments.save(b"not an image", "fake.png")

    def test_conversation_rehydrates_pixels_and_keeps_them_for_follow_up_turns(self):
        record = chat_attachments.save(_png_bytes(), "portrait.png")
        conversation = Conversation(system_prompt="test")
        conversation.add_user("Who is this?", attachments=[record])
        conversation.add_assistant("A person.")
        conversation.add_user("What about the nose?")

        messages = conversation.to_messages()
        attached = next(
            message
            for message in messages
            if message.get("role") == "user"
            and isinstance(message.get("content"), list)
        )
        image = attached["content"][1]["image_url"]["url"]
        self.assertTrue(image.startswith("data:image/png;base64,"))
        self.assertIn("Who is this?", attached["content"][0]["text"])
        self.assertNotIn("base64", str(conversation._messages))
        self.assertEqual(
            conversation.display_messages()[0]["attachments"][0]["filename"],
            "portrait.png",
        )

    def test_mistral_wire_format_uses_its_documented_image_url_shape(self):
        content = [
            {"type": "text", "text": "Inspect this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
            },
        ]
        message = [{"role": "user", "content": content}]

        generic = llm._compatible_messages(message, "OpenAI-compatible")
        mistral = llm._compatible_messages(message, "Mistral")

        self.assertEqual(generic[0]["content"][1]["image_url"]["url"], "data:image/png;base64,AAAA")
        self.assertEqual(mistral[0]["content"][1]["image_url"], "data:image/png;base64,AAAA")

    def test_tool_images_are_promoted_to_one_portable_user_message(self):
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "function": {"name": "load_media", "arguments": {"path": "a.jpg"}},
                    },
                    {
                        "id": "call_b",
                        "function": {"name": "load_media", "arguments": {"path": "b.jpg"}},
                    },
                ],
            },
            {
                "role": "tool",
                "name": "load_media",
                "tool_call_id": "call_a",
                "content": [
                    {"type": "text", "text": "Loaded A."},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
                ],
            },
            {
                "role": "tool",
                "name": "load_media",
                "tool_call_id": "call_b",
                "content": [
                    {"type": "text", "text": "Loaded B."},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBB"}},
                ],
            },
        ]

        compatible = llm._compatible_messages(messages, "OpenAI-compatible")

        self.assertEqual([item["role"] for item in compatible], ["assistant", "tool", "tool", "user"])
        self.assertEqual(compatible[1]["content"], "Loaded A.")
        self.assertEqual(compatible[2]["content"], "Loaded B.")
        visual_content = compatible[3]["content"]
        self.assertEqual([part["type"] for part in visual_content], ["text", "image_url", "image_url"])
        self.assertEqual(visual_content[1]["image_url"]["url"], "data:image/jpeg;base64,AAA")
        self.assertEqual(visual_content[2]["image_url"]["url"], "data:image/jpeg;base64,BBB")

    def test_mistral_adaptation_also_applies_to_promoted_tool_images(self):
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_a",
                "content": [
                    {"type": "text", "text": "Loaded."},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ]

        compatible = llm._compatible_messages(messages, "Mistral")

        self.assertEqual(compatible[0]["content"], "Loaded.")
        self.assertEqual(compatible[1]["role"], "user")
        self.assertEqual(compatible[1]["content"][1]["image_url"], "data:image/png;base64,AAAA")

if __name__ == "__main__":
    unittest.main()
