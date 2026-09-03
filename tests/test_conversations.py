import hashlib
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from supervisor_service.app import create_app
from supervisor_service.conversations import ConversationStore

from platform_runtime.settings import settings


class FakeDispatcher:
    async def start(self):
        pass

    async def close(self):
        pass

    async def available_skills(self):
        return [{"skill": "weather.current", "description": "Current weather"}]

    async def route(self, request, task_id=None):
        return {"result": {"status": "accepted", "skill": request.skill or "weather.current"}}

    async def dispatch(self, request):
        return {"result": {"status": "accepted"}}


class FakeCollector:
    async def start(self):
        pass

    async def close(self):
        pass


class FakeStore:
    def __init__(self):
        self.thread = {"id": "thread-1", "title": "Weather", "messages": []}

    async def start(self):
        pass

    async def close(self):
        pass

    async def create(self, owner, title):
        self.owner = owner
        self.thread["title"] = title
        return self.thread

    async def list(self, owner):
        return [{**self.thread, "message_count": len(self.thread["messages"])}]

    async def get(self, owner, thread_id):
        if thread_id != self.thread["id"]:
            raise HTTPException(404, "Conversation not found")
        return self.thread

    async def add_pending(self, owner, thread_id, task_id, prompt):
        self.thread["messages"] += [
            {"role": "user", "content": prompt, "status": "complete"},
            {"id": task_id, "role": "assistant", "content": "Waiting", "status": "pending"},
        ]

    async def set_skill(self, task_id, skill):
        self.skill = skill

    async def fail(self, task_id, detail):
        self.failed = detail

    async def share(self, owner, thread_id):
        return "share-token"

    async def unshare(self, owner, thread_id):
        self.unshared = thread_id

    async def shared(self, token):
        return self.thread


def test_thread_api_and_read_only_share(monkeypatch):
    monkeypatch.setattr(settings, "auth_disabled", True)
    store = FakeStore()
    app = create_app(FakeDispatcher(), store, FakeCollector())
    with TestClient(app) as client:
        assert client.get("/v1/skills").json()[0]["skill"] == "weather.current"
        assert client.post("/v1/threads", json={"title": "Weather"}).status_code == 201
        response = client.post(
            "/v1/threads/thread-1/messages",
            json={"prompt": "Weather in Seattle", "skill": "weather.current"},
        )
        assert response.status_code == 202
        assert store.skill == "weather.current"
        assert client.get("/v1/threads/thread-1").json()["messages"][0]["role"] == "user"
        shared = client.post("/v1/threads/thread-1/share").json()
        assert shared["url"].endswith("/share/share-token")
        assert client.get("/v1/shared/share-token").status_code == 200
        assert client.delete("/v1/threads/thread-1/share").status_code == 204


@pytest.mark.asyncio
async def test_result_completion_and_share_tokens_are_hashed(monkeypatch):
    class Pool:
        def __init__(self):
            self.calls = []

        async def execute(self, query, *args):
            self.calls.append((query, args))

        async def fetchrow(self, query, *args):
            return {"id": "thread-1", "title": "Thread"}

        async def fetch(self, query, *args):
            return []

    store = ConversationStore(None)
    store.pool = Pool()
    await store.complete({"id": "task-1", "result": {"temperature": 20}})
    assert json.loads(store.pool.calls[-1][1][3])["result"]["temperature"] == 20

    monkeypatch.setattr(
        "supervisor_service.conversations.secrets.token_urlsafe", lambda _: "secret"
    )
    assert await store.share("owner", "thread-1") == "secret"
    assert store.pool.calls[-1][1][0] == hashlib.sha256(b"secret").hexdigest()
