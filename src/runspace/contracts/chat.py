"""Wire shapes for the workspace chat protocol."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AttachmentInput(BaseModel):
    name: str
    type: str  # MIME type
    size: int  # bytes
    content: str = ""  # base64-encoded file content (text files decoded for agent)


class ChatRequest(BaseModel):
    app_id: str | None = None
    agent_id: str | None = None  # alias for app_id (backward compat)
    message: str = ""
    session_id: str = ""
    thread_id: str | None = None
    sender_name: str | None = None  # real user name from JWT (per-request identity)
    file_ids: list[str] = []  # uploaded file references (from /upload endpoint)
    # Legacy (still supported)
    media_base64: str | None = None
    media_mime: str | None = None
    attachments: list[AttachmentInput] = []

    @property
    def resolved_app_id(self) -> str:
        return self.app_id or self.agent_id or ""


class FileAttachmentResponse(BaseModel):
    name: str
    url: str
    size: int
    type: str


class ChatResponse(BaseModel):
    app_id: str
    app_name: str
    response: str
    session_id: str
    tools_used: list[str] = []
    attachments: list[FileAttachmentResponse] = []


class RoutineDelivery(BaseModel):
    """Where (if anywhere) the agent's reply goes when a routine fires.

    Three modes:
      - channel: post to a chat channel (target = channel slug)
      - dm:      send as a direct message (target = user_id)
      - silent:  run the prompt, don't post anywhere (target ignored).
                 The agent still uses tools and has side effects via
                 those tools; only the *announce* part is suppressed.
    """

    kind: Literal["channel", "dm", "silent"]
    target: str | None = None


class RoutineCreateRequest(BaseModel):
    agent_id: str
    schedule: str
    prompt: str
    description: str = ""
    enabled: bool = True
    # Required. Caller picks where the routine's output lands.
    delivery: RoutineDelivery
