"""Text-to-speech.

Synthesizes a chunk of text to audio and plays it. Called once per sentence by
the voice loop so Mounir starts talking before the full reply is generated.

Supported transports are OpenAI-compatible speech synthesis, Google Cloud TTS,
local Piper, and the multilingual MOSS-TTS-Nano ONNX runtime. The
OpenAI-compatible path is provider-neutral and also works with local servers
that implement the common ``/audio/speech`` contract.
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
    if runtime["provider"] == "moss_onnx":
        return _synthesize_moss_wav(text, runtime)
    if runtime["provider"] == "openai_compatible":
        return _synthesize_openai_compatible_wav(text, runtime)
    return _synthesize_piper_wav(text, runtime)


def discover_voices(provider: str, model: str) -> dict:
    """Return model-advertised voices when a provider defines discovery."""
    normalized_provider = str(provider or "").strip().lower()
    normalized_provider = db.VOICE_PROVIDER_ALIASES["tts"].get(
        normalized_provider, normalized_provider
    )
    if normalized_provider not in db.VOICE_PROVIDERS["tts"]:
        raise ValueError("TTS provider is not supported")
    model = str(model or "").strip()
    if not model:
        raise ValueError("TTS model is required")
    if normalized_provider == "moss_onnx":
        from . import moss_tts

        voices = moss_tts.list_builtin_voices(model)
        return {
            "provider": normalized_provider,
            "model": model,
            "discovery": "model_manifest",
            "voices": voices,
        }
    return {
        "provider": normalized_provider,
        "model": model,
        "discovery": "manual",
        "voices": [],
    }


def _synthesize_moss_wav(text: str, runtime: dict) -> bytes:
    from . import moss_tts

    voice = str(runtime.get("voice") or "").strip()
    if not voice:
        raise RuntimeError("The selected MOSS configuration has no voice identifier")
    return moss_tts.synthesize_wav(
        text,
        engine_path=runtime["model"],
        voice=voice,
    )


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

    from .audio_api import endpoint_url

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": runtime.get("language") or "en-US",
            "name": runtime.get("voice") or runtime["model"],
        },
        # LINEAR16 comes back as a WAV container, so we can read rate + PCM
        # straight out of it with the stdlib wave module.
        "audioConfig": {"audioEncoding": "LINEAR16"},
    }
    resp = requests.post(
        endpoint_url(runtime.get("base_url", ""), "text:synthesize"),
        params={"key": runtime["api_key"]},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    audio_b64 = resp.json().get("audioContent")
    return base64.b64decode(audio_b64) if audio_b64 else b""


def _synthesize_openai_compatible_wav(text: str, runtime: dict) -> bytes:
    """Synthesize WAV bytes using the common OpenAI audio-speech contract."""
    import requests

    from .audio_api import bearer_headers, endpoint_url

    payload = {
        "model": runtime["model"],
        "input": text,
        "voice": runtime["voice"],
        "response_format": "wav",
    }
    response = requests.post(
        endpoint_url(runtime.get("base_url", ""), "audio/speech"),
        headers=bearer_headers(runtime.get("api_key"), accept="audio/wav"),
        json=payload,
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def _play_wav(wav_bytes: bytes) -> None:
    import io
    import wave

    import numpy as np

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    _play(np.frombuffer(frames, dtype=np.int16), sample_rate)
