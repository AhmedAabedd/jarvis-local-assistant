"""System agent — hands on the laptop's hardware.

The orchestrator calls run(task) and gets back a short plain-text report of
what was changed (volume, brightness, media playback, radios, power) or read
(battery, disk, memory, network). All tools are thin wrappers over the stock
Ubuntu/GNOME command line — wpctl for PipeWire audio, GNOME's D-Bus interface
for screen brightness (no root, shows the on-screen slider), UPower D-Bus for
the keyboard backlight, MPRIS D-Bus for media players (the same protocol
playerctl wraps), nmcli/rfkill for radios — so there is nothing to install.

Destructive actions are gated: suspend asks the user for confirmation through
the same shared confirmation flow MCP tools use, so it works from the terminal and
the Telegram bridge alike.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from .. import config, llm
from .. import trace

MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """\
You are the system specialist — you control the laptop itself: audio, screen,
media playback, radios, power. The machine you are on is described in your
context; your tools already speak to the right interfaces.

RULES
- Do exactly what the task asks, then STOP. "Turn it up" means one volume
  step, not maxing it out; when the task gives a number, use it.
- Tools return the REAL resulting state — report that, never assume.
- The CURRENT STATE line in your context is a snapshot from BEFORE the task.
  When the task asks for a change, ALWAYS call the tool — never answer that
  it is "already done" from the snapshot or from memory.
- Only touch Wi-Fi, Bluetooth, the lock screen, or suspend when the task
  explicitly asks for them. suspend asks the user to confirm by itself; if it
  reports "cancelled", relay that, don't retry.
- If a tool errors, report the error honestly. Never claim a change you did
  not see succeed.

FINAL REPORT (MANDATORY)
Your last message is read by the SUPERVISOR, not the user. One short sentence
or two with the concrete outcome and the resulting state, e.g. "Volume raised
to 55%." or "Paused the media playing in Chromium. Battery is at 82%,
charging." No fluff, no headers — never write the words "FINAL REPORT".
"""


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _run(argv: list[str], timeout: int = 10) -> tuple[bool, str]:
    """Run a command; (ok, combined output). Never raises."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        return False, f"{argv[0]} is not installed."
    except subprocess.TimeoutExpired:
        return False, f"{argv[0]} timed out."
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out


def _gdbus_call(bus: str, dest: str, path: str, method: str, *args: str) -> tuple[bool, str]:
    argv = ["gdbus", "call", f"--{bus}", "--dest", dest,
            "--object-path", path, "--method", method, *args]
    return _run(argv)


def _rfkill() -> str | None:
    return shutil.which("rfkill") or (
        "/usr/sbin/rfkill" if shutil.which("/usr/sbin/rfkill") else None
    )


# ---------------------------------------------------------------------------
# Audio (PipeWire via wpctl)
# ---------------------------------------------------------------------------

_SINK = "@DEFAULT_AUDIO_SINK@"


def _volume_state() -> str:
    ok, out = _run(["wpctl", "get-volume", _SINK])
    if not ok:
        return out
    # "Volume: 0.46" or "Volume: 0.46 [MUTED]"
    m = re.search(r"([\d.]+)", out)
    pct = f"{round(float(m.group(1)) * 100)}%" if m else out
    return f"volume {pct}" + (" (muted)" if "MUTED" in out else "")


def set_volume(action: str, level: int | None = None) -> str:
    """Change the speaker volume: up / down / set / mute / unmute."""
    action = (action or "").strip().lower()
    if action == "set":
        if level is None:
            return "Give a level (0-100) when using set."
        pct = max(0, min(int(level), 100))
        ok, out = _run(["wpctl", "set-volume", _SINK, f"{pct}%"])
    elif action in ("up", "down"):
        ok, out = _run(["wpctl", "set-volume", "-l", "1.0", _SINK,
                        "5%+" if action == "up" else "5%-"])
    elif action in ("mute", "unmute"):
        ok, out = _run(["wpctl", "set-mute", _SINK, "1" if action == "mute" else "0"])
    else:
        return f"Unknown action {action!r}: use up, down, set, mute, or unmute."
    if not ok:
        return f"Volume change failed: {out}"
    return f"Done — {_volume_state()}."


# ---------------------------------------------------------------------------
# Screen brightness (GNOME Settings Daemon over D-Bus — no root, shows OSD)
# ---------------------------------------------------------------------------

_POWER = ("org.gnome.SettingsDaemon.Power", "/org/gnome/SettingsDaemon/Power")


def _brightness_get() -> int | None:
    ok, out = _gdbus_call(
        "session", *_POWER, "org.freedesktop.DBus.Properties.Get",
        "org.gnome.SettingsDaemon.Power.Screen", "Brightness",
    )
    m = re.search(r"(\d+)", out) if ok else None
    return int(m.group(1)) if m else None


def set_brightness(action: str, level: int | None = None) -> str:
    """Change the screen brightness: up / down / set (percent)."""
    action = (action or "").strip().lower()
    if action == "set":
        if level is None:
            return "Give a level (0-100) when using set."
        pct = max(0, min(int(level), 100))
        ok, out = _gdbus_call(
            "session", *_POWER, "org.freedesktop.DBus.Properties.Set",
            "org.gnome.SettingsDaemon.Power.Screen", "Brightness", f"<int32 {pct}>",
        )
    elif action in ("up", "down"):
        method = "org.gnome.SettingsDaemon.Power.Screen.StepUp" if action == "up" \
            else "org.gnome.SettingsDaemon.Power.Screen.StepDown"
        ok, out = _gdbus_call("session", *_POWER, method)
    else:
        return f"Unknown action {action!r}: use up, down, or set."
    if not ok:
        return f"Brightness change failed: {out}"
    now = _brightness_get()
    return f"Done — screen brightness {now}%." if now is not None else "Done."


def set_keyboard_backlight(level: int) -> str:
    """Set the keyboard backlight to a percentage (0 turns it off)."""
    kbd = ("org.freedesktop.UPower", "/org/freedesktop/UPower/KbdBacklight")
    ok, out = _gdbus_call(
        "system", *kbd, "org.freedesktop.UPower.KbdBacklight.GetMaxBrightness"
    )
    m = re.search(r"(\d+)", out) if ok else None
    if not m:
        return f"No keyboard backlight found: {out}"
    max_raw = int(m.group(1))
    pct = max(0, min(int(level), 100))
    raw = round(max_raw * pct / 100)
    ok, out = _gdbus_call(
        "system", *kbd, "org.freedesktop.UPower.KbdBacklight.SetBrightness", str(raw)
    )
    if not ok:
        return f"Keyboard backlight change failed: {out}"
    return f"Done — keyboard backlight at {pct}%."


# ---------------------------------------------------------------------------
# Media playback (MPRIS over D-Bus — controls browser video/audio too)
# ---------------------------------------------------------------------------

def _mpris_players() -> list[str]:
    ok, out = _gdbus_call(
        "session", "org.freedesktop.DBus", "/org/freedesktop/DBus",
        "org.freedesktop.DBus.ListNames",
    )
    return re.findall(r"org\.mpris\.MediaPlayer2\.[\w.\-]+", out) if ok else []


def media_control(action: str) -> str:
    """Control whatever is playing: play_pause / next / previous / stop."""
    methods = {
        "play_pause": "PlayPause", "pause": "PlayPause", "play": "PlayPause",
        "next": "Next", "previous": "Previous", "stop": "Stop",
    }
    method = methods.get((action or "").strip().lower())
    if method is None:
        return f"Unknown action {action!r}: use play_pause, next, previous, or stop."
    players = _mpris_players()
    if not players:
        return "No media player is running (nothing to control)."
    player = players[0]
    ok, out = _gdbus_call(
        "session", player, "/org/mpris/MediaPlayer2",
        f"org.mpris.MediaPlayer2.Player.{method}",
    )
    if not ok:
        return f"Media control failed: {out}"
    app = player.removeprefix("org.mpris.MediaPlayer2.").split(".")[0]
    return f"Sent {method} to {app}."


# ---------------------------------------------------------------------------
# Status, radios, power
# ---------------------------------------------------------------------------

def system_status() -> str:
    """Battery, disk, memory, Wi-Fi network, and load — one snapshot."""
    lines: list[str] = []

    ok, out = _run(["upower", "-e"])
    battery_dev = next((l for l in out.splitlines() if "BAT" in l.upper()), None) if ok else None
    if battery_dev:
        ok, info = _run(["upower", "-i", battery_dev])
        if ok:
            pct = re.search(r"percentage:\s*([\d.]+)", info)
            state = re.search(r"state:\s*(\S+)", info)
            lines.append(
                f"Battery: {round(float(pct.group(1))) if pct else '?'}% "
                f"({state.group(1) if state else '?'})"
            )

    du = shutil.disk_usage("/")
    lines.append(f"Disk /: {du.free // 2**30} GB free of {du.total // 2**30} GB")

    try:
        mem = dict(
            line.split(":", 1) for line in open("/proc/meminfo").read().splitlines()
        )
        total = int(mem["MemTotal"].split()[0]) // 1024
        avail = int(mem["MemAvailable"].split()[0]) // 1024
        lines.append(f"Memory: {avail} MB free of {total} MB")
    except Exception:
        pass

    ok, out = _run(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"])
    if ok:
        active = next((l.split(":", 1)[1] for l in out.splitlines() if l.startswith("yes:")), None)
        lines.append(f"Wi-Fi: connected to {active}" if active else "Wi-Fi: not connected")

    try:
        load = open("/proc/loadavg").read().split()[0]
        lines.append(f"CPU load (1 min): {load}")
    except Exception:
        pass

    players = _mpris_players()
    if players:
        apps = ", ".join(sorted({p.removeprefix("org.mpris.MediaPlayer2.").split(".")[0] for p in players}))
        lines.append(f"Media players running: {apps}")

    return "\n".join(lines) or "Could not read system status."


def set_wifi(state: str) -> str:
    """Turn Wi-Fi on or off."""
    state = (state or "").strip().lower()
    if state not in ("on", "off"):
        return "Use state 'on' or 'off'."
    ok, out = _run(["nmcli", "radio", "wifi", state])
    return f"Wi-Fi turned {state}." if ok else f"Wi-Fi change failed: {out}"


def set_bluetooth(state: str) -> str:
    """Turn Bluetooth on or off."""
    state = (state or "").strip().lower()
    if state not in ("on", "off"):
        return "Use state 'on' or 'off'."
    rfkill = _rfkill()
    if rfkill is None:
        return "rfkill is not available on this machine."
    ok, out = _run([rfkill, "unblock" if state == "on" else "block", "bluetooth"])
    return f"Bluetooth turned {state}." if ok else f"Bluetooth change failed: {out}"


def lock_screen() -> str:
    """Lock the session (screen locks, everything keeps running)."""
    ok, out = _run(["loginctl", "lock-session"])
    return "Screen locked." if ok else f"Lock failed: {out}"


def suspend() -> str:
    """Suspend the laptop — asks the user to confirm first."""
    from .. import tools as _tools  # confirm flow is owned by the tools module

    if not _tools.request_confirmation(
        "Suspend the laptop? It stops responding (Telegram too) until "
        "someone wakes it up physically."
    ):
        return "Suspend cancelled — the user did not confirm."
    ok, out = _run(["systemctl", "suspend"])
    return "Suspending now." if ok else f"Suspend failed: {out}"


# ---------------------------------------------------------------------------
# Tool schemas + dispatch
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Change speaker volume. Actions: up/down (one 5% step), set (needs level 0-100), mute, unmute. Returns the resulting volume.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["up", "down", "set", "mute", "unmute"]},
                    "level": {"type": "integer", "description": "Target percent 0-100, only with action=set."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Change screen brightness. Actions: up/down (one step), set (needs level 0-100). Returns the resulting percent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["up", "down", "set"]},
                    "level": {"type": "integer", "description": "Target percent 0-100, only with action=set."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_keyboard_backlight",
            "description": "Set the keyboard backlight brightness as a percent; 0 turns it off.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "description": "Percent 0-100."},
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": "Control the media currently playing (including YouTube in the browser): play_pause, next, previous, stop.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_status",
            "description": "Snapshot of battery, disk, memory, Wi-Fi network, CPU load, and running media players.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_wifi",
            "description": "Turn Wi-Fi on or off. Only when explicitly asked.",
            "parameters": {
                "type": "object",
                "properties": {"state": {"type": "string", "enum": ["on", "off"]}},
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_bluetooth",
            "description": "Turn Bluetooth on or off. Only when explicitly asked.",
            "parameters": {
                "type": "object",
                "properties": {"state": {"type": "string", "enum": ["on", "off"]}},
                "required": ["state"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lock_screen",
            "description": "Lock the screen (session keeps running). Only when explicitly asked.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suspend",
            "description": "Suspend the laptop. Asks the user to confirm by itself. Only when explicitly asked.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_REGISTRY = {
    "set_volume": set_volume,
    "set_brightness": set_brightness,
    "set_keyboard_backlight": set_keyboard_backlight,
    "media_control": media_control,
    "system_status": system_status,
    "set_wifi": set_wifi,
    "set_bluetooth": set_bluetooth,
    "lock_screen": lock_screen,
    "suspend": suspend,
}


def _dispatch(name: str, arguments: dict) -> str:
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Unknown tool: {name}"
    try:
        return fn(**arguments)
    except TypeError as exc:
        return f"Bad arguments for {name}: {exc}"
    except Exception as exc:
        return f"Tool {name} failed: {exc}"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _device_info() -> str:
    """Detect the machine at runtime so this works unchanged on another device."""
    import os
    import platform

    parts: list[str] = []
    try:  # hardware model, e.g. "MacBookAir9,1"
        model = open("/sys/devices/virtual/dmi/id/product_name").read().strip()
        if model:
            parts.append(model)
    except OSError:
        pass
    try:  # distro pretty name, e.g. "Ubuntu 24.04.2 LTS"
        os_release = dict(
            line.split("=", 1) for line in open("/etc/os-release").read().splitlines() if "=" in line
        )
        parts.append(os_release.get("PRETTY_NAME", "").strip('"') or platform.system())
    except OSError:
        parts.append(f"{platform.system()} {platform.release()}")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")[-1]
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if desktop or session:
        parts.append(" on ".join(p for p in (desktop, session) if p))
    return ", ".join(parts)


def _context() -> str:
    """Device + current hardware state so simple tasks need zero discovery calls."""
    brightness = _brightness_get()
    state = f"CURRENT STATE: {_volume_state()}"
    if brightness is not None:
        state += f"; screen brightness {brightness}%"
    return f"DEVICE: {_device_info()}\n{state}."


def run(task: str, allowed_tools: list[str] | None = None) -> str:
    """Run the system agent on a task. Returns a short plain-text report."""
    if not config.NVIDIA_API_KEY:
        return "System agent failed: NVIDIA_API_KEY is not set."

    allowed = (
        {str(name) for name in allowed_tools}
        if allowed_tools is not None
        else set(_REGISTRY)
    )
    tool_schemas = [
        schema for schema in TOOLS
        if schema["function"]["name"] in allowed
    ]

    messages = [
        {"role": "system", "content": config.specialist_system_prompt(SYSTEM_PROMPT)},
        {"role": "user", "content": f"{_context()}\n\nTASK FROM SUPERVISOR:\n{task}"},
    ]
    retried_empty = False
    executed: list[str] = []  # tool results so far — actions that REALLY happened

    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            message = llm.nvidia_chat(
                messages, tools=tool_schemas or None, model=config.SYSTEM_MODEL
            )
        except Exception as exc:
            if executed:
                # The LLM died AFTER tools ran (e.g. rate limit on the report
                # call). Saying "failed" would make the supervisor redo actions
                # that already happened — report them instead.
                return (
                    "System agent was cut off by an LLM error while reporting, "
                    "but these actions DID run: " + "; ".join(executed)
                    + ". Do NOT redo them."
                )
            return f"System agent failed: {exc}"

        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        if not tool_calls and not content.strip():
            if not retried_empty:
                retried_empty = True
                continue
            return (
                "System agent failed: the model returned an empty response "
                "twice — nothing was changed. Try again."
            )

        if not tool_calls:
            trace.event(f"{round_num + 1} round(s)")
            # The 8b likes to prefix its report with "FINAL REPORT:" no matter
            # what the prompt says — strip it in code.
            return re.sub(r"(?i)^\s*final report:?\s*", "", content.strip())

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": tool_calls}
        )

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except Exception:
                args = {}
            result = (
                _dispatch(name, args)
                if name in allowed
                else f"Tool {name} is not allowed for this run."
            )
            trace.tool(name, args, result)
            executed.append(f"{name} -> {result}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", "call_0"),
                    "content": result,
                }
            )

    return "System agent reached max tool rounds — the task may be only partly done."
