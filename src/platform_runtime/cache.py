import hashlib
import json
from typing import Any

from redis.asyncio import Redis


class Cache:
    def __init__(self, url: str | None, ttl_seconds: int) -> None:
        self.redis = Redis.from_url(url, decode_responses=True) if url else None
        self.ttl = ttl_seconds

    async def close(self) -> None:
        if self.redis:
            await self.redis.aclose()

    async def get(self, namespace: str, parts: list[str]) -> dict[str, Any] | None:
        if not self.redis:
            return None
        value = await self.redis.get(await self._key(namespace, parts))
        return json.loads(value) if value else None

    async def set(self, namespace: str, parts: list[str], value: dict[str, Any]) -> None:
        if self.redis:
            await self.redis.set(await self._key(namespace, parts), json.dumps(value), ex=self.ttl)

    async def invalidate(self, namespace: str) -> None:
        if self.redis:
            await self.redis.incr(f"cache-generation:{self._digest(namespace)}")

    async def _key(self, namespace: str, parts: list[str]) -> str:
        assert self.redis
        digest = self._digest(namespace)
        generation = await self.redis.get(f"cache-generation:{digest}") or "0"
        return f"cache:{digest}:{generation}:{self._digest('|'.join(parts))}"

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()[:24]
