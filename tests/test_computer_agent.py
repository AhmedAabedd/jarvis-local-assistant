from __future__ import annotations

import base64
import importlib.util
import os
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from mounir import db, graph_runtime, local_computer, tools
from mounir.specialists import computer


class ComputerConfigurationTests(unittest.TestCase):
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

    def test_computer_defaults_to_forty_tool_rounds(self):
        self.assertEqual(computer.MAX_TOOL_ROUNDS, 40)

    def test_builtin_round_limit_is_persisted_and_can_reset_to_code_default(self):
        initial = next(
            item for item in db.list_builtin_agents() if item["key"] == "computer"
        )
        self.assertEqual(initial["max_tool_rounds"], 40)
        self.assertEqual(initial["default_max_tool_rounds"], 40)

        saved = db.update_builtin_agent("computer", max_tool_rounds=25)
        self.assertEqual(saved["max_tool_rounds"], 25)
        self.assertEqual(db.get_builtin_max_tool_rounds("computer", 40), 25)

        reset = db.update_builtin_agent("computer", max_tool_rounds=None)
        self.assertEqual(reset["max_tool_rounds"], 40)
        self.assertEqual(db.get_builtin_max_tool_rounds("computer", 40), 40)

    def test_computer_exposes_only_native_tools_and_no_owned_server(self):
        unavailable = {
            "available": False,
            "backend": "unavailable",
            "reason": "PipeWire unavailable",
        }
        with patch.object(local_computer, "availability", return_value=unavailable):
            item = next(
                candidate
                for candidate in db.list_builtin_agents()
                if candidate["key"] == "computer"
            )

        self.assertEqual(
            [tool["name"] for tool in item["tools"]],
            list(local_computer.TOOL_NAMES),
        )
        self.assertIsNone(db.get_builtin_agent_server_spec("computer"))
        self.assertEqual(item["computer_backend"], "unavailable")
        self.assertIn("delegate_to_computer", [tool.name for tool in tools.DELEGATE_TOOLS])

    def test_unavailable_native_backend_does_not_offer_a_fallback(self):
        unavailable = {
            "available": False,
            "backend": "unavailable",
            "reason": "PipeWire sharing is unavailable",
        }
        with (
            patch.object(local_computer, "availability", return_value=unavailable),
            patch.object(computer.mounir_tools, "request_confirmation") as confirm,
        ):
            report = computer.run("Click the visible button")

        confirm.assert_not_called()
        self.assertIn("native desktop tools are unavailable", report)
        self.assertIn("PipeWire sharing is unavailable", report)
        self.assertNotIn("MCP", report)

    def test_computer_keeps_only_the_session_confirmation(self):
        db.update_builtin_agent("computer", confirm_tools=["*"])
        self.assertEqual(db.get_builtin_confirmation_tools("computer"), [])

        native = {
            "available": True,
            "backend": "local_x11",
            "reason": "",
            "screenshot_backend": "gnome-screenshot",
        }
        with (
            patch.object(local_computer, "availability", return_value=native),
            patch.object(local_computer, "prepare_control_session"),
            patch.object(local_computer, "release_control_session") as release,
            patch.object(computer, "_run_local", return_value="complete"),
            patch.object(
                computer.mounir_tools, "request_confirmation", return_value=True
            ) as confirm,
        ):
            report = computer.run("Complete the approved desktop task")

        self.assertEqual(report, "complete")
        confirm.assert_called_once()
        release.assert_called_once()


class LocalComputerToolTests(unittest.TestCase):
    def test_tool_catalog_is_the_small_native_control_surface(self):
        self.assertIs(computer.TOOLS, local_computer.TOOLS)
        self.assertEqual(
            [tool.name for tool in local_computer.TOOLS],
            list(local_computer.TOOL_NAMES),
        )
        self.assertEqual(len(local_computer.TOOLS), 12)

    def test_screenshot_coordinates_are_scaled_to_the_physical_display(self):
        class Driver:
            moved = None

            @contextmanager
            def display(self):
                yield object()

            def size(self, _display):
                return 2560, 1600

            def cursor(self, _display):
                return self.moved

            def smooth_move(self, _display, x, y, duration):
                self.moved = (x, y)

        driver = Driver()
        x11 = {"available": True, "backend": "local_x11", "reason": ""}
        with (
            patch.object(local_computer, "availability", return_value=x11),
            patch.object(local_computer, "_driver", return_value=driver),
        ):
            result = local_computer.mouse_move([320, 240], duration=0)

        self.assertEqual(driver.moved, (640, 480))
        self.assertIn("(320, 240)", result)

    def test_x11_screenshot_temporary_file_is_deleted(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "capture.png"

            def create_image(command, **_kwargs):
                Image.new("RGB", (1280, 800), "white").save(command[-1])
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            descriptor = os.open(path, os.O_CREAT | os.O_RDWR)
            available = {
                "available": True,
                "backend": "local_x11",
                "reason": "",
                "screenshot_backend": "gnome-screenshot",
            }
            with (
                patch.object(local_computer, "availability", return_value=available),
                patch.object(
                    local_computer.tempfile,
                    "mkstemp",
                    return_value=(descriptor, str(path)),
                ),
                patch.object(
                    local_computer.subprocess, "run", side_effect=create_image
                ),
            ):
                content, artifact = local_computer.screenshot()

            self.assertIsNone(artifact)
            self.assertEqual(content[0]["type"], "image_url")
            self.assertIn("1280x800", content[1]["text"])
            self.assertFalse(path.exists())

    def test_wayland_screenshot_uses_the_latest_screencast_frame(self):
        frame = base64.b64encode(b"jpeg frame").decode("ascii")
        available = {
            "available": True,
            "backend": "wayland_portal",
            "reason": "",
            "screenshot_backend": "pipewire_screencast",
        }
        with (
            patch.object(local_computer, "availability", return_value=available),
            patch.object(
                local_computer._PORTAL,
                "call",
                return_value={
                    "image_base64": frame,
                    "mime_type": "image/jpeg",
                    "width": 1280,
                    "height": 800,
                    "source_width": 2560,
                    "source_height": 1600,
                },
            ) as portal,
            patch.object(local_computer, "_capture_screen_image") as command_capture,
        ):
            content, artifact = local_computer.screenshot()

        self.assertIsNone(artifact)
        self.assertEqual(
            portal.call_args,
            unittest.mock.call("screenshot", width=1280, quality=85),
        )
        command_capture.assert_not_called()
        self.assertIn(frame, content[0]["image_url"]["url"])
        self.assertIn("captured from the Wayland ScreenCast stream", content[1]["text"])

    def test_broken_wayland_stream_stops_without_command_fallback(self):
        available = {
            "available": True,
            "backend": "wayland_portal",
            "reason": "",
            "screenshot_backend": "pipewire_screencast",
        }
        with (
            patch.object(local_computer, "availability", return_value=available),
            patch.object(
                local_computer._PORTAL,
                "call",
                side_effect=local_computer.LocalComputerError("PipeWire unavailable"),
            ),
            patch.object(local_computer, "_capture_screen_image") as command_capture,
        ):
            with self.assertRaisesRegex(
                local_computer.LocalComputerError,
                "Computer task cannot continue",
            ):
                local_computer.screenshot()

        command_capture.assert_not_called()

    def test_native_agent_blocks_an_identical_mutation(self):
        def fake_agent(_messages, runtime_tools, _model_call, **_kwargs):
            move = next(tool for tool in runtime_tools if tool.name == "mouse_move")
            first = move.invoke({"coordinate": [10, 20], "duration": 0})
            second = move.invoke({"coordinate": [10, 20], "duration": 0})
            self.assertNotIn("Duplicate", str(first))
            self.assertIn("Duplicate protected action blocked", str(second))
            return "guarded"

        move = next(tool for tool in local_computer.TOOLS if tool.name == "mouse_move")
        original = move.func
        move.func = lambda **_arguments: "moved"
        try:
            with (
                patch.object(computer.graph_runtime, "run_tool_agent", fake_agent),
                patch.object(
                    computer.agent_skills, "runtime_access", return_value=("", None)
                ),
                patch.object(
                    local_computer,
                    "availability",
                    return_value={"available": True, "backend": "local_x11"},
                ),
            ):
                report = computer._run_local(
                    "Move once",
                    {
                        "model": "test",
                        "provider": "OpenAI-compatible",
                        "base_url": "http://localhost/v1",
                        "api_key": "",
                    },
                    ["mouse_move"],
                    [],
                )
        finally:
            move.func = original

        self.assertEqual(report, "guarded")

    def test_wayland_uses_the_compositor_portal_not_xtest(self):
        wayland = {
            "available": True,
            "backend": "wayland_portal",
            "reason": "",
            "screenshot_backend": "pipewire_screencast",
        }
        with (
            patch.object(local_computer, "availability", return_value=wayland),
            patch.object(local_computer, "_tool_size", return_value=(1024, 640)),
            patch.object(local_computer._PORTAL, "call", return_value={}) as portal,
        ):
            moved = local_computer.mouse_move([400, 250], duration=0.8)
            clicked = local_computer.left_click([22, 12])

        self.assertIn("real Wayland cursor", moved)
        self.assertIn("Wayland compositor", clicked)
        self.assertEqual(portal.call_args_list[0].args, ("move",))
        self.assertEqual(portal.call_args_list[0].kwargs["x"], 400)

    def test_wayland_animation_tracks_its_origin_in_stream_coordinates(self):
        fake_dbus = types.ModuleType("dbus")
        fake_dbus.UInt32 = int
        fake_dbus.Double = float
        fake_mainloop = types.ModuleType("dbus.mainloop")
        fake_glib_mainloop = types.ModuleType("dbus.mainloop.glib")
        fake_glib_mainloop.DBusGMainLoop = lambda **_kwargs: None
        fake_gi = types.ModuleType("gi")
        fake_repository = types.ModuleType("gi.repository")
        fake_repository.GLib = SimpleNamespace()
        helper_path = Path(local_computer.__file__).with_name(
            "wayland_portal_helper.py"
        )

        with patch.dict(
            sys.modules,
            {
                "dbus": fake_dbus,
                "dbus.mainloop": fake_mainloop,
                "dbus.mainloop.glib": fake_glib_mainloop,
                "gi": fake_gi,
                "gi.repository": fake_repository,
            },
        ):
            spec = importlib.util.spec_from_file_location(
                "mounir_test_wayland_portal_helper", helper_path
            )
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)

        points = []

        class Remote:
            def NotifyPointerMotionAbsolute(
                self, _session, _options, _stream, x, y
            ):
                points.append((float(x), float(y)))

        controller = helper.PortalController.__new__(helper.PortalController)
        controller.session = object()
        controller.stream_id = 7
        controller.stream_size = (2560, 1600)
        controller.capture_pipeline = None
        controller.last_stream_position = (800.0, 500.0)
        controller.last_position = (400.0, 250.0)
        controller.remote = Remote()
        controller.start = lambda: None
        controller._empty_options = lambda: {}

        with patch.object(helper.time, "sleep"):
            result = controller.move(600, 400, 1280, 800, duration=0.8)

        self.assertGreater(points[0][0], 800.0)
        self.assertEqual(points[-1], (1200.0, 800.0))
        self.assertEqual(controller.last_stream_position, (1200.0, 800.0))
        self.assertEqual(result["last_position"], [600.0, 400.0])

    def test_wayland_availability_requires_pipewire_screencast(self):
        with (
            patch.object(local_computer.sys, "platform", "linux"),
            patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}),
            patch.object(
                local_computer,
                "_portal_probe",
                return_value={
                    "available": True,
                    "version": 2,
                    "screencast_available": False,
                    "missing_capture_components": ["pipewiresrc"],
                },
            ),
        ):
            status = local_computer.availability()

        self.assertFalse(status["available"])
        self.assertEqual(status["backend"], "unavailable")
        self.assertIn("pipewiresrc", status["reason"])

    def test_wayland_screencast_needs_no_screenshot_command(self):
        with (
            patch.object(local_computer.sys, "platform", "linux"),
            patch.dict(os.environ, {"DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"}),
            patch.object(local_computer.shutil, "which", return_value=None),
            patch.object(
                local_computer,
                "_portal_probe",
                return_value={
                    "available": True,
                    "version": 2,
                    "screencast_version": 5,
                    "screencast_available": True,
                    "missing_capture_components": [],
                },
            ),
        ):
            status = local_computer.availability()

        self.assertTrue(status["available"])
        self.assertEqual(status["backend"], "wayland_portal")
        self.assertEqual(status["screenshot_backend"], "pipewire_screencast")


class ComputerMediaTests(unittest.TestCase):
    def test_only_the_latest_screenshot_reaches_the_next_model_turn(self):
        messages = [
            {
                "role": "tool",
                "content": [{"type": "image_url", "image_url": {"url": "old"}}],
            },
            {"role": "assistant", "content": "acted"},
            {
                "role": "tool",
                "content": [{"type": "image_url", "image_url": {"url": "new"}}],
            },
        ]
        pruned = computer._latest_visual_history(messages)
        self.assertNotEqual(pruned[0]["content"][0].get("type"), "image_url")
        self.assertEqual(pruned[2]["content"][0]["image_url"]["url"], "new")

    def test_debug_trace_omits_screenshot_bytes(self):
        message = ToolMessage(
            name="screenshot",
            tool_call_id="call_1",
            content=[
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,SECRET"},
                },
                {"type": "text", "text": "320x200"},
            ],
        )
        with patch("mounir.trace.tool") as traced:
            graph_runtime.trace_tool_messages([message])
        rendered = str(traced.call_args.args[2])
        self.assertNotIn("SECRET", rendered)
        self.assertIn("visual result", rendered)


if __name__ == "__main__":
    unittest.main()
