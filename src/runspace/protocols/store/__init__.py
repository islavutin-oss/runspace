"""Store adapter — see ADR-0001."""

from .file_store import FileStore
from .in_memory import InMemoryStore
from .protocol import Store

# SupabaseStore needs the optional `supabase` package. Importing lazily
# keeps the protocol layer usable in CI / sandbox environments that
# don't install heavy backend SDKs.
try:
    from .supabase_store import SupabaseStore
except ImportError:  # pragma: no cover
    SupabaseStore = None  # type: ignore[assignment]

__all__ = ["Store", "FileStore", "InMemoryStore", "SupabaseStore"]
