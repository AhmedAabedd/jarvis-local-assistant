#!/usr/bin/env python3
"""Text REPL for talking to Mounir.

    python cli.py

Commands:
    /reset   forget the current conversation
    /save    save conversation to ~/.mounir/last_conversation.json
    /load    restore the last saved conversation
    /think   toggle Qwen3 thinking mode for the next replies (slower, smarter)
    /exit    quit

Press Esc (or Ctrl+C) while Mounir is replying to interrupt it without leaving
the session; Ctrl+C at the empty prompt quits.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time

try:
    # Importing readline is enough to give input() arrow-key line editing
    # (Home/End/Ctrl-A/Ctrl-E to jump around the line) and Up/Down history.
    import readline  # noqa: F401
except ImportError:  # not available on some platforms (e.g. bare Windows)
    readline = None
else:
    try:
        # Bracketed paste: newlines inside a paste become literal text instead
        # of submitting the line, so pasting multi-line content stays ONE
        # request (sent only when you press Enter yourself) rather than firing
        # a separate call per line.
        readline.parse_and_bind("set enable-bracketed-paste on")
    except Exception:
        pass

from mounir import config, llm, trace
from mounir.agent import Agent
from mounir.memory import Conversation


@contextlib.contextmanager
def _esc_interrupts():
    """While active, pressing Esc raises KeyboardInterrupt in the main thread.

    A tiny watcher thread puts the terminal in char-at-a-time mode and waits
    for Esc; on Esc it interrupts the main thread, which the caller catches to
    stop the current response without leaving the session. No-op when stdin
    isn't a real terminal (e.g. piped input).
    """
    if not sys.stdin.isatty():
        yield
        return

    import _thread
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    stop = threading.Event()

    def _watch() -> None:
        try:
            tty.setcbreak(fd)
            while not stop.is_set():
                if select.select([sys.stdin], [], [], 0.1)[0]:
                    if sys.stdin.read(1) == "\x1b":  # Esc
                        _thread.interrupt_main()
                        return
        except Exception:
            pass

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        yield
    finally:
        stop.set()
        watcher.join(timeout=0.2)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        termios.tcflush(fd, termios.TCIFLUSH)  # drop any keys hit while working


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
    print("  Type a message, Esc to interrupt a reply, or /exit to quit.\n")

    # A purple arrow marks the input area. With readline active, color codes
    # must sit inside \001..\002 so it doesn't count them toward the prompt
    # width (otherwise the cursor math is off when you arrow back through it).
    if readline is not None and sys.stdin.isatty():
        prompt = f"\n\001{trace.PURPLE}{trace.BOLD}\002❯\001{trace.RESET}\002 "
    else:
        prompt = f"\n{trace.PURPLE}{trace.BOLD}❯{trace.RESET} "

    while True:
        try:
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.startswith("/"):
            if _handle_command(user_input, agent):
                break
            continue

        print()  # breathing room between the prompt and the reply
        start = time.time()
        tokens = 0
        try:
            with _esc_interrupts():
                for chunk in agent.respond(user_input):
                    print(chunk, end="", flush=True)
                    tokens += 1
        except llm.OllamaError as exc:
            print(f"\n[error] {exc}")
            continue
        except KeyboardInterrupt:
            # Esc or Ctrl+C while working: drop this response, keep the session.
            print(f"\n{trace.DIM}  [interrupted]{trace.RESET}")
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
