"""Text-to-speech.

Synthesizes a chunk of text to audio and plays it. Called once per sentence by
the voice loop so Mounir starts talking before the full reply is generated.

Two backends, chosen by config.TTS_BACKEND:
- "google": Google Cloud TTS over REST (fast, ~1M free chars/month).
- "piper":  local Piper (offline, no quota).
Heavy deps (piper, requests, numpy, sounddevice) are imported lazily.
"""

from __future__ import annotations

from . import config, db

_voice = None  # cached PiperVoice
_voice_model = None


def _load(model: str | None = None):
    global _voice, _voice_model
    model = model or config.PIPER_MODEL
    if _voice is None or _voice_model != model:
        from pathlib import Path

        from piper import PiperVoice

        model_path = Path(model)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Piper voice not found at {model_path}. Download one (see README) "
                "or set MOUNIR_PIPER_MODEL."
            )
        _voice = PiperVoice.load(str(model_path))
        _voice_model = model
    return _voice


def speak(text: str) -> None:
    """Synthesize `text` and play it through the default output device."""
    text = text.strip()
    if not text:
        return
    wav_bytes = synthesize_wav(text)
    if wav_bytes:
        _play_wav(wav_bytes)


def _play(audio, sample_rate) -> None:
    """Play an int16 mono numpy array and block until it finishes."""
    import sounddevice as sd

    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def synthesize_wav(text: str) -> bytes:
    """Synthesize text with the selected database-backed voice configuration."""
    text = str(text or "").strip()
    if not text:
        return b""
    runtime = db.get_voice_runtime("tts")
    if runtime["provider"] == "google":
        return _synthesize_google_wav(text, runtime)
    return _synthesize_piper_wav(text, runtime)


def _synthesize_piper_wav(text: str, runtime: dict) -> bytes:
    import io
    import wave

    voice = _load(runtime["model"])
    parts: list[bytes] = []
    sample_rate = 22050
    for chunk in voice.synthesize(text):
        parts.append(chunk.audio_int16_bytes)
        sample_rate = chunk.sample_rate
    if not parts:
        return b""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(parts))
    return output.getvalue()


def _synthesize_google_wav(text: str, runtime: dict) -> bytes:
    """Synthesize with Google Cloud TTS and return its LINEAR16 WAV."""
    if not runtime.get("api_key"):
        raise RuntimeError(
            "The selected Google text-to-speech configuration has no API key."
        )

    import base64
    import requests

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": runtime.get("language") or "en-US",
            "name": runtime["model"],
        },
        # LINEAR16 comes back as a WAV container, so we can read rate + PCM
        # straight out of it with the stdlib wave module.
        "audioConfig": {"audioEncoding": "LINEAR16"},
    }
    resp = requests.post(
        f"{runtime['base_url'].rstrip('/')}/text:synthesize",
        params={"key": runtime["api_key"]},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    audio_b64 = resp.json().get("audioContent")
    return base64.b64decode(audio_b64) if audio_b64 else b""


def _play_wav(wav_bytes: bytes) -> None:
    import io
    import wave

    import numpy as np

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    _play(np.frombuffer(frames, dtype=np.int16), sample_rate)
