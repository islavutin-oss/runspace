"""Plugin contract for runspace tenants."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from fastapi import APIRouter, FastAPI

StartupHook = Callable[[FastAPI], Awaitable[None]]
ShutdownHook = Callable[[FastAPI], Awaitable[None]]
MiddlewareSpec = tuple[type, dict[str, Any]]


@runtime_checkable
class Plugin(Protocol):
    """Optional Protocol — module-level symbols a runspace plugin can export."""

    router: APIRouter | None
    routers: list[APIRouter] | None
    cron_executors: list[Any] | None
    startup_hooks: list[StartupHook] | None
    shutdown_hooks: list[ShutdownHook] | None
    middlewares: list[MiddlewareSpec] | None


__all__ = [
    "Plugin",
    "StartupHook",
    "ShutdownHook",
    "MiddlewareSpec",
]
