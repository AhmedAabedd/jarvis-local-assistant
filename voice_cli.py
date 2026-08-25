#!/usr/bin/env python3
"""Voice entry point for Mounir.

    python voice_cli.py          # push-to-talk (Enter to start/stop)
    python voice_cli.py --wake   # hands-free: say the wake word, then talk

The configured STT, language model, and TTS runtimes process each turn, speaking
each sentence as soon as it is ready.
"""

from __future__ import annotations

import argparse
import sys

from mounir import llm
from mounir.voice import run_hands_free_loop, run_voice_loop


def main() -> int:
    parser = argparse.ArgumentParser(description="Talk to Mounir by voice.")
    parser.add_argument(
        "--wake",
        action="store_true",
        help="hands-free mode: trigger with the wake word instead of Enter",
    )
    args = parser.parse_args()

    if not llm.is_up():
        print(
            "Can't reach the selected model. Check its connection in Agent Studio.",
            file=sys.stderr,
        )
        return 1

    try:
        if args.wake:
            run_hands_free_loop()
        else:
            run_voice_loop()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
