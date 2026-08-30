"""Speech-to-text compatibility entry point.

The database-backed capability registry selects cloud-native,
OpenAI-compatible, or local adapters. Audio captured by the existing voice
loop is normalized to PCM16 WAV before dispatch. Heavy dependencies remain
lazy so importing this module does not load a speech runtime.
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

    adapter = runtime.get("adapter") or runtime.get("provider")
    if adapter in {"openai_compatible", "groq"}:
        return _transcribe_openai_compatible(audio, selected_language, runtime)

    import io
    import wave

    import numpy as np

    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    from .speech_adapters import transcribe as run_adapter

    result = run_adapter(
        buf.getvalue(),
        {
            **runtime,
            "language": selected_language or "auto",
        },
    )
    return result.text, result.language


def _transcribe_local(audio, language, runtime) -> tuple[str, str]:
    """Transcribe locally with faster-whisper. beam_size=1 keeps it fast on CPU."""
    model = _load(runtime["model"])
    segments, info = model.transcribe(audio, language=language, beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, info.language


def _transcribe_openai_compatible(audio, language, runtime) -> tuple[str, str]:
    """Compatibility wrapper for the former OpenAI-only private helper."""
    import io
    import wave

    import numpy as np

    # float32 [-1, 1] mono -> 16-bit PCM WAV in memory (what the API expects).
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(config.SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())
    from .speech_adapters import transcribe as run_adapter

    result = run_adapter(
        buf.getvalue(),
        {
            **runtime,
            "adapter": "openai_compatible",
            "language": language or "auto",
        },
    )
    return result.text, result.language
