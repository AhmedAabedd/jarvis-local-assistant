"""Portable default-browser discovery, opening, and graceful closing."""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class BrowserApp:
    name: str
    executable: str = ""
    app_id: str = ""


def open_default(url: str = "") -> bool:
    """Ask the operating system to open its configured default browser."""
    return bool(webbrowser.open(url or "about:blank", new=2))


def _run(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _linux_browser() -> BrowserApp | None:
    query = shutil.which("xdg-mime")
    desktop_id = (
        _run([query, "query", "default", "x-scheme-handler/https"])
        if query
        else ""
    )
    if not desktop_id:
        return None

    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    data_dirs = [
        Path(part)
        for part in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        if part
    ]
    desktop_file = next(
        (
            root / "applications" / desktop_id
            for root in [data_home, *data_dirs]
            if (root / "applications" / desktop_id).is_file()
        ),
        None,
    )
    if desktop_file is None:
        return BrowserApp(Path(desktop_id).stem)

    name = Path(desktop_id).stem
    command = ""
    try:
        for line in desktop_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Name=") and name == Path(desktop_id).stem:
                name = line.partition("=")[2].strip() or name
            elif line.startswith("Exec=") and not command:
                command = line.partition("=")[2].strip()
    except OSError:
        pass

    argv = shlex.split(command) if command else []
    while argv and (argv[0] == "env" or "=" in argv[0]):
        argv.pop(0)
    executable = argv[0] if argv else ""
    executable = shutil.which(executable) or executable
    return BrowserApp(name=name, executable=executable, app_id=desktop_id)


def _windows_browser() -> BrowserApp | None:
    try:
        import winreg

        choice_path = (
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"
            r"\https\UserChoice"
        )
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, choice_path) as key:
            prog_id = winreg.QueryValueEx(key, "ProgId")[0]
        with winreg.OpenKey(
            winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command"
        ) as key:
            command = winreg.QueryValueEx(key, None)[0]
        argv = shlex.split(command, posix=False)
        executable = argv[0].strip('"') if argv else ""
        return BrowserApp(name=Path(executable).stem or prog_id, executable=executable)
    except (ImportError, OSError, IndexError):
        return None


def _mac_browser() -> BrowserApp | None:
    script = (
        'set appPath to path to default application for URL "https://example.com"\n'
        'tell application "Finder" to get name of appPath'
    )
    name = _run(["osascript", "-e", script])
    if not name:
        return None
    return BrowserApp(name=name.removesuffix(".app"), app_id=name.removesuffix(".app"))


def default_browser() -> BrowserApp | None:
    system = platform.system()
    if system == "Linux":
        return _linux_browser()
    if system == "Windows":
        return _windows_browser()
    if system == "Darwin":
        return _mac_browser()
    return None


def _tokens(app: BrowserApp) -> set[str]:
    # Only compare application identifiers/basenames. Splitting a full path
    # would create dangerously broad tokens such as "program" or "application".
    values = [
        app.name,
        Path(app.executable).name if app.executable else "",
        Path(app.app_id).stem if app.app_id else "",
    ]
    tokens: set[str] = set()
    for value in values:
        normalized = re.sub(r"[^a-z0-9]", "", Path(value).stem.lower())
        if len(normalized) >= 4:
            tokens.add(normalized)
        tokens.update(
            part.lower()
            for part in re.split(r"[^a-zA-Z0-9]+", value)
            if len(part) >= 5 and part.lower() not in {"browser", "desktop", "stable"}
        )
    return tokens


def _matching_processes(app: BrowserApp) -> list[psutil.Process]:
    tokens = _tokens(app)
    executable = str(Path(app.executable).resolve()) if app.executable else ""
    matches: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            proc_exe = process.info.get("exe") or ""
            if executable and proc_exe and str(Path(proc_exe).resolve()) == executable:
                matches.append(process)
                continue
            values = [process.info.get("name") or "", Path(proc_exe).name]
            cmdline = process.info.get("cmdline") or []
            if cmdline:
                values.append(Path(cmdline[0]).name)
            normalized = {
                re.sub(r"[^a-z0-9]", "", value.lower()) for value in values if value
            }
            if tokens & normalized:
                matches.append(process)
        except (psutil.Error, OSError):
            continue
    return matches


def _close_windows(processes: list[psutil.Process]) -> bool:
    """Post WM_CLOSE to visible top-level windows owned by the processes."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    target_pids = {process.pid for process in processes}
    closed_a_window = False
    user32 = ctypes.windll.user32
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def close_window(hwnd, _lparam):
        nonlocal closed_a_window
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in target_pids and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            closed_a_window = True
        return True

    user32.EnumWindows(enum_callback(close_window), 0)
    return closed_a_window


def close_default(app: BrowserApp | None = None) -> tuple[bool, str]:
    """Gracefully close the configured default browser on supported desktops."""
    app = app or default_browser()
    if app is None:
        return False, "Could not identify the operating system's default browser."

    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["osascript", "-e", f'tell application "{app.app_id}" to quit'],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False, f"Could not close {app.name}."
        return (
            result.returncode == 0,
            f"Closed {app.name}." if result.returncode == 0 else f"Could not close {app.name}.",
        )

    processes = _matching_processes(app)
    if not processes:
        return True, f"{app.name} is not running."
    if platform.system() == "Windows":
        if not _close_windows(processes):
            return False, f"Could not find an open {app.name} window to close."
    else:
        # SIGTERM lets browsers save their session and shut down cleanly.
        for process in processes:
            try:
                process.terminate()
            except psutil.Error:
                pass
    _, alive = psutil.wait_procs(processes, timeout=8)
    if alive:
        return False, f"Asked {app.name} to close, but some windows are still running."
    return True, f"Closed {app.name}."
