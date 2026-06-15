#!/usr/bin/env python3
"""Voice entry point for Mounir (Stage 2).

    python voice_cli.py

Push-to-talk: press Enter to start speaking, Enter again to stop. Mounir
transcribes (Whisper), thinks (Qwen3 via Ollama), and talks back (Piper),
speaking each sentence as soon as it's ready.
"""

from __future__ import annotations

import sys

from mounir import config, llm
from mounir.voice import run_voice_loop


def main() -> int:
    if not llm.is_up():
        print(
            f"Can't reach Ollama at {config.OLLAMA_HOST}.\n"
            "Start it with `ollama serve` first.",
            file=sys.stderr,
        )
        return 1
    try:
        run_voice_loop()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
