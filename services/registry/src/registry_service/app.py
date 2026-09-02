import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from platform_runtime.contracts import AgentCard, Registration
from platform_runtime.settings import settings


class AgentRegistry:
    def __init__(self, ttl_seconds: int, postgres_url: str | None = None) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self.postgres_url = postgres_url
        self.pool: Any | None = None
        self.registrations: dict[str, Registration] = {}
        self.lock = asyncio.Lock()

    async def start(self) -> None:
        if not self.postgres_url:
            return
        self.pool = await asyncpg.create_pool(self.postgres_url, min_size=1, max_size=5)
        await self.pool.execute(
            "CREATE TABLE IF NOT EXISTS agent_registrations ("
            "name TEXT PRIMARY KEY, card JSONB NOT NULL, observed_at TIMESTAMPTZ NOT NULL)"
        )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def register(self, card: AgentCard) -> Registration:
        registration = Registration(card=card)
        if self.pool:
            await self.pool.execute(
                "INSERT INTO agent_registrations(name, card, observed_at) "
                "VALUES($1, $2::jsonb, $3) ON CONFLICT(name) DO UPDATE SET "
                "card=EXCLUDED.card, observed_at=EXCLUDED.observed_at",
                card.name,
                json.dumps(card.model_dump(mode="json")),
                registration.observed_at,
            )
            return registration
        async with self.lock:
            self.registrations[card.name] = registration
        return registration

    async def active(self) -> list[Registration]:
        cutoff = datetime.now(UTC) - self.ttl
        if self.pool:
            await self.pool.execute(
                "DELETE FROM agent_registrations WHERE observed_at < $1", cutoff
            )
            rows = await self.pool.fetch(
                "SELECT card, observed_at FROM agent_registrations ORDER BY name"
            )
            return [
                Registration(
                    card=AgentCard.model_validate(
                        json.loads(row["card"]) if isinstance(row["card"], str) else row["card"]
                    ),
                    observed_at=row["observed_at"],
                )
                for row in rows
            ]
        async with self.lock:
            stale = [name for name, item in self.registrations.items() if item.observed_at < cutoff]
            for name in stale:
                del self.registrations[name]
            return list(self.registrations.values())

    async def find_skill(self, skill: str) -> Registration | None:
        return next(
            (
                item
                for item in await self.active()
                if any(candidate.id == skill for candidate in item.card.skills)
            ),
            None,
        )


def create_app(registry: AgentRegistry | None = None) -> FastAPI:
    store = registry or AgentRegistry(settings.registry_ttl_seconds, settings.postgres_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.start()
        yield
        await store.close()

    app = FastAPI(title="Agent Registry", version="0.1.0", lifespan=lifespan)
    app.state.registry = store

    @app.post("/registry/register", response_model=Registration)
    async def register(card: AgentCard) -> Registration:
        return await store.register(card)

    @app.get("/registry/agents", response_model=list[Registration])
    async def agents() -> list[Registration]:
        return await store.active()

    @app.get("/registry/skills/{skill}", response_model=Registration)
    async def skill(skill: str) -> Registration:
        if registration := await store.find_skill(skill):
            return registration
        raise HTTPException(status_code=404, detail=f"No active agent provides {skill}")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
