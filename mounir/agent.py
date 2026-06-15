"""The agent: ties the LLM client and conversation memory together.

This is the seam where Stage 3 tools will hook in. For now it just streams a
reply and records it. Keeping `respond` a generator means the CLI (and later
the TTS layer) can consume tokens sentence-by-sentence.
"""

from __future__ import annotations

from typing import Iterator

from . import config, llm
from .memory import Conversation


class Agent:
    def __init__(
        self,
        conversation: Conversation | None = None,
        model: str = config.MODEL,
    ) -> None:
        # If we're talking to the custom `mounir` build the SYSTEM block is
        # already baked in, so we don't double up the prompt. For a base model
        # we inject the fallback personality from config.
        system = None if model == "mounir" else config.SYSTEM_PROMPT
        self.conversation = conversation or Conversation(system_prompt=system)
        self.model = model

    def respond(self, user_input: str) -> Iterator[str]:
        """Stream Mounir's reply to one user turn, recording it in memory."""
        self.conversation.add_user(user_input)
        parts: list[str] = []
        for chunk in llm.chat_stream(
            self.conversation.to_messages(), model=self.model
        ):
            parts.append(chunk)
            yield chunk
        self.conversation.add_assistant("".join(parts))
