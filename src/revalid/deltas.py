"""Transient live-token channel for the FR-17 retest console (issue #140).

The retest transcript (``session_events``) is the durable, append-only record a
verdict is re-derived from (FR-10). This channel is the opposite: an in-memory,
per-session buffer of the tokens the model emits *while* it is thinking, so the
console can show the reasoning as it is written instead of a static spinner for
the length of an LLM call.

Deliberately **not persisted**. A half-finished thought is not evidence, and
writing it to the transcript would put text into the audit trail that no verdict
was ever derived from. Deltas are dropped as soon as the turn they belong to
lands as a real transcript event, and the whole buffer is dropped when the
session ends. A reader that misses them (a browser opened mid-turn) loses
nothing that matters — the persisted events still tell the whole story.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: Per-session cap on buffered deltas. A long reasoning stream on a local model
#: runs to hundreds of tokens; past this the oldest are dropped, because the
#: console only ever renders the tail and an unbounded buffer on a wedged session
#: would grow for as long as the process lives.
MAX_BUFFERED_DELTAS = 2000


@dataclass
class _Buffer:
    """One session's deltas plus the index of the first one still held."""

    chunks: list[str] = field(default_factory=list)
    #: Number of chunks dropped from the front, so indices stay monotonic across
    #: trimming and a reader's cursor never silently rewinds.
    dropped: int = 0


class DeltaChannel:
    """Thread-safe fan-out of live model tokens, keyed by retest session id.

    One writer (the agent step running on a worker thread) and one reader (the
    WebSocket loop on the event loop), so a plain lock is enough; readers pull
    with a cursor rather than being pushed to, which keeps the WebSocket handler
    a simple poll and avoids needing an async queue per connection.
    """

    def __init__(self) -> None:
        """Create an empty channel."""
        self._buffers: dict[int, _Buffer] = {}
        self._lock = threading.Lock()

    def publish(self, session_id: int, chunk: str) -> None:
        """Append one token chunk for ``session_id`` (ignored when empty)."""
        if not chunk:
            return
        with self._lock:
            buffer = self._buffers.setdefault(session_id, _Buffer())
            buffer.chunks.append(chunk)
            overflow = len(buffer.chunks) - MAX_BUFFERED_DELTAS
            if overflow > 0:
                del buffer.chunks[:overflow]
                buffer.dropped += overflow

    def read_after(self, session_id: int, cursor: int) -> tuple[str, int]:
        """Return the text buffered after ``cursor``, and the new cursor.

        Args:
            session_id: The session to read.
            cursor: The index returned by the previous call (0 to start).

        Returns:
            The concatenated new chunks (empty when there are none) and the
            cursor to pass next time. Chunks are joined here rather than sent
            individually because the console appends them to one growing string
            anyway, and one frame per token would flood the socket.
        """
        with self._lock:
            buffer = self._buffers.get(session_id)
            if buffer is None:
                return "", cursor
            total = buffer.dropped + len(buffer.chunks)
            if cursor >= total:
                return "", total
            start = max(cursor - buffer.dropped, 0)
            return "".join(buffer.chunks[start:]), total

    def clear(self, session_id: int) -> None:
        """Drop a session's buffer — its turn landed, or the session ended."""
        with self._lock:
            self._buffers.pop(session_id, None)


#: Process-local channel shared by the agent runner and the WebSocket route.
#: Process-local is the right scope: this app is one uvicorn process bound to
#: 127.0.0.1 (ADR-0002), the same assumption ``SessionRegistry`` already makes.
DELTAS = DeltaChannel()
