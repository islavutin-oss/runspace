"""ChannelTransport protocol — the seam where webhook vs polling backends plug in."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class InboundEvent:
    """Provider-agnostic shape of one inbound message.

    Built by the transport's adapter from raw provider payloads
    (Telegram update, Slack event, …) and handed to the message
    pipeline. The fields below are what the existing telegram
    handler uses today; new providers map their concepts onto this
    same shape.
    """

    provider: str  # "telegram" | "slack" | …
    chat_id: str  # group / DM id, provider-native string form
    sender_id: str  # message author id (provider-native)
    sender_handle: str  # @-handle / display name when present
    text: str = ""  # body for text messages, "" otherwise
    caption: str = ""  # caption attached to a file message
    file: dict | None = None  # provider's raw file_obj when present
    file_kind: str | None = None  # "document" | "photo" | None
    message_id: int | str | None = None
    thread_id: int | str | None = None  # topic / thread support
    raw: dict = field(default_factory=dict)  # original payload
    ts: float = 0.0


@dataclass
class InlineButton:
    """One inline-keyboard button in an OutboundReply.

    `callback_data` is what the provider hands back when the user
    clicks; the platform's CallbackHandlerRegistry routes it to the
    right Python function by prefix match.
    """

    label: str
    callback_data: str


@dataclass
class OutboundReply:
    """What the agent or platform asks the transport to deliver
    back into the channel."""

    chat_id: str
    text: str
    reply_to: int | str | None = None
    thread_id: int | str | None = None  # route reply into the
    # same topic the inbound came from
    buttons: list[list[InlineButton]] | None = None  #
    # rows × buttons


# ── Callback handler registry ────────────────────────


CallbackHandler = Callable[[dict], Awaitable[dict]]


class CallbackHandlerRegistry:
    """Routes inline-button `callback_data` to a Python coroutine.

    Handlers are registered with a *prefix*; the longest matching
    prefix wins (so `booking:` and `booking:cancel:` can coexist
    with the more specific one taking precedence). Callback payload
    shape: provider-agnostic dict containing at least
    `{provider, callback_data, sender_id, chat_id, message_id}`.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, CallbackHandler] = {}

    def register(self, prefix: str, handler: CallbackHandler) -> None:
        if not prefix:
            raise ValueError("prefix must be non-empty")
        self._handlers[prefix] = handler

    def lookup(self, callback_data: str) -> CallbackHandler | None:
        # Longest-prefix match.
        best: tuple[int, CallbackHandler] | None = None
        for prefix, handler in self._handlers.items():
            if callback_data.startswith(prefix):
                if best is None or len(prefix) > best[0]:
                    best = (len(prefix), handler)
        return best[1] if best else None

    def prefixes(self) -> list[str]:
        return list(self._handlers.keys())


# Handler the transport calls when a new InboundEvent arrives. Returns
# a status dict for diagnostics (the existing webhook route echoes
# this in its 200 response).
InboundHandler = Callable[[InboundEvent], Awaitable[dict]]


class ChannelTransport(Protocol):
    """Lifecycle + delivery contract every channel adapter satisfies."""

    provider: str  # "telegram" | …

    async def start(self) -> None:
        """Begin pumping inbound events. For webhook transports this
        is a no-op (the route is wired by the FastAPI app). For
        long-poll transports it spawns the background task.
        """
        ...

    async def stop(self) -> None:
        """Stop the pump cleanly. Long-poll transports cancel their
        task here and persist any offset state."""
        ...

    async def send(self, reply: OutboundReply) -> None:
        """Deliver a reply through the transport's API."""
        ...


# ── Transport selection ───────────────────────────────────────────────────


def pick_telegram_transport_mode(
    workspace_cfg: dict, tenant_id: str, bot_config: dict | None = None
) -> str:
    """Decide whether to wire telegram in webhook or polling mode.

    Order:
      1. env override — per-bot `TELEGRAM_TRANSPORT_<TENANT>_<BOT>`
         takes precedence over tenant-wide
         `TELEGRAM_TRANSPORT_<TENANT>`.
      2. `bot_config.transport` (multi-bot) or
         `messaging.telegram.transport` (single-bot legacy)
      3. default: `webhook`
    """
    tenant_key = tenant_id.upper().replace("-", "_")
    if bot_config and bot_config.get("name"):
        bot_key = (bot_config["name"] or "").upper().replace("-", "_")
        per_bot_env = os.environ.get(f"TELEGRAM_TRANSPORT_{tenant_key}_{bot_key}")
        if per_bot_env and per_bot_env.strip().lower() in ("polling", "webhook"):
            return per_bot_env.strip().lower()
    override = (os.environ.get(f"TELEGRAM_TRANSPORT_{tenant_key}") or "").strip().lower()
    if override in ("polling", "webhook"):
        return override
    if bot_config:
        declared = (bot_config.get("transport") or "").strip().lower()
        if declared in ("polling", "webhook"):
            return declared
    from .pairing import resolve_telegram_settings  # local: avoid cycle

    cfg = resolve_telegram_settings(workspace_cfg)
    declared = (cfg.get("transport") or "").strip().lower()
    if declared in ("polling", "webhook"):
        return declared
    return "webhook"
