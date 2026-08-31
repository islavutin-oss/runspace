"""Conversations adapter — two-party on-platform messaging. See ADR-0001."""

from .in_memory import InMemoryConversations
from .models import Message, Sender, Thread, thread_key
from .protocol import Conversations

# SupabaseConversations needs the optional `supabase` package. Lazy so
# the protocol layer stays usable in CI / sandbox without the SDK.
try:
    from .supabase_conversations import SupabaseConversations
except ImportError:  # pragma: no cover
    SupabaseConversations = None  # type: ignore[assignment]

__all__ = [
    "Conversations",
    "Message",
    "Thread",
    "Sender",
    "thread_key",
    "InMemoryConversations",
    "SupabaseConversations",
]
