import base64

import httpx
import pytest
from vision_agent.app import VisionHandler, agent_card, create_app

from platform_runtime.settings import settings


@pytest.mark.asyncio
async def test_vision_handler_calls_multimodal_model(monkeypatch):
    monkeypatch.setattr(settings, "vision_base_url", "http://vision-model")
    monkeypatch.setattr(settings, "vision_model", "qwen3-vl")

    def response(request: httpx.Request) -> httpx.Response:
        body = __import__("json").loads(request.content)
        content = body["messages"][0]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "A chart labeled wavelength"}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    handler = VisionHandler(client)
    result = await handler(
        {
            "jsonrpc": "2.0",
            "id": "vision-1",
            "method": "vision.describe",
            "params": {
                "image_base64": base64.b64encode(b"image").decode(),
                "media_type": "image/jpeg",
            },
        }
    )
    assert result["result"]["caption"] == "A chart labeled wavelength"
    assert (await handler({"jsonrpc": "2.0", "id": "bad", "method": "other"}))["error"]
    assert (await handler({"jsonrpc": "2.0", "id": "missing", "method": "vision.describe"}))[
        "error"
    ]
    await client.aclose()


def test_vision_card_and_routes():
    assert agent_card().skills[0].id == "vision.describe"
    paths = {route.path for route in create_app(register=False).routes}
    assert {"/.well-known/agent.json", "/health", "/metrics"} <= paths
