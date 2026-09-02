import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from knowledge_graph_agent.auth import JwtVerifier

from platform_runtime.cache import Cache
from platform_runtime.settings import settings


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.closed = False

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ex):
        self.values[key] = value
        self.expiry = ex

    async def incr(self, key):
        self.values[key] = str(int(self.values.get(key, "0")) + 1)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_cache_generation_and_round_trip():
    cache = Cache(None, 30)
    cache.redis = FakeRedis()
    await cache.set("tenant", ["search", "JWST"], {"nodes": ["JWST"]})
    assert await cache.get("tenant", ["search", "JWST"]) == {"nodes": ["JWST"]}
    await cache.invalidate("tenant")
    assert await cache.get("tenant", ["search", "JWST"]) is None
    assert len(Cache._digest("tenant")) == 24
    await cache.close()
    assert cache.redis.closed


@pytest.mark.asyncio
async def test_jwt_verifier_configuration(monkeypatch):
    verifier = JwtVerifier()
    monkeypatch.setattr(settings, "auth_disabled", True)
    assert await verifier() == {"sub": "local-development"}
    monkeypatch.setattr(settings, "auth_disabled", False)
    with pytest.raises(HTTPException) as missing:
        await verifier()
    assert missing.value.status_code == 401
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="header.payload.signature"
    )
    with pytest.raises(HTTPException) as unconfigured:
        await verifier(credentials)
    assert unconfigured.value.status_code == 503
