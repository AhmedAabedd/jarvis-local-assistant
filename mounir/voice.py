"""Voice loops: push-to-talk (Stage 2) and hands-free wake-word (Stage 4).

Both reuse the same Agent (so memory, personality, and tools are identical) and
the same transcribe → stream → speak pipeline. They differ only in how a
recording is captured: Enter key vs. wake word + voice-activity detection.
"""

from __future__ import annotations

from . import audio, config, db, stt, tts
from .agent import Agent
from .sentences import iter_sentences


def _handle_utterance(agent: Agent, wav) -> None:
    """Transcribe one recording, stream Mounir's reply, and speak it."""
    text, lang = stt.transcribe(wav)
    if not text:
        print("[heard nothing]\n")
        return
    print(f"you ({lang}) ▸ {text}")
    print(f"{db.get_profile()['assistant_name'].lower()} ▸ ", end="", flush=True)
    try:
        for sentence in iter_sentences(agent.respond(text)):
            print(sentence, end=" ", flush=True)
            tts.speak(sentence)
    except Exception as exc:  # keep the loop alive on a single failure
        print(f"\n[error] {exc}")
    print("\n")


def run_voice_loop(agent: Agent | None = None) -> None:
    """Push-to-talk: press Enter to start/stop each recording."""
    agent = agent or Agent()
    print(f"{db.get_profile()['assistant_name']} voice online — model '{agent.model}'.")
    print("Press Enter to talk, Ctrl-C to quit.\n")

    while True:
        try:
            input("[Enter to talk] ")
            wav = audio.record_until_enter()
        except (EOFError, KeyboardInterrupt):
            print("\nLater.")
            return
        _handle_utterance(agent, wav)


def run_hands_free_loop(agent: Agent | None = None) -> None:
    """Hands-free: wait for the wake word, then record until you stop talking."""
    from . import wakeword  # lazy: pulls openwakeword only in this mode

    agent = agent or Agent()
    print(f"{db.get_profile()['assistant_name']} hands-free — model '{agent.model}'.")
    print(f"Say the wake word ('{config.WAKE_WORD}') to talk. Ctrl-C to quit.\n")

    while True:
        try:
            wakeword.wait_for_wake()
            print("(wake) 🎙️  listening…", flush=True)
            wav = audio.record_until_silence()
        except (EOFError, KeyboardInterrupt):
            print("\nLater.")
            return
        _handle_utterance(agent, wav)
