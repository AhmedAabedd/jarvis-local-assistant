"""Wake-word detection via openwakeword — fully local, CPU, ONNX.

Listens to the mic continuously and returns the moment the trigger phrase
(config.WAKE_WORD, e.g. "hey_jarvis") is heard. openwakeword and sounddevice
are imported lazily so this module is safe to import without them installed.
"""

from __future__ import annotations

from . import config

_model = None  # cached openwakeword Model

# openwakeword expects 16 kHz int16 audio in 80 ms chunks (1280 samples).
_FRAME = 1280


def _load():
    global _model
    if _model is None:
        import openwakeword
        from openwakeword.model import Model

        # Idempotent: downloads the pretrained models on first run only.
        openwakeword.utils.download_models()
        _model = Model(
            wakeword_models=[config.WAKE_WORD], inference_framework="onnx"
        )
    return _model


def wait_for_wake(threshold: float | None = None) -> None:
    """Block until the wake word is detected on the default microphone."""
    import sounddevice as sd

    threshold = config.WAKE_THRESHOLD if threshold is None else threshold
    model = _load()
    model.reset()

    with sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=_FRAME,
    ) as stream:
        while True:
            data, _ = stream.read(_FRAME)
            scores = model.predict(data.flatten())
            if scores and max(scores.values()) >= threshold:
                return
