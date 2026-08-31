"""Session Store — shared across agentino projects.

Supports in-memory (dev) and Redis (production).
Works with any Pydantic BaseModel as the context type.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class SessionStore(ABC, Generic[T]):
    """Abstract session store interface."""

    def __init__(self, context_type: type[T]):
        self._context_type = context_type

    @abstractmethod
    async def get(self, tenant_id: str, sender_id: str) -> T | None:
        pass

    @abstractmethod
    async def set(self, context: T, ttl_seconds: int = 3600) -> None:
        pass

    @abstractmethod
    async def delete(self, tenant_id: str, sender_id: str) -> None:
        pass

    def _make_key(self, tenant_id: str, sender_id: str) -> str:
        return f"session:{tenant_id}:{sender_id}"


class MemorySessionStore(SessionStore[T]):
    """In-memory session store for development."""

    def __init__(self, context_type: type[T] = BaseModel):
        super().__init__(context_type)
        self._sessions: dict[str, T] = {}
        self._expiry: dict[str, datetime] = {}

    async def get(self, tenant_id: str, sender_id: str) -> T | None:
        key = self._make_key(tenant_id, sender_id)
        if key in self._expiry and datetime.now() > self._expiry[key]:
            await self.delete(tenant_id, sender_id)
            return None
        return self._sessions.get(key)

    async def set(self, context: T, ttl_seconds: int = 3600) -> None:
        key = self._make_key(context.tenant_id, context.sender_id)  # type: ignore
        self._sessions[key] = context
        self._expiry[key] = datetime.now() + timedelta(seconds=ttl_seconds)

    async def delete(self, tenant_id: str, sender_id: str) -> None:
        key = self._make_key(tenant_id, sender_id)
        self._sessions.pop(key, None)
        self._expiry.pop(key, None)


class RedisSessionStore(SessionStore[T]):
    """Redis session store for production."""

    def __init__(
        self, redis_url: str = "redis://localhost:6379", context_type: type[T] = BaseModel
    ):
        super().__init__(context_type)
        self.redis_url = redis_url
        self._redis = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as redis

            self._redis = redis.from_url(self.redis_url)
        return self._redis

    async def get(self, tenant_id: str, sender_id: str) -> T | None:
        r = await self._get_redis()
        data = await r.get(self._make_key(tenant_id, sender_id))
        return self._context_type.model_validate_json(data) if data else None

    async def set(self, context: T, ttl_seconds: int = 3600) -> None:
        r = await self._get_redis()
        key = self._make_key(context.tenant_id, context.sender_id)  # type: ignore
        await r.setex(key, ttl_seconds, context.model_dump_json())

    async def delete(self, tenant_id: str, sender_id: str) -> None:
        r = await self._get_redis()
        await r.delete(self._make_key(tenant_id, sender_id))


def create_session_store(
    context_type: type[T] = BaseModel, redis_url: str | None = None
) -> SessionStore[T]:
    """Create appropriate session store based on config."""
    if redis_url:
        return RedisSessionStore(redis_url, context_type)
    return MemorySessionStore(context_type)
