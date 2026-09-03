from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from registry_service.app import AgentRegistry
from registry_service.app import create_app as create_registry
from supervisor_service.app import Dispatcher
from supervisor_service.app import create_app as create_supervisor

from platform_runtime.contracts import AgentCard, JsonRpcRequest, PromptRequest, Skill


def card():
    return AgentCard(
        name="test",
        description="test",
        endpoint="http://test:8000",
        task_topic="tasks.test",
        result_topic="results.test",
        skills=[Skill(id="test.run", description="run")],
    )


def test_registry_routes():
    with TestClient(create_registry(AgentRegistry(90))) as client:
        assert (
            client.post("/registry/register", json=card().model_dump(mode="json")).status_code
            == 200
        )
        assert client.get("/registry/skills/test.run").json()["card"]["name"] == "test"
        assert client.get("/registry/skills/missing").status_code == 404
        assert len(client.get("/registry/agents").json()) == 1
        assert client.get("/health").status_code == 200
        assert "python_gc" in client.get("/metrics").text


@pytest.mark.asyncio
async def test_registry_expires_stale():
    registry = AgentRegistry(1)
    item = await registry.register(card())
    item.observed_at = datetime.now(UTC) - timedelta(seconds=2)
    assert await registry.active() == []


@pytest.mark.asyncio
async def test_registry_persists_cards_in_postgresql(monkeypatch):
    class FakePool:
        closed = False

        async def execute(self, query, *args):
            self.last_execute = (query, args)

        async def fetch(self, query):
            return [{"card": card().model_dump(mode="json"), "observed_at": datetime.now(UTC)}]

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("registry_service.app.asyncpg.create_pool", create_pool)
    registry = AgentRegistry(90, "postgresql://database")
    await registry.start()
    await registry.register(card())
    assert (await registry.active())[0].card.name == "test"
    await registry.close()
    assert pool.closed


class FakeProducer:
    started = stopped = False
    sent = None

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def send_and_wait(self, topic, value):
        self.sent = (topic, value)


@pytest.mark.asyncio
async def test_dispatcher_routes_only_to_kafka():
    registration = {
        "card": card().model_dump(mode="json"),
        "observed_at": datetime.now(UTC).isoformat(),
    }
    dispatcher = Dispatcher("http://registry", "kafka:9092")
    dispatcher.producer = FakeProducer()
    dispatcher.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=registration))
    )
    result = await dispatcher.dispatch(JsonRpcRequest(id="1", method="test.run"))
    assert result["result"]["status"] == "accepted"
    assert dispatcher.producer.sent[0] == "tasks.test"
    await dispatcher.start()
    await dispatcher.close()
    assert dispatcher.producer.started and dispatcher.producer.stopped


@pytest.mark.asyncio
async def test_supervisor_selects_only_registered_skill_through_byom_gateway():
    registration = {
        "card": card().model_dump(mode="json"),
        "observed_at": datetime.now(UTC).isoformat(),
    }

    def route(request):
        if request.url.path == "/registry/agents":
            return httpx.Response(200, json=[registration])
        if request.url.path == "/v1/chat/completions":
            assert request.headers["authorization"] == "Bearer gateway-key"
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"skill":"test.run"}'}}]},
            )
        return httpx.Response(200, json=registration)

    dispatcher = Dispatcher("http://registry", "kafka:9092", "http://byom", "router", "gateway-key")
    dispatcher.producer = FakeProducer()
    dispatcher.client = httpx.AsyncClient(transport=httpx.MockTransport(route))
    result = await dispatcher.route(PromptRequest(prompt="run the test"))
    assert result["result"]["status"] == "accepted"
    assert dispatcher.producer.sent[1]["method"] == "test.run"
    assert dispatcher.producer.sent[1]["params"]["prompt"] == "run the test"
    await dispatcher.client.aclose()


class FakeDispatcher:
    async def start(self):
        pass

    async def close(self):
        pass

    async def dispatch(self, request):
        return {"id": request.id, "result": {"status": "accepted"}}

    async def route(self, request, task_id=None):
        return {"result": {"status": "accepted", "prompt": request.prompt, "skill": "test.run"}}


def test_supervisor_api():
    with TestClient(create_supervisor(FakeDispatcher())) as client:
        assert (
            client.post("/v1/tasks", json={"method": "test.run"}).json()["result"]["status"]
            == "accepted"
        )
        assert client.post("/v1/route", json={"prompt": "run"}).status_code == 200
        assert client.get("/dashboard").status_code == 200
        assert client.get("/health").status_code == 200
