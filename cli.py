#!/usr/bin/env python3
"""Text REPL for talking to Mounir.

    python cli.py

Commands:
    /reset   forget the current conversation
    /save    save conversation to ~/.mounir/last_conversation.json
    /load    restore the last saved conversation
    /think   toggle Qwen3 thinking mode for the next replies (slower, smarter)
    /exit    quit
"""

from __future__ import annotations

import sys
import time

from mounir import config, llm, trace
from mounir.agent import Agent
from mounir.memory import Conversation


def main() -> int:
    if not llm.is_up():
        print(
            "Can't reach Ollama. Start it with `ollama serve` "
            "(and make sure the model is pulled).",
            file=sys.stderr,
        )
        return 1

    agent = Agent()
    trace.banner("knuckles cracked. no cloud, no fluff. let's cook.")
    trace.rule()
    trace.kv("model", llm.active_model(agent.model))
    trace.kv("coder", f"{config.CODER_MODEL}  ·  nvidia")
    trace.kv("thinking", "on" if config.THINK else "off")
    trace.kv("status", "online")
    trace.rule()
    print("  Type a message, or /exit to quit.\n")

    while True:
        try:
            user_input = input("you ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.startswith("/"):
            if _handle_command(user_input, agent):
                break
            continue

        print("mounir ▸ ", end="", flush=True)
        start = time.time()
        tokens = 0
        try:
            for chunk in agent.respond(user_input):
                print(chunk, end="", flush=True)
                tokens += 1
        except llm.OllamaError as exc:
            print(f"\n[error] {exc}")
            continue
        elapsed = time.time() - start
        rate = tokens / elapsed if elapsed else 0
        print(f"\n  ({tokens} chunks, {elapsed:.1f}s, ~{rate:.1f} chunk/s)\n")

    return 0


def _handle_command(cmd: str, agent: Agent) -> bool:
    """Returns True if the REPL should exit."""
    name = cmd.lower()
    if name in ("/exit", "/quit"):
        return True
    if name == "/reset":
        agent.conversation.reset()
        print("[conversation cleared]\n")
    elif name == "/save":
        path = agent.conversation.save()
        print(f"[saved to {path}]\n")
    elif name == "/load":
        ok = agent.conversation.load()
        print("[loaded]\n" if ok else "[nothing saved yet]\n")
    elif name == "/think":
        config.THINK = not config.THINK
        print(f"[thinking mode {'on' if config.THINK else 'off'}]\n")
    else:
        print(f"[unknown command: {cmd}]\n")
    return False


if __name__ == "__main__":
    raise SystemExit(main())
