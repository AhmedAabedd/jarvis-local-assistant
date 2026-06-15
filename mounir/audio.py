"""Microphone capture for push-to-talk.

Heavy deps (sounddevice, numpy) are imported lazily so importing this module
never fails on a machine without audio set up — only calling the functions does.

First version is press-Enter-to-stop, which is robust in a plain terminal.
A hold-a-key variant can replace `record_until_enter` later without touching
the voice loop.
"""

from __future__ import annotations

from . import config


def record_until_enter(prompt: str = "🎙️  Listening… press Enter to stop.") -> "object":
    """Record from the default mic until the user presses Enter.

    Returns a float32 mono numpy array at config.SAMPLE_RATE (empty if silent).
    """
    import numpy as np
    import sounddevice as sd

    print(prompt, flush=True)
    frames: list = []

    def callback(indata, _frames, _time, status):  # runs on the audio thread
        if status:
            print(f"[audio] {status}", flush=True)
        frames.append(indata.copy())

    with sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=callback,
    ):
        input()  # blocks here; stream keeps filling `frames` until Enter

    if not frames:
        return np.zeros(0, dtype="float32")
    return np.concatenate(frames, axis=0).flatten()
