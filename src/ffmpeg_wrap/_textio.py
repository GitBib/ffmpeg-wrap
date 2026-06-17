from __future__ import annotations

import collections
import sys
from typing import Any

# Maximum stderr tail retained for ``FFmpegError`` (ffmpeg's diagnostics land at
# the end of the stream, so a bounded tail keeps memory flat on long jobs while
# still capturing the actionable error text).
STDERR_TAIL_BYTES = 256 * 1024


def decode_text(data: bytes, encoding: str) -> str:
    """Decode tee-path bytes the way ``subprocess.run(text=True)`` returns text.

    Mirrors subprocess's universal-newline translation (``\\r\\n`` and ``\\r``
    collapse to ``\\n``) so the returned stdout and ``FFmpegError.stderr`` have
    the same shape regardless of which ``run()`` path produced them. Decoding
    stays lenient (``errors="replace"``) rather than subprocess's strict
    default: a stray byte must never turn a finished run — or the error report
    for a failed one — into a ``UnicodeDecodeError``.
    """
    return data.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")


class TeePump:
    """Bounded-tail stderr forwarder shared by the sync and async tee paths.

    Owns the per-chunk forward-to-sink logic and the bounded-tail ``deque``
    state. Both the sync thread loop and the async task loop feed it raw byte
    chunks via :meth:`feed`; only the *read loop* (``read1`` vs ``await
    receive``) differs and stays in each shell.

    The sink is resolved lazily at construction time. ``sys.stderr`` is normally
    a text wrapper over a binary ``.buffer``, which takes the raw byte chunks
    directly. When the active ``sys.stderr`` is text-only (no ``.buffer`` — e.g.
    a capturing wrapper) or ``None``, fall back and decode per chunk so the live
    tee is preserved instead of silently dropped on a bytes-to-text write.
    """

    def __init__(self, encoding: str, tail_limit: int = STDERR_TAIL_BYTES) -> None:
        self._encoding = encoding
        self._tail_limit = tail_limit
        buffer = getattr(sys.stderr, "buffer", None)
        if buffer is not None:
            self._sink: Any = buffer
            self._sink_is_text = False
        else:
            self._sink = sys.stderr
            self._sink_is_text = True
        self._tail: collections.deque[bytes] = collections.deque()
        self._tail_len = 0

    def feed(self, chunk: bytes) -> None:
        """Forward ``chunk`` to the live sink and retain it in the bounded tail."""
        try:
            # Forward raw bytes (or a lenient per-chunk decode for a text-only
            # sink) WITHOUT newline translation, so ffmpeg's ``\r`` progress
            # updates still overwrite in place on the terminal rather than
            # scrolling.
            self._sink.write(chunk.decode(self._encoding, errors="replace") if self._sink_is_text else chunk)
            self._sink.flush()
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._tail.append(chunk)
        self._tail_len += len(chunk)
        while self._tail_len > self._tail_limit and len(self._tail) > 1:
            self._tail_len -= len(self._tail.popleft())

    def tail_bytes(self) -> bytes:
        """Return the retained bounded tail joined into a single ``bytes``."""
        return b"".join(self._tail)
