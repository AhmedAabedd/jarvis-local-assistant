"""Persistent GNOME/Wayland RemoteDesktop and ScreenCast portal bridge.

This helper intentionally runs with the distribution Python because Ubuntu's
``python3-dbus`` and PyGObject packages belong to that interpreter. It accepts
one JSON object per stdin line and writes one JSON response per stdout line.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

BUS_NAME = "org.freedesktop.portal.Desktop"
DESKTOP_PATH = "/org/freedesktop/portal/desktop"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"
REMOTE_INTERFACE = "org.freedesktop.portal.RemoteDesktop"
SCREENCAST_INTERFACE = "org.freedesktop.portal.ScreenCast"
KEYBOARD_AND_POINTER = 3
MONITOR_SOURCE = 1
CURSOR_EMBEDDED = 2


def _plain(value):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (dbus.Boolean, bool)):
        return bool(value)
    if isinstance(value, (dbus.Int16, dbus.Int32, dbus.Int64,
                          dbus.UInt16, dbus.UInt32, dbus.UInt64, int)):
        return int(value)
    if isinstance(value, (dbus.Double, float)):
        return float(value)
    return str(value)


class PortalController:
    def __init__(self, token_path: str = ""):
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SessionBus()
        desktop = self.bus.get_object(BUS_NAME, DESKTOP_PATH)
        self.remote = dbus.Interface(desktop, REMOTE_INTERFACE)
        self.screencast = dbus.Interface(desktop, SCREENCAST_INTERFACE)
        self.sender = self.bus.get_unique_name().lstrip(":").replace(".", "_")
        self.token_path = Path(token_path) if token_path else None
        self.session = None
        self.stream_id = None
        self.stream_size = None
        self.stream_properties = {}
        self.pipewire_fd = None
        self.capture_pipeline = None
        self.capture_sink = None
        self.capture_width = None
        self.capture_quality = None
        self.Gst = None
        # Keep the animation origin in the portal stream's coordinate space.
        # ``last_position`` remains in Mounir's screenshot coordinate space for
        # status responses consumed by the parent process.
        self.last_stream_position = None
        self.last_position = None

    def _request(self, call, options: dict, timeout: int = 150) -> dict:
        token = "mounir_" + uuid.uuid4().hex
        options = dict(options)
        options["handle_token"] = dbus.String(token)
        expected = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        response = {}
        loop = GLib.MainLoop()
        proxy = self.bus.get_object(BUS_NAME, expected)

        def complete(code, results):
            response["code"] = int(code)
            response["results"] = dict(results)
            loop.quit()

        signal = proxy.connect_to_signal(
            "Response", complete, dbus_interface=REQUEST_INTERFACE
        )
        actual = str(call(options))
        if actual != expected:
            signal.remove()
            proxy = self.bus.get_object(BUS_NAME, actual)
            signal = proxy.connect_to_signal(
                "Response", complete, dbus_interface=REQUEST_INTERFACE
            )

        def timed_out():
            response["timeout"] = True
            loop.quit()
            return False

        timer = GLib.timeout_add_seconds(timeout, timed_out)
        loop.run()
        if not response.get("timeout"):
            GLib.source_remove(timer)
        signal.remove()
        if response.get("timeout"):
            raise RuntimeError("GNOME Remote Desktop permission timed out")
        if response.get("code") != 0:
            raise RuntimeError(
                f"GNOME Remote Desktop permission was declined ({response.get('code')})"
            )
        return response["results"]

    def _restore_token(self) -> str:
        if not self.token_path:
            return ""
        try:
            return self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _save_restore_token(self, token: str) -> None:
        if not token or not self.token_path:
            return
        self.token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.token_path.with_name(self.token_path.name + ".tmp")
        temporary.write_text(token, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.token_path)

    def start(self) -> dict:
        if self.session is not None:
            return self.info()
        created = self._request(
            lambda options: self.remote.CreateSession(options),
            {
                "session_handle_token": dbus.String(
                    "mounir_session_" + uuid.uuid4().hex
                )
            },
        )
        self.session = dbus.ObjectPath(str(created["session_handle"]))
        device_options = {
            "types": dbus.UInt32(KEYBOARD_AND_POINTER),
            "persist_mode": dbus.UInt32(2),
        }
        restore_token = self._restore_token()
        if restore_token:
            device_options["restore_token"] = dbus.String(restore_token)
        try:
            self._request(
                lambda options: self.remote.SelectDevices(self.session, options),
                device_options,
            )
            self._request(
                lambda options: self.screencast.SelectSources(
                    self.session, options
                ),
                {
                    "types": dbus.UInt32(MONITOR_SOURCE),
                    "multiple": dbus.Boolean(False),
                    "cursor_mode": dbus.UInt32(CURSOR_EMBEDDED),
                },
            )
            started = self._request(
                lambda options: self.remote.Start(self.session, "", options), {}
            )
            devices = int(started.get("devices", 0))
            if devices & KEYBOARD_AND_POINTER != KEYBOARD_AND_POINTER:
                raise RuntimeError("GNOME did not grant keyboard and pointer control")
            streams = list(started.get("streams", []))
            if not streams:
                raise RuntimeError("GNOME returned no screen for absolute pointer control")
            self.stream_id = int(streams[0][0])
            properties = dict(streams[0][1])
            size = properties.get("size")
            if not size or len(size) != 2:
                raise RuntimeError("GNOME did not report the controlled screen size")
            self.stream_size = (int(size[0]), int(size[1]))
            self.stream_properties = properties
            self._save_restore_token(str(started.get("restore_token", "")))
            return self.info()
        except Exception:
            self.close()
            raise

    def info(self) -> dict:
        return {
            "started": self.session is not None,
            "stream_id": self.stream_id,
            "stream_size": list(self.stream_size or ()),
            "last_position": list(self.last_position) if self.last_position else None,
            "capture_started": self.capture_pipeline is not None,
        }

    def close(self) -> dict:
        self._close_capture()
        if self.session is not None:
            try:
                session = self.bus.get_object(BUS_NAME, self.session)
                dbus.Interface(session, SESSION_INTERFACE).Close()
            except Exception:
                pass
        self.session = None
        self.stream_id = None
        self.stream_size = None
        self.stream_properties = {}
        self.last_stream_position = None
        self.last_position = None
        return {"closed": True}

    def _close_capture(self) -> None:
        if self.capture_pipeline is not None and self.Gst is not None:
            try:
                self.capture_pipeline.set_state(self.Gst.State.NULL)
            except Exception:
                pass
        self.capture_pipeline = None
        self.capture_sink = None
        self.capture_width = None
        self.capture_quality = None
        self.Gst = None
        if self.pipewire_fd is not None:
            try:
                os.close(self.pipewire_fd)
            except OSError:
                pass
        self.pipewire_fd = None

    def _start_capture(self, width: int, quality: int) -> None:
        self.start()
        requested_width = max(640, min(int(width), 2560))
        requested_quality = max(70, min(int(quality), 95))
        output_width = min(requested_width, int(self.stream_size[0]))
        if (
            self.capture_pipeline is not None
            and self.capture_width == output_width
            and self.capture_quality == requested_quality
        ):
            return
        self._close_capture()

        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        pipewire_fd = None
        pipeline = None
        try:
            remote_fd = self.screencast.OpenPipeWireRemote(
                self.session, self._empty_options()
            )
            pipewire_fd = (
                int(remote_fd.take())
                if hasattr(remote_fd, "take")
                else int(remote_fd)
            )
            pipeline = Gst.parse_launch(
                "pipewiresrc name=source do-timestamp=true ! "
                "videoconvert ! videoscale ! "
                f"video/x-raw,width={output_width},pixel-aspect-ratio=1/1 ! "
                f"jpegenc quality={requested_quality} ! "
                "appsink name=sink sync=false max-buffers=1 drop=true"
            )
            source = pipeline.get_by_name("source")
            sink = pipeline.get_by_name("sink")
            if source is None or sink is None:
                raise RuntimeError(
                    "GStreamer did not create the screen capture pipeline"
                )
            source.set_property("fd", pipewire_fd)
            serial = self.stream_properties.get("pipewire-serial")
            if serial is not None and source.find_property("target-object") is not None:
                source.set_property("target-object", str(int(serial)))
            else:
                source.set_property("path", str(self.stream_id))
            state = pipeline.set_state(Gst.State.PLAYING)
            if state == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError(
                    "GStreamer could not start the PipeWire screen capture"
                )
        except Exception:
            if pipeline is not None:
                pipeline.set_state(Gst.State.NULL)
            if pipewire_fd is not None:
                os.close(pipewire_fd)
            raise
        self.Gst = Gst
        self.pipewire_fd = pipewire_fd
        self.capture_pipeline = pipeline
        self.capture_sink = sink
        self.capture_width = output_width
        self.capture_quality = requested_quality

    def screenshot(self, width: int = 1280, quality: int = 85) -> dict:
        """Return the newest completed ScreenCast frame as an in-memory JPEG."""
        self._start_capture(width, quality)
        sample = self.capture_sink.emit("try-pull-sample", 5 * self.Gst.SECOND)
        if sample is None:
            message = self.capture_pipeline.get_bus().pop_filtered(
                self.Gst.MessageType.ERROR
            )
            if message is not None:
                error, debug = message.parse_error()
                detail = str(error)
                if debug:
                    detail += f" ({debug})"
                raise RuntimeError(detail)
            raise RuntimeError("Timed out waiting for a PipeWire screen frame")
        buffer = sample.get_buffer()
        if buffer is None:
            raise RuntimeError("PipeWire returned an empty screen frame")
        mapped, mapping = buffer.map(self.Gst.MapFlags.READ)
        if not mapped:
            raise RuntimeError("Could not read the PipeWire screen frame")
        try:
            frame = bytes(mapping.data)
        finally:
            buffer.unmap(mapping)
        if not frame:
            raise RuntimeError("PipeWire returned an empty screen frame")
        caps = sample.get_caps()
        structure = caps.get_structure(0) if caps and caps.get_size() else None
        frame_width = int(structure.get_value("width")) if structure else 0
        frame_height = int(structure.get_value("height")) if structure else 0
        return {
            "image_base64": base64.b64encode(frame).decode("ascii"),
            "mime_type": "image/jpeg",
            "width": frame_width,
            "height": frame_height,
            "source_width": int(self.stream_size[0]),
            "source_height": int(self.stream_size[1]),
        }

    def _empty_options(self):
        return dbus.Dictionary({}, signature="sv")

    def move(self, x: float, y: float, width: int, height: int,
             duration: float = 0.8) -> dict:
        self.start()
        stream_width, stream_height = self.stream_size
        target_x = min(stream_width - 1, max(0.0, float(x) * stream_width / width))
        target_y = min(stream_height - 1, max(0.0, float(y) * stream_height / height))
        start_x, start_y = self.last_stream_position or (target_x, target_y)
        seconds = max(0.0, min(float(duration), 2.0))
        frames = max(1, min(120, round(max(0.05, seconds) * 60)))
        for index in range(1, frames + 1):
            progress = index / frames
            eased = 1 - (1 - progress) ** 3
            next_x = start_x + (target_x - start_x) * eased
            next_y = start_y + (target_y - start_y) * eased
            self.remote.NotifyPointerMotionAbsolute(
                self.session,
                self._empty_options(),
                dbus.UInt32(self.stream_id),
                dbus.Double(next_x),
                dbus.Double(next_y),
            )
            if seconds:
                time.sleep(seconds / frames)
        self.last_stream_position = (target_x, target_y)
        self.last_position = (float(x), float(y))
        return self.info()

    def button(self, button: int, pressed: bool) -> dict:
        self.start()
        self.remote.NotifyPointerButton(
            self.session,
            self._empty_options(),
            dbus.Int32(int(button)),
            dbus.UInt32(1 if pressed else 0),
        )
        return {"button": int(button), "pressed": bool(pressed)}

    def scroll(self, axis: int, steps: int) -> dict:
        self.start()
        self.remote.NotifyPointerAxisDiscrete(
            self.session,
            self._empty_options(),
            dbus.UInt32(int(axis)),
            dbus.Int32(int(steps)),
        )
        return {"axis": int(axis), "steps": int(steps)}

    def keysym(self, keysym: int, pressed: bool) -> dict:
        self.start()
        self.remote.NotifyKeyboardKeysym(
            self.session,
            self._empty_options(),
            dbus.Int32(int(keysym)),
            dbus.UInt32(1 if pressed else 0),
        )
        return {"keysym": int(keysym), "pressed": bool(pressed)}

    def dispatch(self, command: dict) -> dict:
        action = str(command.get("action") or "")
        if action == "start":
            return self.start()
        if action == "status":
            return self.info()
        if action == "screenshot":
            return self.screenshot(
                command.get("width", 1280), command.get("quality", 85)
            )
        if action == "close":
            return self.close()
        if action == "move":
            return self.move(
                command["x"], command["y"], command["width"], command["height"],
                command.get("duration", 0.8),
            )
        if action == "button":
            return self.button(command["button"], command["pressed"])
        if action == "scroll":
            return self.scroll(command["axis"], command["steps"])
        if action == "keysym":
            return self.keysym(command["keysym"], command["pressed"])
        raise ValueError(f"unknown portal action: {action}")


def probe() -> int:
    try:
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        desktop = bus.get_object(BUS_NAME, DESKTOP_PATH)
        properties = dbus.Interface(desktop, "org.freedesktop.DBus.Properties")
        version = int(properties.Get(REMOTE_INTERFACE, "version"))
        devices = int(properties.Get(REMOTE_INTERFACE, "AvailableDeviceTypes"))
        screencast_version = int(properties.Get(SCREENCAST_INTERFACE, "version"))
        missing_elements = []
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst

            Gst.init(None)
            missing_elements = [
                name
                for name in (
                    "pipewiresrc",
                    "videoconvert",
                    "videoscale",
                    "jpegenc",
                    "appsink",
                )
                if Gst.ElementFactory.find(name) is None
            ]
        except Exception as exc:
            missing_elements = [f"GStreamer ({exc})"]
        print(json.dumps({
            "available": version >= 1 and devices & KEYBOARD_AND_POINTER == KEYBOARD_AND_POINTER,
            "version": version,
            "devices": devices,
            "screencast_version": screencast_version,
            "screencast_available": screencast_version >= 1 and not missing_elements,
            "missing_capture_components": missing_elements,
        }))
        return 0
    except Exception as exc:
        print(json.dumps({"available": False, "reason": str(exc)}))
        return 1


def main() -> int:
    if "--probe" in sys.argv:
        return probe()
    token_path = sys.argv[1] if len(sys.argv) > 1 else ""
    controller = PortalController(token_path)
    try:
        for line in sys.stdin:
            try:
                result = controller.dispatch(json.loads(line))
                payload = {"ok": True, "result": _plain(result)}
            except Exception as exc:
                payload = {"ok": False, "error": str(exc)}
            print(json.dumps(payload, ensure_ascii=False), flush=True)
    finally:
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
