import asyncio
import hashlib
import io
import json
import secrets
from contextlib import suppress
from typing import Any
from uuid import uuid4

import asyncpg
from aiokafka import AIOKafkaConsumer
from fastapi import HTTPException

from platform_runtime.settings import settings


class ConversationStore:
    def __init__(self, postgres_url: str | None, object_store: Any | None = None) -> None:
        self.postgres_url = postgres_url
        self.pool: Any | None = None
        self.object_store = object_store
        self._owns_object_store = object_store is None
        if self._owns_object_store and settings.object_store_endpoint:
            from knowledge_graph_agent.object_store import ObjectStore

            self.object_store = ObjectStore()

    async def start(self) -> None:
        if not self.postgres_url:
            return
        self.pool = await asyncpg.create_pool(self.postgres_url, min_size=1, max_size=5)
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_threads (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS conversation_threads_owner_updated
              ON conversation_threads(owner, updated_at DESC);
            CREATE TABLE IF NOT EXISTS conversation_messages (
              id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES conversation_threads(id)
                ON DELETE CASCADE,
              role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
              content TEXT NOT NULL, status TEXT NOT NULL,
              skill TEXT, payload JSONB,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS conversation_messages_thread_created
              ON conversation_messages(thread_id, created_at);
            CREATE TABLE IF NOT EXISTS conversation_shares (
              token_hash TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE
                REFERENCES conversation_threads(id) ON DELETE CASCADE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
        if self._owns_object_store and self.object_store:
            await self.object_store.close()

    async def _persist_to_rustfs(self, owner: str, thread_id: str) -> None:
        """Persist the full conversation thread as JSON in RustFS for auditability."""
        if not self.object_store or not self.pool:
            return
        try:
            thread = await self.pool.fetchrow(
                "SELECT * FROM conversation_threads WHERE id=$1", thread_id
            )
            if not thread:
                return
            messages = await self.pool.fetch(
                "SELECT id, role, content, status, skill, payload, created_at "
                "FROM conversation_messages WHERE thread_id=$1 ORDER BY created_at, role DESC, id",
                thread_id,
            )
            conversation = {
                "thread": dict(thread),
                "messages": [dict(row) for row in messages],
            }
            payload = json.dumps(conversation, indent=2, ensure_ascii=False, default=str)
            await self.object_store.upload(
                owner,
                thread_id,
                "conversation.json",
                io.BytesIO(payload.encode()),
                "application/json",
            )
        except Exception:
            # RustFS persistence is best-effort; PostgreSQL remains the source of truth.
            pass

    def require_pool(self) -> Any:
        if not self.pool:
            raise HTTPException(
                status_code=503, detail="Conversation persistence is not configured"
            )
        return self.pool

    async def create(self, owner: str, title: str) -> dict[str, Any]:
        row = await self.require_pool().fetchrow(
            "INSERT INTO conversation_threads(id, owner, title) VALUES($1, $2, $3) RETURNING *",
            str(uuid4()),
            owner,
            title.strip() or "New conversation",
        )
        await self._persist_to_rustfs(owner, row["id"])
        return dict(row)

    async def list(self, owner: str) -> list[dict[str, Any]]:
        rows = await self.require_pool().fetch(
            "SELECT t.*, count(m.id)::int AS message_count FROM conversation_threads t "
            "LEFT JOIN conversation_messages m ON m.thread_id=t.id WHERE t.owner=$1 "
            "GROUP BY t.id ORDER BY t.updated_at DESC",
            owner,
        )
        return [dict(row) for row in rows]

    async def get(self, owner: str, thread_id: str) -> dict[str, Any]:
        pool = self.require_pool()
        thread = await pool.fetchrow(
            "SELECT * FROM conversation_threads WHERE id=$1 AND owner=$2", thread_id, owner
        )
        if not thread:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = await pool.fetch(
            "SELECT id, role, content, status, skill, payload, created_at "
            "FROM conversation_messages WHERE thread_id=$1 ORDER BY created_at, role DESC, id",
            thread_id,
        )
        return {**dict(thread), "messages": [dict(row) for row in messages]}

    async def add_pending(self, owner: str, thread_id: str, task_id: str, prompt: str) -> None:
        pool = self.require_pool()
        async with pool.acquire() as connection, connection.transaction():
            exists = await connection.fetchval(
                "SELECT true FROM conversation_threads WHERE id=$1 AND owner=$2 FOR UPDATE",
                thread_id,
                owner,
            )
            if not exists:
                raise HTTPException(status_code=404, detail="Conversation not found")
            await connection.executemany(
                "INSERT INTO conversation_messages(id, thread_id, role, content, status) "
                "VALUES($1, $2, $3, $4, $5)",
                [
                    (str(uuid4()), thread_id, "user", prompt, "complete"),
                    (task_id, thread_id, "assistant", "Waiting for an agent…", "pending"),
                ],
            )
            await connection.execute(
                "UPDATE conversation_threads SET updated_at=now() WHERE id=$1", thread_id
            )
        await self._persist_to_rustfs(owner, thread_id)

    async def set_skill(self, task_id: str, skill: str) -> None:
        await self.require_pool().execute(
            "UPDATE conversation_messages SET skill=$2 WHERE id=$1", task_id, skill
        )

    async def fail(self, task_id: str, detail: str) -> None:
        await self.require_pool().execute(
            "UPDATE conversation_messages SET content=$2, status='error' WHERE id=$1",
            task_id,
            detail,
        )

    async def complete(self, payload: dict[str, Any]) -> None:
        task_id = payload.get("id")
        if not task_id or not self.pool:
            return
        error = payload.get("error")
        value = error or payload.get("result") or {}
        content = (
            error.get("message", "Agent task failed")
            if isinstance(error, dict)
            else json.dumps(value, indent=2, ensure_ascii=False)
        )
        row = await self.pool.fetchrow(
            "UPDATE conversation_messages SET content=$2, status=$3, payload=$4::jsonb "
            "WHERE id=$1 AND status='pending' RETURNING thread_id",
            str(task_id),
            content,
            "error" if error else "complete",
            json.dumps(payload),
        )
        if row:
            owner = await self.pool.fetchval(
                "SELECT owner FROM conversation_threads WHERE id=$1", row["thread_id"]
            )
            if owner:
                await self._persist_to_rustfs(owner, row["thread_id"])

    async def share(self, owner: str, thread_id: str) -> str:
        await self.get(owner, thread_id)
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        await self.require_pool().execute(
            "INSERT INTO conversation_shares(token_hash, thread_id) VALUES($1, $2) "
            "ON CONFLICT(thread_id) DO UPDATE SET token_hash=EXCLUDED.token_hash, created_at=now()",
            digest,
            thread_id,
        )
        return token

    async def unshare(self, owner: str, thread_id: str) -> None:
        await self.get(owner, thread_id)
        await self.require_pool().execute(
            "DELETE FROM conversation_shares WHERE thread_id=$1", thread_id
        )

    async def shared(self, token: str) -> dict[str, Any]:
        digest = hashlib.sha256(token.encode()).hexdigest()
        pool = self.require_pool()
        thread = await pool.fetchrow(
            "SELECT t.id, t.title, t.created_at, t.updated_at FROM conversation_threads t "
            "JOIN conversation_shares s ON s.thread_id=t.id WHERE s.token_hash=$1",
            digest,
        )
        if not thread:
            raise HTTPException(status_code=404, detail="Shared conversation not found")
        messages = await pool.fetch(
            "SELECT role, content, status, skill, created_at FROM conversation_messages "
            "WHERE thread_id=$1 ORDER BY created_at, role DESC, id",
            thread["id"],
        )
        return {**dict(thread), "messages": [dict(row) for row in messages]}


class ResultCollector:
    def __init__(self, kafka_servers: str, store: ConversationStore) -> None:
        self.kafka_servers = kafka_servers
        self.store = store
        self.task: asyncio.Task[Any] | None = None
        self.consumer: AIOKafkaConsumer | None = None

    async def start(self) -> None:
        if not self.store.pool:
            return
        self.consumer = AIOKafkaConsumer(
            bootstrap_servers=self.kafka_servers,
            group_id="supervisor-conversations",
            enable_auto_commit=True,
            value_deserializer=lambda value: json.loads(value.decode()),
        )
        self.consumer.subscribe(pattern=r"^results\..+$")
        await self.consumer.start()
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        assert self.consumer
        async for message in self.consumer:
            await self.store.complete(message.value)

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self.consumer:
            await self.consumer.stop()
