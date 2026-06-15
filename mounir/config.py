"""Central configuration for Mounir.

Everything tweakable lives here so the other modules stay clean.
Environment variables override the defaults, which makes it easy to point
the same code at a different Ollama host or model on the DELL.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Ollama -----------------------------------------------------------------

OLLAMA_HOST: str = os.environ.get("MOUNIR_OLLAMA_HOST", "http://localhost:11434")

# The custom model built from modelfiles/mounir.Modelfile.
# Falls back to plain qwen3:4b if you haven't run `ollama create` yet.
MODEL: str = os.environ.get("MOUNIR_MODEL", "mounir")

# Qwen3 supports a "thinking" mode. It's smarter but much slower — bad for
# voice. Off by default; flip per call when you actually want deep reasoning.
THINK: bool = os.environ.get("MOUNIR_THINK", "false").lower() in ("1", "true", "yes")

# Sampling — kept in sync with the Modelfile so API calls behave the same
# whether or not the custom model is used.
OPTIONS: dict = {
    "temperature": 0.6,
    "top_k": 20,
    "top_p": 0.95,
    "repeat_penalty": 1.0,
}

# How long Ollama keeps the model resident in RAM after a request.
# "30m" avoids the multi-second cold reload between messages.
KEEP_ALIVE: str = os.environ.get("MOUNIR_KEEP_ALIVE", "30m")

# --- Memory -----------------------------------------------------------------

DATA_DIR: Path = Path(
    os.environ.get("MOUNIR_DATA_DIR", Path.home() / ".mounir")
)

# Conversation history is trimmed to roughly this many of the most recent
# messages (system prompt excluded) before each request, to keep the prompt
# small and inference fast on CPU.
MAX_HISTORY_MESSAGES: int = int(os.environ.get("MOUNIR_MAX_HISTORY", "20"))

# Fallback personality, used only when talking to a base model that has no
# SYSTEM block baked in (e.g. raw qwen3:4b instead of the `mounir` build).
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
