#!/usr/bin/env python3
"""Standalone Telegram entry point for Mounir.

The web server now starts the same bridge automatically when
``TELEGRAM_BOT_TOKEN`` is configured.  This file remains available for running
Telegram by itself while that integrated path is being used and tested.
"""

from __future__ import annotations

from mounir import llm, trace
from mounir.telegram_bridge import TelegramBridge


def main() -> int:
    bridge = TelegramBridge()
    error = bridge.configuration_error()
    if error:
        print(
            f"{error}. Open Agent Studio → Telegram and save the token from "
            "@BotFather first."
        )
        return 1
    if not llm.is_up():
        print("Can't reach the selected model. Check its connection in Agent Studio.")
        return 1

    trace.banner("phone in your pocket, brain on your desk.")
    trace.rule(64)
    trace.agent_row("Agent", llm.active_model(bridge.agent.model))
    trace.kv("bridge", "Telegram long polling")
    trace.rule(64)
    print("  Ctrl+C to stop.\n")

    try:
        if not bridge.run_forever():
            print(bridge.last_error)
            return 1
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
