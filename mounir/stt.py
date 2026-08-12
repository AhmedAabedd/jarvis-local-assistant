"""Speech-to-text.

Two transports are selected through the database-backed voice settings:
- ``openai_compatible`` uploads audio to an OpenAI-compatible transcription API.
- ``local_whisper`` runs faster-whisper / CTranslate2 in-process and offline.
Heavy deps (faster_whisper, requests, numpy) are imported lazily so this module
is safe to import without them installed.
"""

from __future__ import annotations

from . import config, db

_model = None  # cached WhisperModel
_model_name = None


def _load(model_name: str):
    global _model, _model_name
    if _model is None or _model_name != model_name:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            model_name,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
        _model_name = model_name
    return _model


def transcribe(audio, language: str | None = None) -> tuple[str, str]:
    """Transcribe a float32 mono 16 kHz numpy array.

    Returns (text, detected_language).
    """
    runtime = db.get_voice_runtime("stt")
    selected_language = language
    if selected_language is None:
        selected_language = runtime.get("language") or "auto"
    if selected_language == "auto":
        selected_language = None
    if audio is None or len(audio) == 0:
        return "", selected_language or ""

    if runtime["provider"] in {"openai_compatible", "groq"}:
        return _transcribe_openai_compatible(audio, selected_language, runtime)
    return _transcribe_local(audio, selected_language, runtime)


def _transcribe_local(audio, language, runtime) -> tuple[str, str]:
    """Transcribe locally with faster-whisper. beam_size=1 keeps it fast on CPU."""
    model = _load(runtime["model"])
    segments, info = model.transcribe(audio, language=language, beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, info.language


def _transcribe_openai_compatible(audio, language, runtime) -> tuple[str, str]:
    """Upload a clip using the common OpenAI audio-transcriptions contract."""
    import io
    import wave

    import numpy as np
    import requests

    from .audio_api import bearer_headers, endpoint_url

    # float32 [-1, 1] mono -> 16-bit PCM WAV in memory (what the API expects).
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    buf.seek(0)

    # Plain JSON is the broadest common response format across compatible
    # providers. Some additionally return a detected ``language`` field.
    data = {"model": runtime["model"], "response_format": "json"}
    if language:
        data["language"] = language  # otherwise Groq auto-detects
    resp = requests.post(
        endpoint_url(runtime.get("base_url", ""), "audio/transcriptions"),
        headers=bearer_headers(runtime.get("api_key"), accept="application/json"),
        files={"file": ("audio.wav", buf, "audio/wav")},
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    return (body.get("text") or "").strip(), body.get("language") or language or ""
