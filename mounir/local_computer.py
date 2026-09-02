"""Small native Linux desktop toolset for the built-in Computer specialist.

This backend uses XTest plus an installed screenshot command on X11, and the
XDG RemoteDesktop and ScreenCast portals with PipeWire on Wayland.
"""

from __future__ import annotations

import base64
import atexit
import ctypes
import ctypes.util
import io
import json
import math
import os
import shutil
import select
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Iterator

from langchain_core.tools import StructuredTool

from . import config

SCREENSHOT_TIMEOUT_SECONDS = 20
SCREENSHOT_MAX_BYTES = 20 * 1024 * 1024
SCREENSHOT_WIDTH = max(
    640, min(int(os.environ.get("MOUNIR_COMPUTER_SCREENSHOT_WIDTH", "1280")), 2560)
)
SCREENSHOT_JPEG_QUALITY = 85
POINTER_MOVE_SECONDS = max(
    0.0,
    min(float(os.environ.get("MOUNIR_COMPUTER_POINTER_MOVE_SECONDS", "0.8")), 2.0),
)
POINTER_MOVE_FPS = 60
PORTAL_HELPER = Path(__file__).with_name("wayland_portal_helper.py")
PORTAL_TOKEN_PATH = config.DATA_DIR / "computer" / "wayland-restore-token"
PORTAL_TIMEOUT_SECONDS = 180

READ_ONLY_TOOL_NAMES = (
    "screenshot",
    "cursor_position",
    "get_display_size",
    "wait",
)
MUTATING_TOOL_NAMES = (
    "mouse_move",
    "left_click",
    "right_click",
    "double_click",
    "left_click_drag",
    "scroll",
    "type",
    "key",
)
TOOL_NAMES = (*READ_ONLY_TOOL_NAMES, *MUTATING_TOOL_NAMES)

_KEYSYMS = {
    "backspace": 0xFF08,
    "tab": 0xFF09,
    "return": 0xFF0D,
    "enter": 0xFF0D,
    "escape": 0xFF1B,
    "esc": 0xFF1B,
    "home": 0xFF50,
    "left": 0xFF51,
    "up": 0xFF52,
    "right": 0xFF53,
    "down": 0xFF54,
    "pageup": 0xFF55,
    "pagedown": 0xFF56,
    "end": 0xFF57,
    "delete": 0xFFFF,
    "space": 0x20,
}
_MODIFIERS = {
    "shift": 0xFFE1,
    "ctrl": 0xFFE3,
    "control": 0xFFE3,
    "alt": 0xFFE9,
    "super": 0xFFEB,
    "meta": 0xFFEB,
}


class LocalComputerError(RuntimeError):
    pass


def _system_python() -> str:
    configured = os.environ.get("MOUNIR_SYSTEM_PYTHON", "").strip()
    if configured:
        return configured
    return "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else "python3"


def _portal_probe() -> dict:
    try:
        process = subprocess.run(
            [_system_python(), str(PORTAL_HELPER), "--probe"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        payload = json.loads((process.stdout or "").strip() or "{}")
        if process.returncode or not payload.get("available"):
            return {
                "available": False,
                "reason": str(payload.get("reason") or process.stderr or "portal unavailable"),
            }
        return payload
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


class _PortalClient:
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.lock = threading.Lock()

    def _start(self) -> subprocess.Popen:
        if self.process is not None and self.process.poll() is None:
            return self.process
        PORTAL_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.process = subprocess.Popen(
            [_system_python(), "-u", str(PORTAL_HELPER), str(PORTAL_TOKEN_PATH)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return self.process

    def call(self, action: str, **arguments) -> dict:
        with self.lock:
            process = self._start()
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(
                json.dumps({"action": action, **arguments}, ensure_ascii=False) + "\n"
            )
            process.stdin.flush()
            readable, _, _ = select.select(
                [process.stdout], [], [], PORTAL_TIMEOUT_SECONDS
            )
            if not readable:
                self.stop()
                raise LocalComputerError(
                    "GNOME Remote Desktop permission timed out. Approve the desktop "
                    "control dialog on the computer and try again."
                )
            line = process.stdout.readline()
            if not line:
                detail = ""
                if process.stderr is not None:
                    detail = process.stderr.read().strip()
                self.stop()
                raise LocalComputerError(detail or "Wayland portal helper stopped")
            payload = json.loads(line)
            if not payload.get("ok"):
                raise LocalComputerError(str(payload.get("error") or "portal action failed"))
            return dict(payload.get("result") or {})

    def stop(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except Exception:
            process.kill()


_PORTAL = _PortalClient()
atexit.register(_PORTAL.stop)


class _X11:
    def __init__(self):
        x11_name = ctypes.util.find_library("X11")
        xtst_name = ctypes.util.find_library("Xtst")
        if not x11_name or not xtst_name:
            raise LocalComputerError("libX11 and libXtst are required")
        self.x11 = ctypes.CDLL(x11_name)
        self.xtst = ctypes.CDLL(xtst_name)
        self._configure()

    def _configure(self) -> None:
        display = ctypes.c_void_p
        window = ctypes.c_ulong
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XOpenDisplay.restype = display
        self.x11.XCloseDisplay.argtypes = [display]
        self.x11.XDefaultScreen.argtypes = [display]
        self.x11.XDefaultScreen.restype = ctypes.c_int
        self.x11.XDisplayWidth.argtypes = [display, ctypes.c_int]
        self.x11.XDisplayWidth.restype = ctypes.c_int
        self.x11.XDisplayHeight.argtypes = [display, ctypes.c_int]
        self.x11.XDisplayHeight.restype = ctypes.c_int
        self.x11.XDefaultRootWindow.argtypes = [display]
        self.x11.XDefaultRootWindow.restype = window
        self.x11.XQueryPointer.argtypes = [
            display,
            window,
            ctypes.POINTER(window),
            ctypes.POINTER(window),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint),
        ]
        self.x11.XQueryPointer.restype = ctypes.c_int
        self.x11.XWarpPointer.argtypes = [
            display,
            window,
            window,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self.x11.XFlush.argtypes = [display]
        self.x11.XSync.argtypes = [display, ctypes.c_int]
        self.x11.XKeysymToKeycode.argtypes = [display, ctypes.c_ulong]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XkbKeycodeToKeysym.argtypes = [
            display,
            ctypes.c_ubyte,
            ctypes.c_int,
            ctypes.c_int,
        ]
        self.x11.XkbKeycodeToKeysym.restype = ctypes.c_ulong
        self.xtst.XTestFakeButtonEvent.argtypes = [
            display,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeButtonEvent.restype = ctypes.c_int
        self.xtst.XTestFakeKeyEvent.argtypes = [
            display,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        self.xtst.XTestFakeKeyEvent.restype = ctypes.c_int

    @contextmanager
    def display(self) -> Iterator[ctypes.c_void_p]:
        handle = self.x11.XOpenDisplay(None)
        if not handle:
            raise LocalComputerError(
                f"Cannot open X display {os.environ.get('DISPLAY') or '(unset)'}"
            )
        try:
            yield handle
        finally:
            self.x11.XCloseDisplay(handle)

    def size(self, display) -> tuple[int, int]:
        screen = self.x11.XDefaultScreen(display)
        return (
            int(self.x11.XDisplayWidth(display, screen)),
            int(self.x11.XDisplayHeight(display, screen)),
        )

    def cursor(self, display) -> tuple[int, int]:
        root = self.x11.XDefaultRootWindow(display)
        root_return = ctypes.c_ulong()
        child_return = ctypes.c_ulong()
        root_x = ctypes.c_int()
        root_y = ctypes.c_int()
        window_x = ctypes.c_int()
        window_y = ctypes.c_int()
        mask = ctypes.c_uint()
        if not self.x11.XQueryPointer(
            display,
            root,
            ctypes.byref(root_return),
            ctypes.byref(child_return),
            ctypes.byref(root_x),
            ctypes.byref(root_y),
            ctypes.byref(window_x),
            ctypes.byref(window_y),
            ctypes.byref(mask),
        ):
            raise LocalComputerError("Could not read the current cursor position")
        return root_x.value, root_y.value

    def validate(self, display, x: int, y: int) -> None:
        width, height = self.size(display)
        if x < 0 or y < 0 or x >= width or y >= height:
            raise LocalComputerError(
                f"Coordinates ({x}, {y}) are outside {width}x{height}"
            )

    def warp(self, display, x: int, y: int) -> None:
        self.validate(display, x, y)
        root = self.x11.XDefaultRootWindow(display)
        self.x11.XWarpPointer(display, 0, root, 0, 0, 0, 0, x, y)
        self.x11.XSync(display, 0)

    def smooth_move(self, display, x: int, y: int, duration: float) -> None:
        self.validate(display, x, y)
        start_x, start_y = self.cursor(display)
        seconds = max(0.0, min(float(duration), 2.0))
        frames = max(1, min(120, math.ceil(seconds * POINTER_MOVE_FPS)))
        for index in range(1, frames + 1):
            progress = index / frames
            eased = 1 - (1 - progress) ** 3
            next_x = round(start_x + (x - start_x) * eased)
            next_y = round(start_y + (y - start_y) * eased)
            self.warp(display, next_x, next_y)
            if seconds:
                time.sleep(seconds / frames)

    def button(self, display, number: int, pressed: bool) -> None:
        if not self.xtst.XTestFakeButtonEvent(display, number, int(pressed), 0):
            raise LocalComputerError("XTest rejected the mouse button event")
        self.x11.XSync(display, 0)

    def click(self, display, button: int, count: int) -> None:
        for index in range(count):
            self.button(display, button, True)
            self.button(display, button, False)
            if index + 1 < count:
                time.sleep(0.09)

    def key_event(self, display, keycode: int, pressed: bool) -> None:
        if not keycode or not self.xtst.XTestFakeKeyEvent(
            display, keycode, int(pressed), 0
        ):
            raise LocalComputerError("XTest rejected the keyboard event")
        self.x11.XSync(display, 0)

    def keycode(self, display, keysym: int) -> int:
        return int(self.x11.XKeysymToKeycode(display, keysym))

    def character_key(self, display, character: str) -> tuple[int, bool]:
        keysym = ord(character)
        if keysym > 0xFF:
            keysym = 0x01000000 | keysym
        for keycode in range(8, 256):
            for level in (0, 1):
                if self.x11.XkbKeycodeToKeysym(display, keycode, 0, level) == keysym:
                    return keycode, level == 1
        raise LocalComputerError(
            f"Character {character!r} is unavailable in the active keyboard layout"
        )


def availability() -> dict[str, object]:
    if sys.platform != "linux":
        return {
            "available": False,
            "backend": "unavailable",
            "reason": "Native tools currently support Linux only.",
        }
    if not os.environ.get("DISPLAY"):
        return {
            "available": False,
            "backend": "unavailable",
            "reason": "DISPLAY is not configured.",
        }
    screenshot_backend = next(
        (name for name in ("gnome-screenshot", "scrot") if shutil.which(name)),
        "",
    )
    if os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland":
        portal = _portal_probe()
        if not portal.get("available"):
            return {
                "available": False,
                "backend": "unavailable",
                "reason": (
                    "GNOME Wayland requires the XDG RemoteDesktop portal: "
                    + str(portal.get("reason") or "unavailable")
                ),
            }
        if not portal.get("screencast_available"):
            missing = ", ".join(
                str(item)
                for item in portal.get("missing_capture_components", [])
            )
            return {
                "available": False,
                "backend": "unavailable",
                "reason": (
                    "Wayland screen capture requires the GStreamer PipeWire "
                    f"pipeline{': ' + missing if missing else '.'}"
                ),
            }
        return {
            "available": True,
            "backend": "wayland_portal",
            "reason": "",
            "screenshot_backend": "pipewire_screencast",
            "portal_version": portal.get("version", ""),
            "screencast_version": portal.get("screencast_version", ""),
            "screencast_available": bool(portal.get("screencast_available")),
            "missing_capture_components": portal.get(
                "missing_capture_components", []
            ),
        }
    if not screenshot_backend:
        return {
            "available": False,
            "backend": "unavailable",
            "reason": "Install gnome-screenshot or scrot.",
        }
    try:
        driver = _X11()
        with driver.display() as display:
            driver.size(display)
            driver.cursor(display)
    except Exception as exc:
        return {
            "available": False,
            "backend": "unavailable",
            "reason": str(exc),
        }
    return {
        "available": True,
        "backend": "local_x11",
        "reason": "",
        "screenshot_backend": screenshot_backend,
    }


def _driver() -> _X11:
    status = availability()
    if not status["available"] or status["backend"] != "local_x11":
        raise LocalComputerError(str(status["reason"]))
    return _X11()


def prepare_control_session() -> dict:
    """Start compositor-level control after Mounir's task approval."""
    status = availability()
    if not status["available"]:
        raise LocalComputerError(str(status["reason"]))
    if status["backend"] == "wayland_portal":
        return _PORTAL.call("start")
    return {"started": True, "backend": status["backend"]}


def release_control_session() -> None:
    """Release the Wayland sharing indicator when the Computer task ends."""
    status = availability()
    if status.get("backend") == "wayland_portal" and _PORTAL.process is not None:
        try:
            _PORTAL.call("close")
        except Exception:
            _PORTAL.stop()


def _coordinate_space(driver: _X11, display) -> tuple[int, int, int, int, float]:
    physical_width, physical_height = driver.size(display)
    scale = min(1.0, SCREENSHOT_WIDTH / physical_width)
    tool_width = max(1, round(physical_width * scale))
    tool_height = max(1, round(physical_height * scale))
    return physical_width, physical_height, tool_width, tool_height, scale


def _physical_coordinate(
    driver: _X11, display, coordinate: list[int]
) -> tuple[int, int, int, int]:
    if len(coordinate) != 2:
        raise LocalComputerError("coordinate must be [x, y]")
    x, y = map(int, coordinate)
    physical_width, physical_height, tool_width, tool_height, scale = (
        _coordinate_space(driver, display)
    )
    if x < 0 or y < 0 or x >= tool_width or y >= tool_height:
        raise LocalComputerError(
            f"Coordinates ({x}, {y}) are outside the {tool_width}x{tool_height} "
            "screenshot coordinate space"
        )
    physical_x = min(physical_width - 1, round(x / scale))
    physical_y = min(physical_height - 1, round(y / scale))
    return physical_x, physical_y, tool_width, tool_height


def _tool_coordinate(driver: _X11, display, x: int, y: int) -> tuple[int, int]:
    *_sizes, scale = _coordinate_space(driver, display)
    return round(x * scale), round(y * scale)


def _tool_size() -> tuple[int, int]:
    """Return the model-visible coordinate size for either local backend."""
    driver = _X11()
    with driver.display() as display:
        _physical_width, _physical_height, width, height, _scale = (
            _coordinate_space(driver, display)
        )
    return width, height


def _portal_move(coordinate: list[int], duration: float) -> tuple[int, int]:
    if len(coordinate) != 2:
        raise LocalComputerError("coordinate must be [x, y]")
    x, y = map(int, coordinate)
    width, height = _tool_size()
    if x < 0 or y < 0 or x >= width or y >= height:
        raise LocalComputerError(
            f"Coordinates ({x}, {y}) are outside the {width}x{height} "
            "screenshot coordinate space"
        )
    _PORTAL.call(
        "move", x=x, y=y, width=width, height=height, duration=float(duration)
    )
    return x, y


def _capture_screen_image():
    """Capture one full-resolution RGB image and delete its source file."""
    status = availability()
    if not status["available"]:
        raise LocalComputerError(str(status["reason"]))
    descriptor, raw_path = tempfile.mkstemp(prefix="mounir-screen-", suffix=".png")
    os.close(descriptor)
    path = Path(raw_path)
    try:
        backend = str(status["screenshot_backend"])
        command = (
            [backend, "--include-pointer", "-f", str(path)]
            if backend == "gnome-screenshot"
            else [backend, str(path)]
        )
        process = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=SCREENSHOT_TIMEOUT_SECONDS,
        )
        if process.returncode or not path.is_file():
            detail = " ".join((process.stderr or process.stdout or "").split())
            raise LocalComputerError(detail or "Desktop screenshot failed")
        if path.stat().st_size > SCREENSHOT_MAX_BYTES:
            raise LocalComputerError("Desktop screenshot exceeded the safe size limit")
        from PIL import Image

        with Image.open(path) as image:
            return image.convert("RGB").copy()
    finally:
        path.unlink(missing_ok=True)


def _encoded_visual(image, *, format: str, quality: int | None = None) -> str:
    output = io.BytesIO()
    options = {"optimize": True}
    if quality is not None:
        options["quality"] = quality
    image.save(output, format=format, **options)
    if output.tell() > SCREENSHOT_MAX_BYTES:
        raise LocalComputerError("Desktop image exceeded the safe size limit")
    return base64.b64encode(output.getvalue()).decode("ascii")


def _screenshot_size(source_width: int, source_height: int) -> tuple[int, int]:
    scale = min(1.0, SCREENSHOT_WIDTH / source_width)
    return (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
    )


def screenshot() -> tuple[list[dict], None]:
    """Capture the active desktop; the temporary source file is always deleted."""
    status = availability()
    if status.get("backend") == "wayland_portal":
        try:
            result = _PORTAL.call(
                "screenshot",
                width=SCREENSHOT_WIDTH,
                quality=SCREENSHOT_JPEG_QUALITY,
            )
            encoded = str(result.get("image_base64") or "")
            image_bytes = base64.b64decode(encoded, validate=True)
            width = int(result.get("width") or 0)
            height = int(result.get("height") or 0)
            source_width = int(result.get("source_width") or width)
            source_height = int(result.get("source_height") or height)
            if (
                not image_bytes
                or len(image_bytes) > SCREENSHOT_MAX_BYTES
                or width < 1
                or height < 1
            ):
                raise LocalComputerError("Wayland ScreenCast returned an invalid frame")
            return (
                [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Screenshot {width}x{height}, captured from the Wayland "
                            f"ScreenCast stream {source_width}x{source_height}. Use the "
                            f"{width}x{height} screenshot coordinate space for all "
                            "pointer tools."
                        ),
                    },
                ],
                None,
            )
        except Exception as exc:
            raise LocalComputerError(
                "Wayland screen sharing stopped or PipeWire capture failed: "
                f"{' '.join(str(exc).split()) or exc.__class__.__name__}. "
                "The Computer task cannot continue without the active sharing session."
            ) from exc

    from PIL import Image

    source = _capture_screen_image()
    source_width, source_height = source.size
    width, height = _screenshot_size(source_width, source_height)
    image = (
        source.resize((width, height), Image.Resampling.LANCZOS)
        if source.size != (width, height)
        else source
    )
    encoded = _encoded_visual(
        image, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY
    )
    return (
        [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            },
            {
                "type": "text",
                "text": (
                    f"Screenshot {width}x{height}, scaled from "
                    f"{source_width}x{source_height}. Use the {width}x{height} "
                    "screenshot coordinate space for all pointer tools."
                ),
            },
        ],
        None,
    )


def cursor_position() -> str:
    """Read the current physical cursor coordinates."""
    status = availability()
    if status.get("backend") == "wayland_portal":
        info = _PORTAL.call("status") if _PORTAL.process is not None else {}
        position = info.get("last_position")
        if position and len(position) == 2:
            return (
                f"Mounir last moved the real Wayland cursor to "
                f"({round(position[0])}, {round(position[1])}) in screenshot coordinates."
            )
        return (
            "The real Wayland cursor is included in screenshots. No portal movement "
            "has been issued in this control session yet."
        )
    driver = _driver()
    with driver.display() as display:
        x, y = driver.cursor(display)
        tool_x, tool_y = _tool_coordinate(driver, display, x, y)
    return f"Cursor is at ({tool_x}, {tool_y}) in screenshot coordinates."


def get_display_size() -> str:
    """Read the active X11 display dimensions."""
    driver = _X11()
    with driver.display() as display:
        physical_width, physical_height, width, height, _scale = _coordinate_space(
            driver, display
        )
    return (
        f"Pointer coordinate space is {width}x{height}, matching screenshots. "
        f"Valid coordinates: x=0-{width - 1}, y=0-{height - 1}. "
        f"Physical display is {physical_width}x{physical_height}."
    )


def mouse_move(
    coordinate: Annotated[list[int], "Destination [x, y] in screenshot pixels."],
    duration: Annotated[
        float,
        "Visible movement duration in seconds, 0 to 2. Defaults to 0.8 seconds.",
    ] = POINTER_MOVE_SECONDS,
) -> str:
    """Smoothly move the physical cursor to exact screen coordinates."""
    if availability().get("backend") == "wayland_portal":
        x, y = _portal_move(coordinate, duration)
        return (
            f"Delivered a real Wayland cursor move to ({x}, {y}) in screenshot "
            "coordinates. This confirms input delivery only; inspect a screenshot "
            "to verify the visible result."
        )
    driver = _driver()
    with driver.display() as display:
        physical_x, physical_y, _width, _height = _physical_coordinate(
            driver, display, coordinate
        )
        driver.smooth_move(display, physical_x, physical_y, duration)
        actual_x, actual_y = driver.cursor(display)
        actual = _tool_coordinate(driver, display, actual_x, actual_y)
    return f"Moved cursor to ({actual[0]}, {actual[1]}) in screenshot coordinates."


def _click(coordinate: list[int], button: int, count: int) -> str:
    if availability().get("backend") == "wayland_portal":
        x, y = _portal_move(coordinate, POINTER_MOVE_SECONDS)
        portal_button = {1: 272, 2: 274, 3: 273}[button]
        for index in range(count):
            _PORTAL.call("button", button=portal_button, pressed=True)
            _PORTAL.call("button", button=portal_button, pressed=False)
            if index + 1 < count:
                time.sleep(0.09)
        return (
            f"Delivered {count} click(s) at ({x}, {y}) through the Wayland "
            "compositor. This confirms input delivery only; inspect a new screenshot "
            "before claiming the intended UI outcome."
        )
    driver = _driver()
    with driver.display() as display:
        physical_x, physical_y, _width, _height = _physical_coordinate(
            driver, display, coordinate
        )
        driver.smooth_move(display, physical_x, physical_y, POINTER_MOVE_SECONDS)
        driver.click(display, button, count)
        actual_x, actual_y = driver.cursor(display)
        actual = _tool_coordinate(driver, display, actual_x, actual_y)
    return (
        f"Clicked {count} time(s) at ({actual[0]}, {actual[1]}) "
        "in screenshot coordinates."
    )


def left_click(
    coordinate: Annotated[list[int], "Click destination [x, y] in screenshot pixels."],
) -> str:
    """Smoothly move to coordinates and left-click once."""
    return _click(coordinate, 1, 1)


def right_click(
    coordinate: Annotated[list[int], "Click destination [x, y] in screenshot pixels."],
) -> str:
    """Smoothly move to coordinates and right-click once."""
    return _click(coordinate, 3, 1)


def double_click(
    coordinate: Annotated[list[int], "Click destination [x, y] in screenshot pixels."],
) -> str:
    """Smoothly move to coordinates and left-click twice."""
    return _click(coordinate, 1, 2)


def left_click_drag(
    start_coordinate: Annotated[list[int], "Drag start [x, y] in screenshot pixels."],
    coordinate: Annotated[list[int], "Drag destination [x, y] in screenshot pixels."],
    duration: Annotated[float, "Drag movement duration in seconds, 0 to 2."] = 0.5,
) -> str:
    """Smoothly drag from one coordinate to another with the left button."""
    if availability().get("backend") == "wayland_portal":
        _portal_move(start_coordinate, POINTER_MOVE_SECONDS)
        _PORTAL.call("button", button=272, pressed=True)
        try:
            end_x, end_y = _portal_move(coordinate, duration)
        finally:
            _PORTAL.call("button", button=272, pressed=False)
        return (
            f"Dragged to ({end_x}, {end_y}) through the Wayland compositor."
        )
    driver = _driver()
    with driver.display() as display:
        start_x, start_y, _width, _height = _physical_coordinate(
            driver, display, start_coordinate
        )
        end_x, end_y, _width, _height = _physical_coordinate(
            driver, display, coordinate
        )
        driver.smooth_move(display, start_x, start_y, POINTER_MOVE_SECONDS)
        driver.button(display, 1, True)
        try:
            driver.smooth_move(display, end_x, end_y, duration)
        finally:
            driver.button(display, 1, False)
        actual_x, actual_y = driver.cursor(display)
        actual = _tool_coordinate(driver, display, actual_x, actual_y)
    return f"Dragged to ({actual[0]}, {actual[1]}) in screenshot coordinates."


def scroll(
    coordinate: Annotated[list[int], "Scroll location [x, y] in screenshot pixels."],
    direction: Annotated[str, "up, down, left, or right"],
    amount: Annotated[int, "Positive number of scroll steps."] = 3,
) -> str:
    """Move to coordinates and scroll in one direction."""
    normalized = str(direction or "").strip().lower()
    buttons = {"up": 4, "down": 5, "left": 6, "right": 7}
    if normalized not in buttons:
        raise LocalComputerError("direction must be up, down, left, or right")
    steps = max(1, min(int(amount), 50))
    if availability().get("backend") == "wayland_portal":
        x, y = _portal_move(coordinate, POINTER_MOVE_SECONDS)
        axis = 0 if normalized in {"up", "down"} else 1
        signed_steps = -steps if normalized in {"up", "left"} else steps
        _PORTAL.call("scroll", axis=axis, steps=signed_steps)
        return (
            f"Scrolled {normalized} {steps} step(s) at ({x}, {y}) through "
            "the Wayland compositor."
        )
    driver = _driver()
    with driver.display() as display:
        x, y, _width, _height = _physical_coordinate(driver, display, coordinate)
        driver.smooth_move(display, x, y, POINTER_MOVE_SECONDS)
        for _ in range(steps):
            driver.click(display, buttons[normalized], 1)
    return (
        f"Scrolled {normalized} {steps} step(s) at "
        f"({int(coordinate[0])}, {int(coordinate[1])}) in screenshot coordinates."
    )


def _keysym(name: str) -> int:
    normalized = name.strip().lower().replace("_", "")
    if normalized in _KEYSYMS:
        return _KEYSYMS[normalized]
    if len(normalized) == 1:
        return ord(normalized)
    if normalized.startswith("f") and normalized[1:].isdigit():
        number = int(normalized[1:])
        if 1 <= number <= 35:
            return 0xFFBD + number
    raise LocalComputerError(f"Unsupported key: {name}")


def _press_combo(driver: _X11, display, text: str) -> None:
    parts = [part.strip().lower() for part in str(text or "").split("+") if part.strip()]
    if not parts:
        raise LocalComputerError("key combination is empty")
    modifiers = []
    for part in parts[:-1]:
        keysym = _MODIFIERS.get(part)
        if keysym is None:
            raise LocalComputerError(f"Unsupported modifier: {part}")
        modifiers.append(driver.keycode(display, keysym))
    keycode = driver.keycode(display, _keysym(parts[-1]))
    if not keycode:
        raise LocalComputerError(f"Key is unavailable: {parts[-1]}")
    for modifier in modifiers:
        driver.key_event(display, modifier, True)
    try:
        driver.key_event(display, keycode, True)
        driver.key_event(display, keycode, False)
    finally:
        for modifier in reversed(modifiers):
            driver.key_event(display, modifier, False)


def _portal_key_event(keysym: int, pressed: bool) -> None:
    _PORTAL.call("keysym", keysym=int(keysym), pressed=bool(pressed))


def _press_portal_combo(text: str) -> None:
    parts = [part.strip().lower() for part in str(text or "").split("+") if part.strip()]
    if not parts:
        raise LocalComputerError("key combination is empty")
    modifiers = []
    for part in parts[:-1]:
        keysym = _MODIFIERS.get(part)
        if keysym is None:
            raise LocalComputerError(f"Unsupported modifier: {part}")
        modifiers.append(keysym)
    main_keysym = _keysym(parts[-1])
    for modifier in modifiers:
        _portal_key_event(modifier, True)
    try:
        _portal_key_event(main_keysym, True)
        _portal_key_event(main_keysym, False)
    finally:
        for modifier in reversed(modifiers):
            _portal_key_event(modifier, False)


def key(
    text: Annotated[str, "Key or combination such as ctrl+l, return, or escape."],
    repeat: Annotated[int, "Number of presses."] = 1,
) -> str:
    """Press a keyboard key or Ctrl/Alt/Shift/Super combination."""
    count = max(1, min(int(repeat), 50))
    if availability().get("backend") == "wayland_portal":
        for _ in range(count):
            _press_portal_combo(text)
            time.sleep(0.025)
        return f"Pressed {text} {count} time(s) through the Wayland compositor."
    driver = _driver()
    with driver.display() as display:
        for _ in range(count):
            _press_combo(driver, display, text)
            time.sleep(0.025)
    return f"Pressed {text} {count} time(s)."


def type_text(
    text: Annotated[str, "Text to type into the currently focused field."],
    clear: Annotated[bool, "Select and remove existing field contents first."] = False,
    press_enter: Annotated[bool, "Press Enter after typing."] = False,
) -> str:
    """Type text with the active X11 keyboard layout."""
    if len(text) > 10_000:
        raise LocalComputerError("Text is too long for desktop typing")
    if availability().get("backend") == "wayland_portal":
        if clear:
            _press_portal_combo("ctrl+a")
            _press_portal_combo("backspace")
        for character in text:
            keysym = 0xFF0D if character == "\n" else ord(character)
            if keysym > 0xFF and character != "\n":
                keysym = 0x01000000 | keysym
            _portal_key_event(keysym, True)
            _portal_key_event(keysym, False)
            time.sleep(0.004)
        if press_enter:
            _press_portal_combo("return")
        return (
            f"Typed {len(text)} character(s) through the Wayland compositor"
            f"{' and pressed Enter' if press_enter else ''}."
        )
    driver = _driver()
    with driver.display() as display:
        if clear:
            _press_combo(driver, display, "ctrl+a")
            _press_combo(driver, display, "backspace")
        shift = driver.keycode(display, _MODIFIERS["shift"])
        for character in text:
            if character == "\n":
                _press_combo(driver, display, "return")
                continue
            keycode, shifted = driver.character_key(display, character)
            if shifted:
                driver.key_event(display, shift, True)
            try:
                driver.key_event(display, keycode, True)
                driver.key_event(display, keycode, False)
            finally:
                if shifted:
                    driver.key_event(display, shift, False)
            time.sleep(0.004)
        if press_enter:
            _press_combo(driver, display, "return")
    return f"Typed {len(text)} character(s){' and pressed Enter' if press_enter else ''}."


def wait(
    duration: Annotated[float, "Seconds to wait, from 0 to 30."] = 1.0,
) -> str:
    """Wait briefly for the desktop to update."""
    seconds = max(0.0, min(float(duration), 30.0))
    time.sleep(seconds)
    return f"Waited {seconds:g} second(s)."


def _tool(function, name: str, description: str, *, visual: bool = False):
    return StructuredTool.from_function(
        func=function,
        name=name,
        description=description,
        response_format="content_and_artifact" if visual else "content",
    )


TOOLS = [
    _tool(screenshot, "screenshot", "Capture the visible desktop for visual inspection.", visual=True),
    _tool(cursor_position, "cursor_position", "Read the physical cursor coordinates."),
    _tool(get_display_size, "get_display_size", "Read display dimensions and coordinate bounds."),
    _tool(wait, "wait", "Wait briefly for an application to update."),
    _tool(mouse_move, "mouse_move", "Smoothly and visibly move the physical cursor."),
    _tool(left_click, "left_click", "Smoothly move to coordinates and left-click."),
    _tool(right_click, "right_click", "Smoothly move to coordinates and right-click."),
    _tool(double_click, "double_click", "Smoothly move to coordinates and double-click."),
    _tool(left_click_drag, "left_click_drag", "Drag smoothly between two coordinates."),
    _tool(scroll, "scroll", "Move to coordinates and scroll."),
    _tool(type_text, "type", "Type text into the focused field, optionally clearing it or pressing Enter."),
    _tool(key, "key", "Press a key or shortcut such as ctrl+l, return, or escape."),
]
