"""One-time installer for Mounir's required local GBrain service.

Provider-specific bootstrapping stays isolated here. Normal MCP servers remain
entirely user configured and never pass through this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import knowledge_protocol

GBRAIN_PACKAGE = "github:garrytan/gbrain"
SETUP_TIMEOUT_SECONDS = 300


def _run(argv: list[str], *, env: dict[str, str] | None = None) -> str:
    try:
        process = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=SETUP_TIMEOUT_SECONDS,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Could not run {' '.join(argv[:2])}: {exc}") from exc
    output = "\n".join(
        part.strip() for part in (process.stdout, process.stderr) if part.strip()
    )
    if process.returncode:
        raise RuntimeError(output[-2000:] or f"Setup exited with code {process.returncode}.")
    return output


def _gbrain_executable() -> str | None:
    executable = shutil.which("gbrain")
    if executable:
        return executable
    bun = shutil.which("bun")
    if bun:
        sibling = Path(bun).resolve().parent / "gbrain"
        if sibling.is_file():
            return str(sibling)
    return None


def ensure_local_gbrain() -> str:
    """Install GBrain when needed and initialize a small local PGLite brain."""
    executable = _gbrain_executable()
    installed = False
    if executable is None:
        bun = shutil.which("bun")
        if bun is None:
            raise RuntimeError(
                "Bun is required to install the built-in GBrain service. "
                "Install Bun, then run setup again."
            )
        _run([bun, "install", "-g", GBRAIN_PACKAGE])
        executable = _gbrain_executable()
        if executable is None:
            raise RuntimeError("GBrain was installed but its executable is not on PATH.")
        installed = True

    home_parent = knowledge_protocol.local_home_parent()
    home = home_parent / ".gbrain"
    if not (home / "config.json").is_file():
        environment = os.environ.copy()
        environment["GBRAIN_HOME"] = str(home_parent)
        _run(
            [
                executable,
                "init",
                "--pglite",
                "--no-embedding",
                "--non-interactive",
            ],
            env=environment,
        )
        return "GBrain was installed and initialized." if installed else "GBrain was initialized."
    return "GBrain is installed and initialized."


def main() -> int:
    print(ensure_local_gbrain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
