"""External channels — bring messages from outside transports (Telegram, etc.) into the platform"""

from .buffer import ContextBuffer, get_buffer
from .pairing import (
    PAIRING_TTL_HOURS,
    FilePairingState,
    resolve_allow_list,
    resolve_dm_policy,
)
from .transport import (
    CallbackHandlerRegistry,
    ChannelTransport,
    InboundEvent,
    InlineButton,
    OutboundReply,
    pick_telegram_transport_mode,
)

__all__ = [
    "ContextBuffer",
    "get_buffer",
    "FilePairingState",
    "resolve_dm_policy",
    "resolve_allow_list",
    "PAIRING_TTL_HOURS",
    "CallbackHandlerRegistry",
    "ChannelTransport",
    "InboundEvent",
    "InlineButton",
    "OutboundReply",
    "pick_telegram_transport_mode",
]
