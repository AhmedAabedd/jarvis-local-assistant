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
import random
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

from mounir import config, llm, tools, trace
from mounir.agent import Agent
from mounir.memory import Conversation


# Shared so the Esc watcher, the thinking spinner, and the terminal confirm
# prompt don't fight over stdin/stderr during a turn. The lock guards stdin;
# fd/old let the confirm flip the terminal back to normal line mode to read.
_io: dict = {"lock": threading.Lock(), "fd": None, "old": None, "spinner": None}


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
    _io["fd"], _io["old"] = fd, old

    def _watch() -> None:
        try:
            tty.setcbreak(fd)
            while not stop.is_set():
                # Read under the shared lock so a confirm prompt (which borrows
                # stdin in cooked mode) isn't fighting us for the keystrokes.
                with _io["lock"]:
                    if select.select([sys.stdin], [], [], 0.05)[0]:
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
        _io["fd"], _io["old"] = None, None


# Claude-Code-style filler words shown while Mounir is working. Pure flavour.
_THINK_WORDS = [
    "Thinking", "Pondering", "Cooking", "Brewing", "Scheming", "Noodling",
    "Percolating", "Ruminating", "Conjuring", "Crunching", "Mulling", "Churning",
    "Tinkering", "Hatching", "Plotting", "Computing", "Wrangling", "Vibing",
    "Synthesizing", "Spelunking", "Finagling", "Marinating", "Concocting",
]
_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Spinner:
    """A moving shape + rotating word printed to stderr while we wait.

    Stops and wipes its line the moment the reply starts. No-op when stderr
    isn't a terminal (piped output), so logs stay clean.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # One word picked per request; it persists until the reply is done.
        self._word = random.choice(_THINK_WORDS)

    @staticmethod
    def _clear() -> None:
        sys.stderr.write("\r\033[2K")  # carriage return + erase the whole line
        sys.stderr.flush()

    def start(self) -> None:
        if not sys.stderr.isatty():
            return
        self._stop.clear()
        # Let trace wipe our line before it prints, so its output never lands
        # on top of the spinner (which would leave a frozen leftover frame).
        trace.set_pre_output(self._clear)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        start = time.time()
        i = 0
        while not self._stop.is_set():
            frame = _SPIN_FRAMES[i % len(_SPIN_FRAMES)]
            i += 1
            secs = int(time.time() - start)
            # Write under trace's lock so frames never interleave with trace lines.
            with trace.output_lock():
                sys.stderr.write(
                    f"\r\033[2K{trace.LAV}{frame} {self._word}…{trace.RESET}"
                    f" {trace.DIM}({secs}s · esc to interrupt){trace.RESET}"
                )
                sys.stderr.flush()
            self._stop.wait(0.09)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=0.3)
        self._thread = None
        trace.set_pre_output(None)
        if sys.stderr.isatty():
            with trace.output_lock():
                self._clear()


def _terminal_confirm(action: str) -> bool:
    """Ask the user to confirm an action in the terminal — y = yes.

    A tool calls this from the agent's worker thread mid-turn, while the Esc
    watcher and the thinking spinner are also live. So we stop the spinner and
    borrow stdin in normal line mode under the shared lock; the watcher backs
    off, the prompt echoes properly, then we hand the terminal back.
    """
    spinner = _io.get("spinner")
    if spinner is not None:
        spinner.stop()

    fd, old = _io.get("fd"), _io.get("old")
    prompt = (
        f"\n  {trace.PURPLE}⚠{trace.RESET} {trace.BOLD}confirm{trace.RESET} {action}\n"
        f"  {trace.DIM}y/N ›{trace.RESET} "
    )

    if not (sys.stdin.isatty() and fd is not None):
        try:                       # piped / no Esc watcher: just prompt plainly
            return input(prompt).strip().lower() in ("y", "yes")
        except EOFError:
            return False

    import termios
    import tty

    with _io["lock"]:              # block the watcher's reads while we prompt
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)   # cooked: echo + line edit
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:
            pass
        try:
            answer = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        finally:
            try:
                tty.setcbreak(fd)   # restore raw mode for the Esc watcher
            except Exception:
                pass
    return answer in ("y", "yes")


def main() -> int:
    if not llm.is_up():
        print(
            "Can't reach Ollama. Start it with `ollama serve` "
            "(and make sure the model is pulled).",
            file=sys.stderr,
        )
        return 1

    agent = Agent()
    tools.confirm_fn = _terminal_confirm  # terminal confirm that coexists with Esc + spinner
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
        spinner = Spinner()
        _io["spinner"] = spinner  # so a confirm prompt can pause it
        spinner.start()
        try:
            with _esc_interrupts():
                for chunk in agent.respond(user_input):
                    if tokens == 0:
                        spinner.stop()  # first output — clear the thinking line
                    print(chunk, end="", flush=True)
                    tokens += 1
            spinner.stop()  # no chunks produced
        except llm.OllamaError as exc:
            spinner.stop()
            print(f"\n[error] {exc}")
            continue
        except KeyboardInterrupt:
            # Esc or Ctrl+C while working: drop this response, keep the session.
            spinner.stop()
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
