"""The agent: ties the LLM, conversation memory, and tools together.

`respond` runs the standard tool-calling loop:
  1. stream a model turn (with the tool schemas available);
  2. if the model asked to call tools, run them, record the results, and loop
     so it can use them;
  3. otherwise the streamed text is the final answer — record and stop.

Plain chat (no tool needed) streams straight through in one pass. Keeping
`respond` a generator lets the CLI and the voice layer consume it the same way.
"""

from __future__ import annotations

from typing import Iterator

from . import config, llm, tools
from .memory import Conversation

# Safety cap so a misbehaving model can't loop on tool calls forever.
MAX_TOOL_ROUNDS = 4


class Agent:
    def __init__(
        self,
        conversation: Conversation | None = None,
        model: str = config.MODEL,
        use_tools: bool = True,
    ) -> None:
        # If we're talking to the custom `mounir` build the SYSTEM block is
        # already baked in, so we don't double up the prompt. For a base model
        # we inject the fallback personality from config.
        system = None if model == "mounir" else config.SYSTEM_PROMPT
        # Explicit None check: an empty Conversation is falsy (len 0), so
        # `conversation or Conversation(...)` would wrongly discard it.
        if conversation is None:
            conversation = Conversation(system_prompt=system)
        self.conversation = conversation
        self.model = model
        self.tools = tools.SCHEMAS if use_tools else None

    def respond(self, user_input: str) -> Iterator[str]:
        """Stream Mounir's reply to one user turn, recording it in memory."""
        self.conversation.add_user(user_input)

        for _ in range(MAX_TOOL_ROUNDS):
            tool_calls: list = []
            parts: list[str] = []
            for chunk in llm.chat_stream(
                self.conversation.to_messages(),
                model=self.model,
                tools=self.tools,
                tool_calls_out=tool_calls,
            ):
                parts.append(chunk)
                yield chunk

            if not tool_calls:
                self.conversation.add_assistant("".join(parts))
                return

            self._record_tool_calls(parts, tool_calls)
            self._run_tools(tool_calls)

        # Exhausted the tool-round budget: do one final tool-free pass so the
        # user still gets an answer instead of silence.
        parts = []
        for chunk in llm.chat_stream(
            self.conversation.to_messages(), model=self.model, tools=None
        ):
            parts.append(chunk)
            yield chunk
        self.conversation.add_assistant("".join(parts))

    def _record_tool_calls(self, parts: list[str], tool_calls: list) -> None:
        self.conversation.add_message(
            {
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
            }
        )

    def _run_tools(self, tool_calls: list) -> None:
        for tc in tool_calls:
            result = tools.dispatch(tc.function.name, dict(tc.function.arguments))
            self.conversation.add_message(
                {"role": "tool", "tool_name": tc.function.name, "content": result}
            )
