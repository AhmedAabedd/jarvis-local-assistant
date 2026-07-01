"""Speech-to-text.

Two backends, chosen by config.STT_BACKEND:
- "groq":  Groq Whisper over its OpenAI-compatible REST endpoint (fast, cloud).
- "local": faster-whisper / CTranslate2, resident in-process (offline).
Heavy deps (faster_whisper, requests, numpy) are imported lazily so this module
is safe to import without them installed.
"""

from __future__ import annotations

from . import config

_model = None  # cached WhisperModel


def _load():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=config.WHISPER_DEVICE,
            compute_type=config.WHISPER_COMPUTE_TYPE,
        )
    return _model


def transcribe(audio, language: str | None = config.WHISPER_LANGUAGE) -> tuple[str, str]:
    """Transcribe a float32 mono 16 kHz numpy array.

    Returns (text, detected_language).
    """
    if audio is None or len(audio) == 0:
        return "", language or ""

    if config.STT_BACKEND == "groq":
        return _transcribe_groq(audio, language)
    return _transcribe_local(audio, language)


def _transcribe_local(audio, language) -> tuple[str, str]:
    """Transcribe locally with faster-whisper. beam_size=1 keeps it fast on CPU."""
    model = _load()
    segments, info = model.transcribe(audio, language=language, beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, info.language


def _transcribe_groq(audio, language) -> tuple[str, str]:
    """Transcribe by uploading the clip to Groq's Whisper endpoint."""
    if not config.GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set (needed for MOUNIR_STT_BACKEND=groq)."
        )

    import io
    import wave

    import numpy as np
    import requests

    # float32 [-1, 1] mono -> 16-bit PCM WAV in memory (what the API expects).
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    buf.seek(0)

    data = {"model": config.GROQ_STT_MODEL, "response_format": "verbose_json"}
    if language:
        data["language"] = language  # otherwise Groq auto-detects
    resp = requests.post(
        f"{config.GROQ_BASE_URL}/audio/transcriptions",
        headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"},
        files={"file": ("audio.wav", buf, "audio/wav")},
        data=data,
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    return (body.get("text") or "").strip(), body.get("language") or language or ""
