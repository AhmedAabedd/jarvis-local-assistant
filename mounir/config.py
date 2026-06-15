"""Central configuration for Mounir.

Everything tweakable lives here so the other modules stay clean.
Environment variables override the defaults.
"""

from __future__ import annotations

import os
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
