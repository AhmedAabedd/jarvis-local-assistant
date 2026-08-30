"""Text-to-speech.

Synthesizes a chunk of text to audio and plays it. Called once per sentence by
the voice loop so Mounir starts talking before the full reply is generated.

The capability registry in ``speech_adapters`` selects cloud-native,
OpenAI-compatible, or local runtimes. This module preserves the CLI playback
and legacy WAV-facing helpers while the web path retains the provider's real
audio media type. Heavy dependencies are imported lazily.
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
    """Synthesize text and normalize the provider response to PCM16 WAV."""
    text = str(text or "").strip()
    if not text:
        return b""
    runtime = db.get_voice_runtime("tts")
    adapter = runtime.get("adapter") or runtime.get("provider")
    # Preserve the long-standing private seams used by lightweight installs and
    # tests while the richer web path below retains the provider's real media type.
    if adapter == "google":
        return _synthesize_google_wav(text, runtime)
    if adapter == "moss_onnx":
        return _synthesize_moss_wav(text, runtime)
    if adapter == "piper":
        return _synthesize_piper_wav(text, runtime)
    if adapter == "openai_compatible":
        return _synthesize_openai_compatible_wav(text, runtime)
    from .speech_adapters import synthesize as run_adapter

    return run_adapter(text, runtime).as_wav()


def synthesize(text: str):
    """Return a normalized AudioResult with the provider's real media type."""
    text = str(text or "").strip()
    if not text:
        from .speech_adapters import AudioResult

        return AudioResult(b"")
    runtime = db.get_voice_runtime("tts")
    from .speech_adapters import synthesize as run_adapter

    return run_adapter(text, runtime)


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
    if not runtime.get("api_key") and not (runtime.get("headers") or {}).get(
        "Authorization"
    ):
        raise RuntimeError(
            "The selected Google text-to-speech configuration has no API key or authorization header."
        )
    language = str(runtime.get("language") or "").strip()
    if not language or language == "auto":
        raise RuntimeError(
            "The selected Google text-to-speech configuration needs a language code."
        )

    import base64
    import requests

    from .audio_api import endpoint_url

    payload = {
        "input": {"text": text},
        "voice": {
            "languageCode": language,
            "name": runtime.get("voice") or runtime["model"],
        },
        # LINEAR16 comes back as a WAV container, so we can read rate + PCM
        # straight out of it with the stdlib wave module.
        "audioConfig": {"audioEncoding": "LINEAR16"},
    }
    resp = requests.post(
        endpoint_url(runtime.get("base_url", ""), "text:synthesize"),
        params={"key": runtime["api_key"]} if runtime.get("api_key") else None,
        headers=runtime.get("headers") or None,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    audio_b64 = resp.json().get("audioContent")
    return base64.b64decode(audio_b64) if audio_b64 else b""


def _synthesize_openai_compatible_wav(text: str, runtime: dict) -> bytes:
    """Compatibility wrapper for callers of the former private helper."""
    from .speech_adapters import synthesize as run_adapter

    if "provider_options" in runtime:
        return run_adapter(
            text, {**runtime, "adapter": "openai_compatible"}
        ).as_wav()
    result = run_adapter(
        text,
        {**runtime, "adapter": "openai_compatible", "provider_options": {"output_format": "wav"}},
    )
    return result.data


def _play_wav(wav_bytes: bytes) -> None:
    import io
    import wave

    import numpy as np

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        sample_rate = wf.getframerate()
        frames = wf.readframes(wf.getnframes())
    _play(np.frombuffer(frames, dtype=np.int16), sample_rate)
