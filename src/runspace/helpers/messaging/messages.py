"""Shared message types across agentino projects.

Both Acme and initech use identical message primitives.
Domain-specific types (Booking, Lead, etc.) stay in each project.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

MessageChannel = Literal["whatsapp", "telegram", "web", "cli", "api", "email"]
MessageDirection = Literal["in", "out"]
MessageType = Literal["text", "voice", "image"]


class NormalizedMessage(BaseModel):
    """Incoming message normalized across channels."""

    channel: MessageChannel
    channel_message_id: str | None = None

    tenant_id: str
    sender_id: str
    sender_name: str | None = None

    type: MessageType = "text"
    text: str | None = None
    media_url: str | None = None
    detected_language: str | None = None  # ISO 639-1

    timestamp: datetime
