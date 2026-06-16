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


def record_until_silence(
    silence_seconds: float | None = None,
    max_seconds: float | None = None,
    energy_threshold: float | None = None,
) -> "object":
    """Record from the mic and stop automatically after the talker pauses.

    Detects speech by frame loudness (RMS): waits for speech to begin, then
    ends once there's `silence_seconds` of quiet (or after `max_seconds`). Pure
    numpy — no external VAD dependency. Returns a float32 mono array in [-1, 1]
    at config.SAMPLE_RATE, the format faster-whisper wants.
    """
    import numpy as np
    import sounddevice as sd

    silence_seconds = (
        config.VAD_SILENCE_SECONDS if silence_seconds is None else silence_seconds
    )
    max_seconds = config.VAD_MAX_SECONDS if max_seconds is None else max_seconds
    energy_threshold = (
        config.VAD_ENERGY_THRESHOLD if energy_threshold is None else energy_threshold
    )

    frame_ms = 30
    frame_len = int(config.SAMPLE_RATE * frame_ms / 1000)
    silence_limit = int(silence_seconds * 1000 / frame_ms)
    max_frames = int(max_seconds * 1000 / frame_ms)

    frames: list = []
    started = False
    silent_run = 0

    with sd.InputStream(
        samplerate=config.SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=frame_len,
    ) as stream:
        for _ in range(max_frames):
            data, _ = stream.read(frame_len)
            pcm = data.flatten()
            frames.append(pcm)
            rms = float(np.sqrt(np.mean(pcm**2))) if pcm.size else 0.0
            if rms >= energy_threshold:
                started = True
                silent_run = 0
            elif started:
                silent_run += 1
                if silent_run >= silence_limit:
                    break

    if not frames:
        return np.zeros(0, dtype="float32")
    return np.concatenate(frames)
