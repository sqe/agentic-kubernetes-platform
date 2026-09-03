import httpx
import jwt
import pytest
from analytics_agent.app import AnalyticsHandler, agent_card, create_app
from analytics_agent.cube import CubeClient

from platform_runtime.cache import Cache


class FakeCube:
    def __init__(self):
        self.queries = []

    async def query(self, query):
        self.queries.append(query)
        return {"data": [{"AgentMessages.skill": "weather.current", "AgentMessages.count": "3"}]}

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_usage_is_tenant_scoped_and_reproducible():
    cube = FakeCube()
    handler = AnalyticsHandler(cube, Cache(None, 60))
    response = await handler(
        {
            "jsonrpc": "2.0",
            "id": "usage-1",
            "method": "analytics.usage",
            "params": {"days": 7, "tenant": "user-1"},
        }
    )
    assert response["result"]["rows"][0]["AgentMessages.count"] == "3"
    assert response["result"]["query"] == cube.queries[0]
    assert cube.queries[0]["filters"][0]["values"] == ["user-1"]


@pytest.mark.asyncio
async def test_errors_adds_status_filter_and_unknown_method_fails():
    cube = FakeCube()
    handler = AnalyticsHandler(cube, Cache(None, 60))
    response = await handler(
        {"jsonrpc": "2.0", "id": "errors-1", "method": "analytics.errors", "params": {}}
    )
    assert cube.queries[0]["filters"][0]["values"] == ["error"]
    invalid = await handler(
        {"jsonrpc": "2.0", "id": "bad-1", "method": "analytics.other", "params": {}}
    )
    assert invalid["error"]["code"] == -32601
    assert response["result"]["window_days"] == 30


@pytest.mark.asyncio
async def test_cube_client_signs_short_lived_token():
    async def transport(request: httpx.Request):
        token = request.headers["Authorization"]
        claims = jwt.decode(token, "secret", algorithms=["HS256"])
        assert claims["sub"] == "analytics-agent"
        return httpx.Response(200, json={"data": [{"value": "1"}]})

    client = CubeClient(
        "http://cube", "secret", httpx.AsyncClient(transport=httpx.MockTransport(transport))
    )
    assert (await client.query({"measures": ["AgentMessages.count"]}))["data"]


def test_analytics_card_and_routes():
    assert {skill.id for skill in agent_card().skills} == {
        "analytics.usage",
        "analytics.errors",
    }
    paths = {route.path for route in create_app(FakeCube(), register=False).routes}
    assert "/.well-known/agent.json" in paths
    assert "/health" in paths
