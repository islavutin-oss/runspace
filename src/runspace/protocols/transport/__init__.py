"""Transport adapter — see ADR-0001.

Concrete impls are lazy — `pip install runspace-contracts` ships the
Protocol without dragging in transport-specific deps (telegram client,
file watcher libs, …).
"""

from .protocol import Attachment, IncomingMessage, MessageCallback, Transport

try:
    from .file_inbox import FileInboxTransport
except ImportError:  # pragma: no cover
    FileInboxTransport = None  # type: ignore[assignment]

try:
    from .telegram import TelegramTransport
except ImportError:  # pragma: no cover
    TelegramTransport = None  # type: ignore[assignment]

__all__ = [
    "Attachment",
    "IncomingMessage",
    "MessageCallback",
    "Transport",
    "FileInboxTransport",
    "TelegramTransport",
]
