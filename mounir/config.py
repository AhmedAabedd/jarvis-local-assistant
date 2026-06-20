"""Central configuration for Mounir.

Everything tweakable lives here so the other modules stay clean.
Environment variables override the defaults.
"""

from __future__ import annotations

import os, platform
from pathlib import Path

# --- Model ------------------------------------------------------------------

# The custom model built from modelfiles/mounir.Modelfile (FROM qwen3:8b).
MODEL: str = os.environ.get("MOUNIR_MODEL", "mounir")

# Qwen3 thinking mode. Off by default (smarter but much slower); the 8B stock
# template honours this through the API, so think=False disables it directly.
THINK: bool = os.environ.get("MOUNIR_THINK", "false").lower() in ("1", "true", "yes")

# --- Memory -----------------------------------------------------------------

DATA_DIR: Path = Path(os.environ.get("MOUNIR_DATA_DIR", Path.home() / ".mounir"))

# Conversation history is trimmed to roughly this many of the most recent
# messages (system prompt excluded) before each request.
MAX_HISTORY_MESSAGES: int = int(os.environ.get("MOUNIR_MAX_HISTORY", "20"))

# Fallback personality, used only when talking to a base model that has no
# SYSTEM block baked in (e.g. raw qwen3:8b instead of the `mounir` build).
SYSTEM_PROMPT: str = (
    "You are Mounir, a private AI assistant that runs locally on Ahmed's own "
    "machine. The person you're talking to is Ahmed — your owner. You're his "
    "loyal right hand and you always have his back. You're sharp, direct, and "
    "quick-witted, with a dry sense of humor and a bit of sarcasm. You speak "
    "plainly and waste no words: no padded intros, no \"certainly!\", no "
    "corporate fluff. Get to the point. When you don't know something, say so "
    "straight instead of making it up. You are Mounir and only Mounir — never "
    "call yourself Qwen or any other name."
)

# --- Voice (Stage 2) --------------------------------------------------------

# Mic capture / Whisper both work at 16 kHz mono.
SAMPLE_RATE: int = int(os.environ.get("MOUNIR_SAMPLE_RATE", "16000"))

# Speech-to-text via faster-whisper (CTranslate2 — much faster than vanilla
# Whisper on CPU). "small" is the floor for decent Arabic + English; drop to
# "base" if you need more speed.
WHISPER_MODEL: str = os.environ.get("MOUNIR_WHISPER_MODEL", "small")
WHISPER_DEVICE: str = os.environ.get("MOUNIR_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE: str = os.environ.get("MOUNIR_WHISPER_COMPUTE", "int8")
# None = auto-detect language per utterance (good for AR/EN mixing).
# Set to "en" or "ar" to force it and skip detection.
WHISPER_LANGUAGE: str | None = os.environ.get("MOUNIR_WHISPER_LANG") or None

# Text-to-speech via Piper. Point this at a downloaded voice (.onnx file);
# its matching .onnx.json must sit next to it. See README for the download.
PIPER_MODEL: str = os.environ.get(
    "MOUNIR_PIPER_MODEL", str(DATA_DIR / "voices" / "en_US-amy-medium.onnx")
)

# --- Wake word + hands-free (Stage 4) ---------------------------------------

# openwakeword pretrained model to trigger on. Built-ins include "hey_jarvis",
# "alexa", "hey_mycroft". A custom "hey_mounir" needs training (see README).
WAKE_WORD: str = os.environ.get("MOUNIR_WAKE_WORD", "hey_jarvis")
WAKE_THRESHOLD: float = float(os.environ.get("MOUNIR_WAKE_THRESHOLD", "0.5"))

# Hands-free recording stops on silence, detected by frame loudness (RMS).
# Raise if it cuts you off in a noisy room; lower if it won't stop on quiet.
VAD_ENERGY_THRESHOLD: float = float(os.environ.get("MOUNIR_VAD_ENERGY", "0.015"))
# Stop recording after this much trailing silence once speech has started.
VAD_SILENCE_SECONDS: float = float(os.environ.get("MOUNIR_VAD_SILENCE", "1.0"))
# Hard cap on a single utterance.
VAD_MAX_SECONDS: float = float(os.environ.get("MOUNIR_VAD_MAX", "15"))

# --- Email (send) -----------------------------------------------------------

# SMTP for the send_email tool. Defaults target Gmail. Credentials come from
# the environment ONLY — never hard-code them here or they'd land in git.
# SMTP_PASS must be a Gmail "App Password" (16 chars, needs 2-Step Verification),
# not your normal account password.
SMTP_HOST: str = os.environ.get("MOUNIR_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT: int = int(os.environ.get("MOUNIR_SMTP_PORT", "587"))
SMTP_USER: str = os.environ.get("MOUNIR_SMTP_USER", "")  # full email address
SMTP_PASS: str = os.environ.get("MOUNIR_SMTP_PASS", "")  # Gmail App Password

LOCATION: str = os.environ.get("MOUNIR_LOCATION", "Tunis, Tunisia")


def _build_context_message() -> str:
    h = Path.home()
    return "\n".join([
        f"OS: {platform.system()} {platform.release()}",
        f"Home: {h}",
        f"Downloads: {h / 'Downloads'}",
        f"Documents: {h / 'Documents'}",
        f"Desktop: {h / 'Desktop'}",
        f"Location: {LOCATION}",
    ])


CONTEXT_MESSAGE: str = _build_context_message()


# --- Gemini -----------------------------------------------------------------
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
USE_GEMINI: bool = os.environ.get("USE_GEMINI", "false").lower() in ("1", "true", "yes")


# --- Groq ---------------------------------------------------------------
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "qwen/qwen3-32b")
USE_GROQ: bool = os.environ.get("USE_GROQ", "false").lower() in ("1", "true", "yes")


# --- Mistral ----------------------------------------------------------------
MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_MODEL: str = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
USE_MISTRAL: bool = os.environ.get("USE_MISTRAL", "false").lower() in ("1", "true", "yes")