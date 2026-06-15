"""Speech-to-text via faster-whisper (CTranslate2).

Much faster than openai-whisper on CPU. The model loads once on first use and
stays resident. faster-whisper is imported lazily so this module is safe to
import without it installed.
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

    Returns (text, detected_language). beam_size=1 keeps it fast on CPU.
    """
    if audio is None or len(audio) == 0:
        return "", language or ""

    model = _load()
    segments, info = model.transcribe(audio, language=language, beam_size=1)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    return text, info.language
