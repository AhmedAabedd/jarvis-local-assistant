"""Text-to-speech.

Synthesizes a chunk of text to audio and plays it. Called once per sentence by
the voice loop so Mounir starts talking before the full reply is generated.

Two backends, chosen by config.TTS_BACKEND:
- "google": Google Cloud TTS over REST (fast, ~1M free chars/month).
- "piper":  local Piper (offline, no quota).
Heavy deps (piper, requests, numpy, sounddevice) are imported lazily.
"""

from __future__ import annotations

from . import config

_GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

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
    """Synthesize `text` and play it through the default output device."""
    text = text.strip()
    if not text:
        return
    if config.TTS_BACKEND == "google":
        _speak_google(text)
    else:
        _speak_piper(text)


def _play(audio, sample_rate) -> None:
    """Play an int16 mono numpy array and block until it finishes."""
    import sounddevice as sd

    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def _speak_piper(text: str) -> None:
    """Synthesize `text` with local Piper."""
    import numpy as np

    voice = _load()
    # piper >= 1.3 yields AudioChunk objects from synthesize(); each carries the
    # int16 PCM bytes and the sample rate.
    parts: list = []
    sample_rate = None
    for chunk in voice.synthesize(text):
        parts.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
        sample_rate = chunk.sample_rate
    if not parts:
        return
    _play(np.concatenate(parts), sample_rate)


def _speak_google(text: str) -> None:
    """Synthesize `text` with Google Cloud TTS (REST + API key) and play it."""
    if not config.GOOGLE_TTS_API_KEY:
        raise RuntimeError(
            "GOOGLE_TTS_API_KEY is not set (needed for MOUNIR_TTS_BACKEND=google)."
        )

    import base64
    import io
    import wave

    import numpy as np
    import requests

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": config.GOOGLE_TTS_LANGUAGE,
            "name": config.GOOGLE_TTS_VOICE,
        },
        # LINEAR16 comes back as a WAV container, so we can read rate + PCM
        # straight out of it with the stdlib wave module.
        "audioConfig": {"audioEncoding": "LINEAR16"},
    }
    resp = requests.post(
        _GOOGLE_TTS_URL,
        params={"key": config.GOOGLE_TTS_API_KEY},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    audio_b64 = resp.json().get("audioContent")
    if not audio_b64:
        return

    with wave.open(io.BytesIO(base64.b64decode(audio_b64)), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    _play(np.frombuffer(frames, dtype=np.int16), sample_rate)
