"""The agent: ties the LLM, conversation memory, and tools together.

`respond` runs the standard tool-calling loop:
  1. Build a local `conversation` list from persistent history (never mutated back).
  2. Stream a model turn (with tool schemas available).
  3. If the model asked to call tools, run them, append assistant+tool messages
     to the local list only, and loop so it can use the results.
  4. Once the model replies with no tool calls, that's the final answer —
     persist only the user input and assistant reply to self.conversation.

Tool calls and results live only for the duration of the current turn,
exactly like the Odoo implementation.
"""

from __future__ import annotations

from typing import Iterator

from . import config, llm, tools
from .memory import Conversation

# Safety cap so a misbehaving model can't loop on tool calls forever.
MAX_TOOL_ROUNDS = 10


class Agent:
    def __init__(
        self,
        conversation: Conversation | None = None,
        model: str = config.MODEL,
        use_tools: bool = True,
    ) -> None:
        system = config.SYSTEM_PROMPT
        if conversation is None:
            conversation = Conversation(system_prompt=system)
        self.conversation = conversation
        self.model = model
        self.tools = tools.SCHEMAS if use_tools else None

    def respond(self, user_input: str) -> Iterator[str]:
        """Stream Mounir's reply to one user turn, recording it in memory."""
        self.conversation.add_user(user_input)

        # Local list for this turn only — tool results go here, never persisted.
        conversation = self.conversation.to_messages()

        for _ in range(MAX_TOOL_ROUNDS):
            tool_calls: list = []
            parts: list[str] = []
            for chunk in llm.chat_stream(
                conversation,
                model=self.model,
                tools=self.tools,
                tool_calls_out=tool_calls,
            ):
                parts.append(chunk)
                yield chunk

            if not tool_calls:
                self.conversation.add_assistant("".join(parts))
                return

            # Append assistant turn + tool results to local list only.
            conversation.append({
                "role": "assistant",
                "content": "".join(parts),
                "tool_calls": [
                    {
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    }
                    for tc in tool_calls
                ],
            })
            for i, tc in enumerate(tool_calls):
                result = tools.dispatch(tc.function.name, dict(tc.function.arguments))
                conversation.append({
                    "role": "tool",
                    "tool_name": tc.function.name,
                    "tool_call_id": f"call_{i}",
                    "content": result,
                })

        # Cap reached — final tool-free pass.
        parts = []
        for chunk in llm.chat_stream(conversation, model=self.model, tools=None):
            parts.append(chunk)
            yield chunk
        self.conversation.add_assistant("".join(parts))
