"""Text-to-speech via Piper.

Synthesizes a chunk of text to audio and plays it. Called once per sentence by
the voice loop so Mounir starts talking before the full reply is generated.
Piper, numpy and sounddevice are imported lazily.
"""

from __future__ import annotations

from . import config

_voice = None  # cached PiperVoice


def _load():
    global _voice
    if _voice is None:
        from pathlib import Path

        from piper import PiperVoice

        model_path = Path(config.PIPER_MODEL)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper voice not found at {model_path}. Download one (see README) "
                "or set MOUNIR_PIPER_MODEL."
            )
        _voice = PiperVoice.load(str(model_path))
    return _voice


def speak(text: str) -> None:
    """Synthesize `text` with Piper and play it through the default output."""
    text = text.strip()
    if not text:
        return

    import numpy as np
    import sounddevice as sd

    voice = _load()
    chunks = [
        np.frombuffer(raw, dtype=np.int16)
        for raw in voice.synthesize_stream_raw(text)
    ]
    if not chunks:
        return
    audio = np.concatenate(chunks)
    sd.play(audio, samplerate=voice.config.sample_rate)
    sd.wait()
