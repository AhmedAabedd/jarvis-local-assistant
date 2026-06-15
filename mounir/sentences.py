"""Turn a stream of token chunks into complete sentences.

The agent yields the LLM reply token-by-token. To start *speaking* before the
whole reply is ready (the trick that hides CPU latency), we buffer chunks and
flush each sentence the moment it's complete. Pure text logic — no audio deps —
so it's unit-testable on any machine.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

# A sentence ends at . ! ? (or the Arabic ؟) or a newline, followed by
# whitespace. Requiring trailing whitespace avoids splitting "3.14" or "v2.0"
# mid-number, since those have no space after the dot.
_BOUNDARY = re.compile(r"(.*?[.!?؟\n])(\s+)", re.DOTALL)


def _drain(buffer: str) -> tuple[list[str], str]:
    """Pull every complete sentence out of `buffer`, return them + the rest."""
    sentences: list[str] = []
    while True:
        match = _BOUNDARY.match(buffer)
        if not match:
            break
        sentence = match.group(1).strip()
        if sentence:
            sentences.append(sentence)
        buffer = buffer[match.end():]
    return sentences, buffer


def iter_sentences(chunks: Iterable[str]) -> Iterator[str]:
    """Yield complete sentences as soon as they form from a chunk stream."""
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        ready, buffer = _drain(buffer)
        yield from ready
    tail = buffer.strip()
    if tail:
        yield tail
