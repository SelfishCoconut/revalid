"""Unit tests for the FR-17 live-token channel (issue #140).

No I/O and no model: the channel is a plain in-memory buffer, and what matters
about it is that a reader's cursor never rewinds, that a bounded buffer drops
the *oldest* tokens rather than the newest, and that concurrent publishing from
a worker thread while the socket reads does not lose or duplicate text.
"""

from __future__ import annotations

import threading

from revalid.deltas import MAX_BUFFERED_DELTAS, DeltaChannel


def test_reader_receives_published_text_once() -> None:
    channel = DeltaChannel()
    channel.publish(1, "think")
    channel.publish(1, "ing")

    text, cursor = channel.read_after(1, 0)
    assert text == "thinking"

    # Nothing new: a second read at the same cursor yields nothing, so the
    # console appends each token exactly once.
    again, cursor2 = channel.read_after(1, cursor)
    assert again == ""
    assert cursor2 == cursor


def test_sessions_are_isolated() -> None:
    channel = DeltaChannel()
    channel.publish(1, "one")
    channel.publish(2, "two")
    assert channel.read_after(1, 0)[0] == "one"
    assert channel.read_after(2, 0)[0] == "two"


def test_unknown_session_reads_empty_without_moving_the_cursor() -> None:
    channel = DeltaChannel()
    assert channel.read_after(999, 0) == ("", 0)


def test_empty_chunks_are_ignored() -> None:
    # Models emit empty deltas; they must not advance the cursor or add noise.
    channel = DeltaChannel()
    channel.publish(1, "")
    assert channel.read_after(1, 0) == ("", 0)


def test_clear_drops_the_buffer() -> None:
    channel = DeltaChannel()
    channel.publish(1, "abc")
    channel.clear(1)
    assert channel.read_after(1, 0) == ("", 0)


def test_overflow_drops_oldest_and_keeps_the_cursor_monotonic() -> None:
    """A long stream trims from the front, and a stale cursor never rewinds.

    The console renders the tail, so the oldest tokens are the right ones to
    lose. The cursor must still only move forward, or a reader would replay text
    it had already appended.
    """
    channel = DeltaChannel()
    for i in range(MAX_BUFFERED_DELTAS + 50):
        channel.publish(1, f"{i}|")

    text, cursor = channel.read_after(1, 0)
    assert cursor == MAX_BUFFERED_DELTAS + 50  # counts everything ever published
    assert text.endswith(f"{MAX_BUFFERED_DELTAS + 49}|")
    assert "0|" in text  # the tail is intact...
    assert not text.startswith("0|")  # ...but the front was dropped

    # A reader that comes back with the returned cursor sees nothing stale.
    assert channel.read_after(1, cursor)[0] == ""


def test_concurrent_publish_and_read_loses_nothing() -> None:
    """The writer is a worker thread and the reader is the event loop (#156's lesson).

    Publishes from several threads while a reader drains, then asserts every
    published token is accounted for exactly once across all the reads.
    """
    channel = DeltaChannel()
    writers = 4
    per_writer = 200

    def publish(worker: int) -> None:
        for i in range(per_writer):
            channel.publish(7, f"<{worker}:{i}>")

    collected: list[str] = []
    stop = threading.Event()

    def read() -> None:
        cursor = 0
        while not stop.is_set():
            text, cursor = channel.read_after(7, cursor)
            if text:
                collected.append(text)
        text, _ = channel.read_after(7, cursor)
        collected.append(text)

    reader = threading.Thread(target=read)
    reader.start()
    threads = [threading.Thread(target=publish, args=(w,)) for w in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    reader.join()

    joined = "".join(collected)
    assert joined.count("<") == writers * per_writer
    for worker in range(writers):
        assert joined.count(f"<{worker}:0>") == 1
