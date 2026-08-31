"""Ephemeral per-chat context buffer for external channels."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BufferedMessage:
    ts: float  # unix seconds
    sender: str  # @handle or "user_id" if no handle
    text: str

    def render(self) -> str:
        """One line for the caption. HH:MM in UTC → close enough."""
        hhmm = time.strftime("%H:%M", time.gmtime(self.ts))
        return f"{hhmm} {self.sender}: {self.text}"


class ContextBuffer:
    """Ring buffer of recent text messages for one chat.

    Bounded by max_messages (count) and window_seconds (age).
    push() appends; render() returns the LLM-facing caption block.
    Thread-safe — webhook handler may be hit concurrently for the
    same chat (rare but not impossible).
    """

    def __init__(self, *, max_messages: int = 20, window_seconds: int = 600):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._dq: deque[BufferedMessage] = deque(maxlen=max_messages)
        self._lock = threading.Lock()

    def push(self, *, sender: str, text: str, ts: float | None = None) -> None:
        if not text or not text.strip():
            return
        if ts is None:
            ts = time.time()
        with self._lock:
            self._dq.append(BufferedMessage(ts=ts, sender=sender, text=text.strip()))

    def edit(self, *, sender: str, text: str, original_ts: float) -> bool:
        """Best-effort in-place edit. Match by (sender, original_ts)
        within the window; replace text if found. Returns True if
        replaced. Never raises — edits are advisory."""
        with self._lock:
            for i, m in enumerate(self._dq):
                if m.sender == sender and abs(m.ts - original_ts) < 1.0:
                    self._dq[i] = BufferedMessage(ts=m.ts, sender=sender, text=text.strip())
                    return True
        return False

    def _prune(self) -> None:
        cutoff = time.time() - self.window_seconds
        with self._lock:
            while self._dq and self._dq[0].ts < cutoff:
                self._dq.popleft()

    def render(self, *, label: str = "Telegram chat context") -> str:
        """Caption text the agent sees alongside the file. Empty string
        if the buffer has nothing fresh — caller passes that through to
        the agent, which falls back to its no-context behavior."""
        self._prune()
        with self._lock:
            if not self._dq:
                return ""
            lines = [m.render() for m in self._dq]
        minutes = self.window_seconds // 60
        return f"[{label}, last {minutes} min]\n" + "\n".join(lines)

    def __len__(self) -> int:
        self._prune()
        with self._lock:
            return len(self._dq)


# ── Global registry of buffers, one per (tenant_id, chat_id) ──────────────

_BUFFERS: dict[tuple[str, str], ContextBuffer] = {}
_BUFFERS_LOCK = threading.Lock()


def get_buffer(
    tenant_id: str,
    chat_id: str,
    *,
    max_messages: int = 20,
    window_seconds: int = 600,
) -> ContextBuffer:
    """Return the ContextBuffer for this (tenant, chat). Lazy-creates
    on first use. Subsequent calls with different bounds are
    ignored — the binding's config sets the bounds at routine
    registration time, and the buffer keeps them."""
    key = (tenant_id, chat_id)
    with _BUFFERS_LOCK:
        b = _BUFFERS.get(key)
        if b is None:
            b = ContextBuffer(max_messages=max_messages, window_seconds=window_seconds)
            _BUFFERS[key] = b
        return b


def reset_all() -> None:
    """Drop every buffer. Test-only helper; don't call in prod."""
    with _BUFFERS_LOCK:
        _BUFFERS.clear()
