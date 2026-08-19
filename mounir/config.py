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

# Fallback personality, used by the LangGraph supervisor when talking to a
# base model that has no SYSTEM block baked in (e.g. raw qwen3:8b instead of
# the `mounir` build).
DEFAULT_USER_NAME: str = os.environ.get("MOUNIR_USER_NAME", "Ahmed")
DEFAULT_ASSISTANT_NAME: str = os.environ.get("MOUNIR_ASSISTANT_NAME", "Mounir")
DEFAULT_LOCATION: str = os.environ.get(
    "MOUNIR_LOCATION", "Ezzahra, Ben Arous, Tunis, Tunisia"
)
DEFAULT_LANGUAGE: str = os.environ.get("MOUNIR_LANGUAGE", "auto")


def build_system_prompt(profile: dict | None = None) -> str:
    profile = profile or {}
    user_name = profile.get("user_name") or DEFAULT_USER_NAME
    assistant_name = profile.get("assistant_name") or DEFAULT_ASSISTANT_NAME
    language = profile.get("preferred_language") or DEFAULT_LANGUAGE
    language_rule = {
        "auto": "Reply in the language the user is currently using.",
        "en": "Reply in English unless the user explicitly requests another language.",
        "fr": "Reply in French unless the user explicitly requests another language.",
        "ar": "Reply in Arabic unless the user explicitly requests another language.",
    }.get(language, "Reply in the language the user is currently using.")
    return (
        f"You are {assistant_name}, a private AI assistant that runs locally on "
        f"{user_name}'s own machine. The person you're talking to is {user_name} — "
        "your owner. You're their loyal right hand and you always have their back. "
        "You're sharp, direct, and quick-witted, with a dry sense of humor and a "
        "bit of sarcasm. You speak plainly and waste no words: no padded intros, "
        "no \"certainly!\", no corporate fluff. Get to the point. When you don't "
        "know something, say so straight instead of making it up. "
        f"You are {assistant_name} and only {assistant_name} — never call yourself "
        "Qwen or any other name.\n\n"
        f"{language_rule}\n\n"
        "The tools supplied with the current request are your exact capabilities. "
        "Some may delegate to focused specialists. Use their names and descriptions "
        "to choose the right one. Never claim a specialist exists and never call a "
        "tool that is not currently supplied; it may be inactive or unconfigured. "
        "You have no web search unless a currently supplied tool provides it.\n\n"
        "HARD RULES:\n"
        "1. Never claim you did something unless you actually called the tool THIS "
        "turn and saw its result. Do not write \"done\", \"task delegated\", "
        "\"file updated\", or similar from your head — if you didn't call the tool, "
        "you didn't do it, and saying otherwise is lying. Perform actions by calling "
        "tools, never by describing them.\n"
        "2. For anything you need to look up — current events, facts that may have "
        "changed, prices, docs, comparisons — use an available web or research "
        "specialist tool. If none is supplied, say that lookup capability is unavailable. "
        "Never answer a potentially stale lookup from memory.\n"
        "3. For EVERY local file or media operation — finding or listing paths; "
        "reading, creating, editing, appending, or converting files; and analyzing "
        "or generating documents, data, presentations, images, audio, or video — "
        "you MUST delegate to the available Files and Media specialist. Pass every "
        "name, location hint, and path the user supplied. Do not guess a path or "
        "use shell commands as a substitute. If the specialist is unavailable, "
        "say that local artifact capability is unavailable.\n"
        "4. For anything that changes long-term knowledge — \"remember this\", a "
        "new contact, a preference, a template, or forgetting/cleaning stored "
        "knowledge — use an available knowledge specialist with what to read, store, "
        "or remove. If none is supplied, say that knowledge capability is unavailable. "
        "Never manipulate files in the knowledge folder through another specialist.\n"
        "5. When you don't know something, say so straight instead of making it up."
    )


SYSTEM_PROMPT: str = build_system_prompt()


SUBAGENT_CAPABILITY_PROMPT = """\
CAPABILITY BOUNDARY
If the request cannot be completed with your available tools, do not guess, do
not call unrelated tools, and do not pretend. Reply using exactly this shape:

I can't complete this request with my available tools.
Reason: <one short reason>
What I can do:
- <two to five short capabilities relevant to this specialist>
"""


def profile_instruction(profile: dict | None = None) -> str:
    """Authoritative identity block reusable by specialist prompts."""
    profile = profile or {}
    user_name = profile.get("user_name") or DEFAULT_USER_NAME
    assistant_name = profile.get("assistant_name") or DEFAULT_ASSISTANT_NAME
    location = profile.get("location") or DEFAULT_LOCATION
    language = profile.get("preferred_language") or DEFAULT_LANGUAGE
    return (
        "CONFIGURED PROFILE (authoritative)\n"
        f"- User name: {user_name}\n"
        f"- Assistant name: {assistant_name}\n"
        f"- Location: {location}\n"
        f"- Preferred language: {language}\n"
        "Any different personal names in examples or older specialist instructions "
        "are placeholders. Use this configured profile."
    )


def specialist_system_prompt(base_prompt: str, profile: dict | None = None) -> str:
    """Apply the shared capability contract and current profile to a specialist."""
    if profile is None:
        try:
            from . import db

            profile = db.get_profile()
        except Exception:
            profile = None
    return "\n\n".join(
        (base_prompt.strip(), SUBAGENT_CAPABILITY_PROMPT.strip(), profile_instruction(profile))
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

# Mounir's "knowledge" folder: plain files the assistant reads for context.
# contacts.md is the address book — the model reads it to turn a spoken name
# into the real address before delegating a send to a mail agent (so a
# misheard name can't reach the mailbox).
KNOWLEDGE_DIR: Path = Path(
    os.environ.get("MOUNIR_KNOWLEDGE_DIR", Path(__file__).resolve().parent.parent / "knowledge")
)
CONTACTS_FILE: Path = KNOWLEDGE_DIR / "contacts.md"
# index.md is the always-loaded "menu" of the knowledge folder: it lists every
# knowledge file and when to open it. Only this small index rides in context;
# the supervisor delegates a specific lookup to the Knowledge specialist.
INDEX_FILE: Path = KNOWLEDGE_DIR / "index.md"

LOCATION: str = DEFAULT_LOCATION


def build_context_message(profile: dict | None = None) -> str:
    from . import path_search

    profile = profile or {}
    user_name = profile.get("user_name") or DEFAULT_USER_NAME
    assistant_name = profile.get("assistant_name") or DEFAULT_ASSISTANT_NAME
    location = profile.get("location") or DEFAULT_LOCATION
    language = profile.get("preferred_language") or DEFAULT_LANGUAGE
    h = Path.home()
    lines = [
        f"User: {user_name}",
        f"Assistant: {assistant_name}",
        f"OS: {platform.system()} {platform.release()}",
        f"Home: {h}",
        f"Current directory: {Path.cwd()}",
        f"Location: {location}",
        f"Preferred language: {language}",
    ]
    for key, path in sorted(path_search.xdg_user_directories().items()):
        lines.append(f"{key.title()}: {path}")
    # Append the knowledge index (the "menu") so Mounir always knows what
    # knowledge files exist and when to read one. Kept small on purpose.
    try:
        index = INDEX_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if index:
            lines.append("\nKnowledge available (delegate a lookup when needed):")
            lines.append(index)
    except OSError:
        pass
    return "\n".join(lines)


CONTEXT_MESSAGE: str = build_context_message()


# --- Gemini -----------------------------------------------------------------
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
USE_GEMINI: bool = os.environ.get("USE_GEMINI", "false").lower() in ("1", "true", "yes")
# Google's OpenAI-compatible endpoint uses the same shared chat transport as
# every other saved model, with no provider SDK required.
GEMINI_BASE_URL: str = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
)
# Powers the knowledge agent specialist (knowledge-folder curator).
KNOWLEDGE_MODEL: str = os.environ.get("KNOWLEDGE_MODEL", GEMINI_MODEL)
# Powers the system specialist (volume/brightness/media/power) — on NVIDIA,
# like the researcher/media. The free tiers elsewhere couldn't sustain it:
# Groq allows only 6-12k tokens/MINUTE (one task costs ~2.4k, so the SDK
# silently slept on 429s — the "stuck 20s before reporting" bug) and this
# Gemini key allows only 20 requests/DAY per model.
# (llama-3.3-70b answered correctly too but queues ~22s/call on the free
# tier; the 8b answers in ~1s and hardware commands don't need more brain.)
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
# Initial enabled state imported with the legacy token/chat values.
TELEGRAM_ENABLED: bool = os.environ.get("MOUNIR_TELEGRAM_ENABLED", "true").lower() in (
    "1", "true", "yes", "on"
)


# --- Groq ---------------------------------------------------------------
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "qwen/qwen3-32b")
USE_GROQ: bool = os.environ.get("USE_GROQ", "false").lower() in ("1", "true", "yes")


# --- Mistral ----------------------------------------------------------------
MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_MODEL: str = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_BASE_URL: str = os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1")
USE_MISTRAL: bool = os.environ.get("USE_MISTRAL", "false").lower() in ("1", "true", "yes")


# --- NVIDIA (build.nvidia.com) -----------------------------------------------
NVIDIA_API_KEY: str = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
# Omni (multimodal) model powering the media specialist: reads images, PDFs,
# audio, and video frames. Must be a model that accepts image/audio content
# parts on the NVIDIA OpenAI-compatible endpoint.
MEDIA_MODEL: str = os.environ.get("MEDIA_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")


# --- Ollama Cloud (ollama.com) — powers dynamic cloud specialists ------------
# Key from https://ollama.com/settings/keys. The cloud endpoint is
# OpenAI-compatible, so it uses the shared message/tool transport without a
# local Ollama daemon.
OLLAMA_API_KEY: str = os.environ.get("OLLAMA_API_KEY", "")
OLLAMA_CLOUD_BASE_URL: str = os.environ.get(
    "OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1"
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
OPENAI_TTS_API_KEY: str = os.environ.get(
    "MOUNIR_TTS_API_KEY", os.environ.get("OPENAI_API_KEY", "")
)
# Google Cloud TTS over REST + a plain API key (no service-account JSON). Make a
# key in the Google Cloud console with the "Cloud Text-to-Speech API" enabled.
# Free tier: ~1M chars/month on Neural2/WaveNet voices, refilled monthly.
GOOGLE_TTS_API_KEY: str = os.environ.get("GOOGLE_TTS_API_KEY", "")
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
OPENAI_STT_API_KEY: str = os.environ.get(
    "MOUNIR_STT_API_KEY",
    GROQ_API_KEY if STT_BACKEND == "groq" else os.environ.get("OPENAI_API_KEY", ""),
)
