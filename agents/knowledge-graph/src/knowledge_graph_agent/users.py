from datetime import datetime
from typing import Any

import asyncpg
from pydantic import BaseModel


class UserProfile(BaseModel):
    issuer: str
    subject: str
    email: str | None = None
    display_name: str | None = None
    created_at: datetime
    last_seen_at: datetime


class UserStore:
    def __init__(self, postgres_url: str | None) -> None:
        self.postgres_url = postgres_url
        self.pool: Any | None = None

    async def start(self) -> None:
        if not self.postgres_url:
            return
        self.pool = await asyncpg.create_pool(self.postgres_url, min_size=1, max_size=5)
        await self.pool.execute(
            "CREATE TABLE IF NOT EXISTS platform_users ("
            "issuer TEXT NOT NULL, subject TEXT NOT NULL, email TEXT, display_name TEXT, "
            "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "PRIMARY KEY (issuer, subject))"
        )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def sync(self, claims: dict[str, Any], default_issuer: str | None) -> UserProfile:
        if not self.pool:
            raise RuntimeError("PostgreSQL user profiles are not configured")
        issuer = str(claims.get("iss") or default_issuer or "local-development")
        subject = str(claims["sub"])
        email = claims.get("email")
        display_name = claims.get("name") or claims.get("preferred_username") or email
        row = await self.pool.fetchrow(
            "INSERT INTO platform_users(issuer, subject, email, display_name) "
            "VALUES($1, $2, $3, $4) ON CONFLICT(issuer, subject) DO UPDATE SET "
            "email=EXCLUDED.email, display_name=COALESCE(platform_users.display_name, "
            "EXCLUDED.display_name), last_seen_at=NOW() RETURNING issuer, subject, email, "
            "display_name, created_at, last_seen_at",
            issuer,
            subject,
            str(email) if email else None,
            str(display_name) if display_name else None,
        )
        return UserProfile.model_validate(dict(row))
