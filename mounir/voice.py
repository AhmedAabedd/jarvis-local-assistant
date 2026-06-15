"""The Stage 2 voice loop: push-to-talk → STT → agent → speak per sentence.

This is the audio counterpart to cli.py. It reuses the same Agent (so memory
and personality are identical) and just swaps text I/O for microphone + Piper.
"""

from __future__ import annotations

from . import audio, stt, tts
from .agent import Agent
from .sentences import iter_sentences


def run_voice_loop(agent: Agent | None = None) -> None:
    agent = agent or Agent()
    print(f"Mounir voice online — model '{agent.model}'.")
    print("Press Enter to talk, Ctrl-C to quit.\n")

    while True:
        try:
            input("[Enter to talk] ")
            wav = audio.record_until_enter()
        except (EOFError, KeyboardInterrupt):
            print("\nLater.")
            return

        text, lang = stt.transcribe(wav)
        if not text:
            print("[heard nothing]\n")
            continue
        print(f"you ({lang}) ▸ {text}")

        # Stream the reply; speak each sentence the instant it completes.
        print("mounir ▸ ", end="", flush=True)
        try:
            for sentence in iter_sentences(agent.respond(text)):
                print(sentence, end=" ", flush=True)
                tts.speak(sentence)
        except Exception as exc:  # keep the loop alive on a single failure
            print(f"\n[error] {exc}")
        print("\n")
