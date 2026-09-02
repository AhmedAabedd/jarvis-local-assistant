"""Central configuration for Mounir.

Everything tweakable lives here so the other modules stay clean.
Environment variables override the defaults.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

# --- Model ------------------------------------------------------------------

# Local fallback used only until the user selects a saved model in Agent Studio.
MODEL: str = os.environ.get("MOUNIR_MODEL", "mounir")

# --- Memory -----------------------------------------------------------------

DATA_DIR: Path = Path(os.environ.get("MOUNIR_DATA_DIR", Path.home() / ".mounir"))

# Conversation history is trimmed to roughly this many of the most recent
# messages (system prompt excluded) before each request.
MAX_HISTORY_MESSAGES: int = int(os.environ.get("MOUNIR_MAX_HISTORY", "20"))

# Dynamic subagent developer defaults. Agent-specific values are persisted in
# SQLite; these remain the source of truth for new agents and Reset to defaults.
SUBAGENT_MAX_TOOL_ROUNDS: int = max(
    1, min(int(os.environ.get("MOUNIR_MCP_MAX_ROUNDS", "8")), 100)
)
SUBAGENT_TOOL_TIMEOUT_SECONDS: float = max(
    1.0, float(os.environ.get("MOUNIR_MCP_TOOL_TIMEOUT", "60"))
)
SUBAGENT_TASK_TIMEOUT_SECONDS: float = max(
    SUBAGENT_TOOL_TIMEOUT_SECONDS,
    float(os.environ.get("MOUNIR_MCP_AGENT_TIMEOUT", "300")),
)

# Optional first-run profile values. Personal fields stay empty until configured.
DEFAULT_USER_NAME: str = os.environ.get("MOUNIR_USER_NAME", "").strip()
DEFAULT_ASSISTANT_NAME: str = os.environ.get("MOUNIR_ASSISTANT_NAME", "Mounir")
DEFAULT_LOCATION: str = os.environ.get("MOUNIR_LOCATION", "").strip()
DEFAULT_LANGUAGE: str = os.environ.get("MOUNIR_LANGUAGE", "auto")


def build_system_prompt(profile: dict | None = None) -> str:
    profile = profile or {}
    user_name = str(profile.get("user_name") or DEFAULT_USER_NAME).strip()
    assistant_name = profile.get("assistant_name") or DEFAULT_ASSISTANT_NAME
    user_identity = (
        f"The user is {user_name}. "
        if user_name
        else ""
    )
    return (
        f"You are {assistant_name}, the user's private local AI assistant. "
        f"{user_identity}"
        "Be loyal, sharp, direct, and quick-witted, with dry humor and occasional "
        "sarcasm. Skip padded introductions, automatic agreement, and corporate "
        "fluff. Never identify yourself as the underlying model or provider.\n\n"
        "The tools supplied for this request are your exact capabilities. Use their "
        "names and descriptions to choose the right tool or specialist. Never call "
        "or claim access to a capability that is not supplied.\n\n"
        "RULES\n"
        "1. Perform actions through tools. Claim an outcome only when a tool called "
        "this turn confirms it.\n"
        "2. Use an available web or research specialist for current or potentially "
        "stale information. If none is available, say so.\n"
        "3. Inspect images attached to the current message directly. Delegate every "
        "other local file or media operation to Files and Media, passing all supplied "
        "names, paths, and location hints. Never guess paths or substitute shell "
        "commands.\n"
        "4. Knowledge is your durable memory. Treat information stored there as "
        "knowledge you possess. Before answering any request that may rely on "
        "retained information, or claiming that you do not know, consult Knowledge. "
        "Answer naturally from what it returns. If nothing relevant is found, say "
        "that you have no saved knowledge about it. Use Knowledge for all durable "
        "memory retrieval and changes.\n"
        "5. If information or a required capability is unavailable, say so; never "
        "invent an answer or result.\n"
        "6. Prefer a direct purpose-built tool over visible GUI automation. Use "
        "Computer only when the request truly requires interaction with the visible "
        "desktop.\n"
        "7. If one valid route fails but another succeeds, report the verified outcome "
        "without narrating internal retries or disparaging another specialist."
    )
SUBAGENT_CAPABILITY_PROMPT = """\
CAPABILITY BOUNDARY
If your available capabilities cannot complete the task, do not guess or use
unrelated tools. Reply exactly:

I can't complete this request with my available tools.
Reason: <one short reason>
What I can do:
- <two to five relevant capabilities>
"""
SUBAGENT_SHARED_PROMPT = """\
SHARED SPECIALIST RULES
- Use only available capabilities and stop when the task is complete.
- Base every claimed outcome on real tool results.
- Report declined, failed, or timed-out actions plainly. Do not retry unless
  the result explicitly says it is safe.

FINAL RESPONSE
Return a complete, concrete report with everything your parent agent needs to
continue. Exclude unrelated commentary and unnecessary headings.
"""


def specialist_system_prompt(base_prompt: str) -> str:
    """Apply the shared capability contract to a specialist."""
    return "\n\n".join(
        (
            base_prompt.strip(),
            SUBAGENT_SHARED_PROMPT.strip(),
            SUBAGENT_CAPABILITY_PROMPT.strip(),
        )
    )

# --- Voice ------------------------------------------------------------------

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

# --- Wake word + hands-free -------------------------------------------------

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

def build_context_message(profile: dict | None = None) -> str:
    profile = profile or {}
    location = str(profile.get("location") or DEFAULT_LOCATION).strip()
    language = str(profile.get("preferred_language") or DEFAULT_LANGUAGE).strip()
    language_label = {
        "auto": "Automatic",
        "en": "English",
        "fr": "French",
        "ar": "Arabic",
    }.get(language, language)
    lines = [f"OS: {platform.system()} {platform.release()}"]
    if location:
        lines.append(f"Location: {location}")
    lines.append(f"Preferred language: {language_label}")
    return "\n".join(lines)
# --- Gemini -----------------------------------------------------------------
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
# Google's OpenAI-compatible endpoint uses the same shared chat transport as
# every other saved model, with no provider SDK required.
GEMINI_BASE_URL: str = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
)
# Powers the knowledge specialist's analysis and tool-selection loop.
KNOWLEDGE_MODEL: str = os.environ.get("KNOWLEDGE_MODEL", "gemini-2.5-flash")
# Powers the system specialist when no database model has been selected yet.
SYSTEM_MODEL: str = os.environ.get("SYSTEM_MODEL", "meta/llama-3.1-8b-instruct")


# --- Telegram bridge bootstrap ----------------------------------------------
# Agent Studio stores the live settings in SQLite. These environment values
# are imported only when the singleton DB record is first created, preserving
# existing installations. Long polling remains outbound-only.
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# The ONE chat allowed to talk to the assistant (the bot is publicly findable,
# so anyone could message it otherwise). Leave unset for first-run discovery:
# the bridge replies to any message with that chat's id so you can export it.
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")
# Initial enabled state imported with the environment bootstrap values.
TELEGRAM_ENABLED: bool = os.environ.get("MOUNIR_TELEGRAM_ENABLED", "true").lower() in (
    "1", "true", "yes", "on"
)
# Incoming photos, videos, and documents are retained here so later Telegram
# turns can still refer to them. Both values remain installation-configurable.
TELEGRAM_ATTACHMENT_DIR: Path = Path(
    os.environ.get(
        "MOUNIR_TELEGRAM_ATTACHMENT_DIR",
        str(DATA_DIR / "telegram" / "attachments"),
    )
)
TELEGRAM_MAX_ATTACHMENT_BYTES: int = int(
    os.environ.get("MOUNIR_TELEGRAM_MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024))
)

# Images uploaded through chat channels are stored as opaque conversation
# attachments. The supervisor receives them through the common multimodal Chat
# Completions content format; filesystem paths remain owned by Files and Media.
CHAT_ATTACHMENT_DIR: Path = Path(
    os.environ.get(
        "MOUNIR_CHAT_ATTACHMENT_DIR",
        str(DATA_DIR / "chat" / "attachments"),
    )
)
CHAT_ATTACHMENT_MAX_BYTES: int = int(
    os.environ.get("MOUNIR_CHAT_ATTACHMENT_MAX_BYTES", str(10 * 1024 * 1024))
)


# --- Groq speech bootstrap --------------------------------------------------
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")


# --- NVIDIA (build.nvidia.com) -----------------------------------------------
NVIDIA_API_KEY: str = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
# Omni (multimodal) model powering the media specialist: reads images, PDFs,
# audio, and video frames. Must be a model that accepts image/audio content
# parts on the NVIDIA OpenAI-compatible endpoint.
MEDIA_MODEL: str = os.environ.get(
    "MEDIA_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
)


# --- Text-to-speech ---------------------------------------------------------
# Initial transport imported into Agent Studio on first run. Supported values
# are "piper", "openai_compatible", and the legacy native "google" transport.
TTS_BACKEND: str = os.environ.get("MOUNIR_TTS_BACKEND", "piper").lower()
OPENAI_TTS_BASE_URL: str = os.environ.get(
    "MOUNIR_TTS_BASE_URL", "https://api.openai.com/v1"
)
OPENAI_TTS_MODEL: str = os.environ.get("MOUNIR_TTS_MODEL", "tts-1")
OPENAI_TTS_VOICE: str = os.environ.get("MOUNIR_TTS_VOICE", "alloy")
# Google Cloud TTS over REST + a plain API key (no service-account JSON). Make a
# key in the Google Cloud console with the "Cloud Text-to-Speech API" enabled.
# Free tier: ~1M chars/month on Neural2/WaveNet voices, refilled monthly.
GOOGLE_TTS_LANGUAGE: str = os.environ.get("GOOGLE_TTS_LANGUAGE", "en-US")
GOOGLE_TTS_VOICE: str = os.environ.get("GOOGLE_TTS_VOICE", "en-US-Neural2-D")


# --- Speech-to-text ---------------------------------------------------------
# "local"/"local_whisper" runs offline. "openai_compatible" connects any
# hosted or local service exposing the common audio-transcriptions contract.
# "groq" remains an accepted first-run alias for older installations.
STT_BACKEND: str = os.environ.get("MOUNIR_STT_BACKEND", "local").lower()
# Groq's OpenAI-compatible audio endpoint; reuses GROQ_API_KEY above.
# whisper-large-v3-turbo is multilingual and ~216x real-time.
GROQ_BASE_URL: str = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_STT_MODEL: str = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")
OPENAI_STT_BASE_URL: str = os.environ.get(
    "MOUNIR_STT_BASE_URL",
    GROQ_BASE_URL if STT_BACKEND == "groq" else "https://api.openai.com/v1",
)
OPENAI_STT_MODEL: str = os.environ.get(
    "MOUNIR_STT_MODEL",
    GROQ_STT_MODEL if STT_BACKEND == "groq" else "whisper-1",
)
