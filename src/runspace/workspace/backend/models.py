"""Back-compat shim for the old `workspace.backend.models` import path."""

from __future__ import annotations

from runspace.contracts.chat import (  # noqa: F401
    AttachmentInput,
    ChatRequest,
    ChatResponse,
    FileAttachmentResponse,
    RoutineCreateRequest,
    RoutineDelivery,
)

__all__ = [
    "AttachmentInput",
    "ChatRequest",
    "ChatResponse",
    "FileAttachmentResponse",
    "RoutineCreateRequest",
    "RoutineDelivery",
]
