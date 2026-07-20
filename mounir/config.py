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
SYSTEM_PROMPT: str = (
    "You are Mounir, a private AI assistant that runs locally on Ahmed's own "
    "machine. The person you're talking to is Ahmed — your owner. You're his "
    "loyal right hand and you always have his back. You're sharp, direct, and "
    "quick-witted, with a dry sense of humor and a bit of sarcasm. You speak "
    "plainly and waste no words: no padded intros, no \"certainly!\", no "
    "corporate fluff. Get to the point. When you don't know something, say so "
    "straight instead of making it up. You are Mounir and only Mounir — never "
    "call yourself Qwen or any other name.\n\n"
    "You have tools for browser control, "
    "reading/writing files, and shell commands, plus specialists you "
    #"reach by tool call: delegate_to_coder (all coding), delegate_to_researcher "
    "reach by tool call: delegate_to_researcher (all web lookups), "
    "delegate_to_media (reading images, PDFs, audio, and video), "
    "delegate_to_knowledge (saving/updating/deleting long-term knowledge), "
    "delegate_to_system (volume, brightness, media playback, battery, Wi-Fi, "
    "lock/suspend — anything about this laptop's hardware), and "
    "delegate_to_email (ALL email: search, read, send, reply, labels, drafts). "
    "You have NO web search of your own.\n\n"
    "HARD RULES:\n"
    "1. Never claim you did something unless you actually called the tool THIS "
    "turn and saw its result. Do not write \"done\", \"task sent to the coder\", "
    "\"file updated\", or similar from your head — if you didn't call the tool, "
    "you didn't do it, and saying otherwise is lying. Perform actions by calling "
    "tools, never by describing them.\n"
    #"2. For anything involving CODE — writing new scripts or modules, editing or "
    #"refactoring existing code files, debugging, bug fixes — you MUST call "
    #"delegate_to_coder. The coder makes surgical edits; never rewrite a whole "
    #"file yourself for a small change. When the user tells you to use or ask the "
    #"coder, you MUST call delegate_to_coder — do not do it yourself.\n"
    "2. For anything you need to look up — current events, facts that may have "
    "changed, prices, docs, comparisons — you MUST call delegate_to_researcher. "
    "It returns a synthesized answer with sources; pass the sources along when "                             
    "they matter. Never answer a lookup from memory if it could be stale.\n"
    "3. For anything that requires LOOKING AT or LISTENING TO a file — an "
    "image, a PDF, a screenshot, an audio clip, a video — you MUST call "
    "delegate_to_media with the file path."
    "The media agent reads the file and returns a text report.\n"
    #"4. write_file and edit_file are only for simple, non-code text (a note, a "
    #"plain-text or config file): write_file creates or overwrites a whole file, "
    #"edit_file makes a surgical change to an existing one. Never use either to "
    #"create or edit code — that always goes to delegate_to_coder.\n"
    "4. For anything that changes long-term knowledge — \"remember this\", a "
    "new contact, a preference, a template, or forgetting/cleaning stored "
    "knowledge — you MUST call delegate_to_knowledge with what to store or "
    "remove. Never create, edit, or delete files in the knowledge folder "
    "yourself; you may still READ them with read_file.\n"
    "5. When you don't know something, say so straight instead of making it up."
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
# into the real address before delegating a send to the email agent (so a
# misheard name can't reach the mailbox).
KNOWLEDGE_DIR: Path = Path(
    os.environ.get("MOUNIR_KNOWLEDGE_DIR", Path(__file__).resolve().parent.parent / "knowledge")
)
CONTACTS_FILE: Path = KNOWLEDGE_DIR / "contacts.md"
# index.md is the always-loaded "menu" of the knowledge folder: it lists every
# knowledge file and when to open it. Only this small index rides in context;
# the model reads a specific file (read_file) on demand when a task needs it.
INDEX_FILE: Path = KNOWLEDGE_DIR / "index.md"

LOCATION: str = os.environ.get("MOUNIR_LOCATION", "Ezzahra, Ben Arous, Tunis, Tunisia")


def _build_context_message() -> str:
    h = Path.home()
    lines = [
        f"OS: {platform.system()} {platform.release()}",
        f"Home: {h}",
        f"Current directory: {Path.cwd()}",
        f"Downloads: {h / 'Downloads'}",
        f"Documents: {h / 'Documents'}",
        f"Desktop: {h / 'Desktop'}",
        f"Location: {LOCATION}",
    ]
    # Append the knowledge index (the "menu") so Mounir always knows what
    # knowledge files exist and when to read one. Kept small on purpose.
    try:
        index = INDEX_FILE.read_text(encoding="utf-8", errors="replace").strip()
        if index:
            lines.append("\nKnowledge available (read a file with read_file when needed):")
            lines.append(index)
    except OSError:
        pass
    return "\n".join(lines)


CONTEXT_MESSAGE: str = _build_context_message()


# --- Gemini -----------------------------------------------------------------
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
USE_GEMINI: bool = os.environ.get("USE_GEMINI", "false").lower() in ("1", "true", "yes")
# Google's OpenAI-compatible endpoint — lets llm.gemini_chat reuse the same
# message/tool format as the other specialists, no google-genai SDK needed.
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


# --- Telegram bridge ---------------------------------------------------------
# Token from @BotFather. The bridge (telegram_cli.py) long-polls Telegram, so
# everything is an OUTBOUND connection — nothing on this machine is exposed.
TELEGRAM_BOT_TOKEN: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# The ONE chat allowed to talk to the assistant (the bot is publicly findable,
# so anyone could message it otherwise). Leave unset for first-run discovery:
# the bridge replies to any message with that chat's id so you can export it.
TELEGRAM_CHAT_ID: str = os.environ.get("TELEGRAM_CHAT_ID", "")


# --- Groq ---------------------------------------------------------------
GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL: str = os.environ.get("GROQ_MODEL", "qwen/qwen3-32b")
USE_GROQ: bool = os.environ.get("USE_GROQ", "false").lower() in ("1", "true", "yes")


# --- Mistral ----------------------------------------------------------------
MISTRAL_API_KEY: str = os.environ.get("MISTRAL_API_KEY", "")
MISTRAL_MODEL: str = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
USE_MISTRAL: bool = os.environ.get("USE_MISTRAL", "false").lower() in ("1", "true", "yes")


# --- NVIDIA (build.nvidia.com) — powers the coder specialist ----------------
NVIDIA_API_KEY: str = os.environ.get("NVIDIA_API_KEY", "")
NVIDIA_BASE_URL: str = os.environ.get(
    "NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"
)
CODER_MODEL: str = os.environ.get("CODER_MODEL", "minimaxai/minimax-m3")
# Omni (multimodal) model powering the media specialist: reads images, PDFs,
# audio, and video frames. Must be a model that accepts image/audio content
# parts on the NVIDIA OpenAI-compatible endpoint.
MEDIA_MODEL: str = os.environ.get("MEDIA_MODEL", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")


# --- Ollama Cloud (ollama.com) — powers the researcher specialist ------------
# Key from https://ollama.com/settings/keys. The cloud endpoint is
# OpenAI-compatible, so llm.ollama_cloud_chat reuses the same message/tool
# format as the other providers — no local ollama daemon involved.
OLLAMA_API_KEY: str = os.environ.get("OLLAMA_API_KEY", "")
OLLAMA_CLOUD_BASE_URL: str = os.environ.get(
    "OLLAMA_CLOUD_BASE_URL", "https://ollama.com/v1"
)
RESEARCHER_MODEL: str = os.environ.get("RESEARCHER_MODEL", "nemotron-3-super:cloud")


# --- Email specialist (Gmail via MCP) -----------------------------------------
# The email agent spawns this MCP server over stdio for each task and uses
# whatever tools the server advertises — no hand-written Gmail schemas.
# One-time OAuth setup: see specialists/email.py run() for the exact steps.
GMAIL_MCP_COMMAND: str = os.environ.get(
    "GMAIL_MCP_COMMAND", "npx -y @gongrzhe/server-gmail-autoauth-mcp"
)
# ~15 tool schemas per call needs a solid tool-caller. On Ollama Cloud like
# the researcher: gpt-oss answers in ~1-2s (NVIDIA's 49b queued 7-22s/call).
EMAIL_MODEL: str = os.environ.get("EMAIL_MODEL", "gpt-oss:120b-cloud")


# --- Cloud text-to-speech (Google Cloud TTS) --------------------------------
# Which backend tts.speak() uses: "google" (cloud) or "piper" (local).
# Defaults to piper so nothing changes until you opt in with MOUNIR_TTS_BACKEND.
TTS_BACKEND: str = os.environ.get("MOUNIR_TTS_BACKEND", "piper").lower()
# Google Cloud TTS over REST + a plain API key (no service-account JSON). Make a
# key in the Google Cloud console with the "Cloud Text-to-Speech API" enabled.
# Free tier: ~1M chars/month on Neural2/WaveNet voices, refilled monthly.
GOOGLE_TTS_API_KEY: str = os.environ.get("GOOGLE_TTS_API_KEY", "")
GOOGLE_TTS_LANGUAGE: str = os.environ.get("GOOGLE_TTS_LANGUAGE", "en-US")
GOOGLE_TTS_VOICE: str = os.environ.get("GOOGLE_TTS_VOICE", "en-US-Neural2-D")


# --- Cloud speech-to-text (Groq Whisper) ------------------------------------
# Which backend stt.transcribe() uses: "groq" (cloud) or "local" (faster-whisper).
# Defaults to local so nothing changes until you opt in with MOUNIR_STT_BACKEND.
STT_BACKEND: str = os.environ.get("MOUNIR_STT_BACKEND", "local").lower()
# Groq's OpenAI-compatible audio endpoint; reuses GROQ_API_KEY above.
# whisper-large-v3-turbo is multilingual and ~216x real-time.
GROQ_BASE_URL: str = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_STT_MODEL: str = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3-turbo")
