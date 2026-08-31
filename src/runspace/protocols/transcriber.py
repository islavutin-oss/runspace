"""Transcriber Protocol — runtime-agnostic STT seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class TranscriptionResult:
    """One transcription. ISO-639-1 `language` may be empty if the
    backend didn't return language detection."""

    text: str
    model: str = ""
    language: str = ""
    duration_ms: int = 0


@runtime_checkable
class Transcriber(Protocol):
    """Object that can turn audio bytes into a TranscriptionResult.

    `transcribe` may be sync or async — runtimes adapt as needed. The
    gateway always `await`s, so concrete impls should be async.
    """

    async def transcribe(
        self,
        audio: bytes,
        mime: str = "audio/ogg",
        language: str | None = None,
    ) -> TranscriptionResult: ...


__all__ = ["Transcriber", "TranscriptionResult"]
