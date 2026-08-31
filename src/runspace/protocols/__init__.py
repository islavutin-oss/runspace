"""Adapter pattern services — see ADR-0001."""

from .clock import Clock, FrozenClock, RealClock, get_clock
from .embeddings import Embeddings
from .file_storage import FileMetadata, FileStorage
from .store import Store
from .transcriber import Transcriber, TranscriptionResult
from .transport import Attachment, IncomingMessage, MessageCallback, Transport
from .vision import Vision

# Names exposed via lazy attribute access — see __getattr__ below.
_LAZY_REGISTRY_NAMES = frozenset(
    {
        "get_embeddings",
        "get_file_storage",
        "get_store",
        "get_transport",
        "get_vision",
        "is_sandbox",
        "reset",
    }
)


def __getattr__(name: str):
    """PEP 562 — load registry helpers on first access.

    Keeps `pip install runspace-contracts` light: the standalone package
    has only Protocols + pure-data, no transport/storage backends.
    Calling any `get_*` helper triggers the registry import, which in
    turn pulls in concrete adapter modules (and their httpx/supabase
    deps). Hosts that install the full workspace product (runspace
    extras) have those deps available; hosts that don't shouldn't be
    calling the factories anyway.
    """
    if name in _LAZY_REGISTRY_NAMES:
        from . import registry as _registry

        return getattr(_registry, name)
    raise AttributeError(f"module 'protocols' has no attribute {name!r}")


__all__ = [
    "get_store",
    "get_vision",
    "get_transport",
    "get_clock",
    "get_file_storage",
    "get_embeddings",
    "is_sandbox",
    "reset",
    "Store",
    "Vision",
    "Transport",
    "Clock",
    "FileStorage",
    "FileMetadata",
    "Embeddings",
    "FrozenClock",
    "RealClock",
    "Attachment",
    "IncomingMessage",
    "MessageCallback",
    "Transcriber",
    "TranscriptionResult",
]


def _register_with_agentino() -> None:
    """Point agentino's standard tools at this package's file storage.

    agentino ships a local-filesystem default so it works standalone. When
    runspace is present it owns storage — including the tenant scoping and
    the swappable backends — so the standard tools should go through here
    rather than writing to their own directory.
    """
    try:
        from agentino.tools.std import set_file_storage_provider
    except ImportError:
        return  # agentino's std tools are an optional extra

    # Resolve through the lazy facade at call time, so importing this package
    # does not drag in the registry and its backend dependencies.
    from runspace.protocols.registry import get_file_storage as _factory

    set_file_storage_provider(_factory)

    # Same idea for PDFs: agentino ships a plain renderer so its create_pdf
    # tool works standalone; when runspace is present it supplies the branded
    # one, which knows the tenant's colour, logo and footer.
    try:
        from agentino.tools.std import set_pdf_renderer
    except ImportError:
        return

    def _branded(html_body: str, title: str, out_path: str):
        from runspace.helpers.documents.branded_pdf import generate_branded_pdf

        head, _, sub = title.partition("\n")
        fn = getattr(generate_branded_pdf, "fn", generate_branded_pdf)
        return fn(head, sub, html_body, out_path)

    set_pdf_renderer(_branded)


_register_with_agentino()
