import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from platform_runtime.contracts import (
    AgentCard,
    JsonRpcError,
    JsonRpcResponse,
    Skill,
    normalize_task_request,
)
from platform_runtime.kafka import KafkaWorker
from platform_runtime.settings import settings

logger = logging.getLogger(__name__)


def agent_card() -> AgentCard:
    return AgentCard(
        name="vision",
        description="Image captioning, OCR, chart, and diagram understanding with Qwen3-VL",
        endpoint=settings.vision_agent_endpoint,
        task_topic="tasks.vision",
        result_topic="results.vision",
        skills=[
            Skill(
                id="vision.describe",
                description="Describe an image and transcribe visible labels, charts, and tables",
            )
        ],
    )


class VisionHandler:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(timeout=settings.vision_timeout_seconds)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_task_request(payload)
        if request.method != "vision.describe":
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32601, message="Method not found"),
            ).model_dump(mode="json", exclude_none=True)
        image = request.params.get("image_base64")
        media_type = request.params.get("media_type", "image/jpeg")
        if not isinstance(image, str) or not image:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32602, message="image_base64 is required"),
            ).model_dump(mode="json", exclude_none=True)
        if not settings.vision_base_url or not settings.vision_model:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32003, message="Vision model is not configured"),
            ).model_dump(mode="json", exclude_none=True)
        headers = {"Content-Type": "application/json"}
        if settings.vision_api_key:
            headers["Authorization"] = f"Bearer {settings.vision_api_key}"
        response = await self.client.post(
            f"{settings.vision_base_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json={
                "model": settings.vision_model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": request.params.get(
                                    "prompt",
                                    "Describe the image and transcribe visible labels and text.",
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{media_type};base64,{image}"},
                            },
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": 768,
            },
        )
        response.raise_for_status()
        caption = response.json()["choices"][0]["message"].get("content")
        if not caption:
            raise RuntimeError("Vision model returned no caption")
        return JsonRpcResponse(
            id=request.id, result={"caption": caption, "media_type": media_type}
        ).model_dump(mode="json", exclude_none=True)


async def register_forever(client: httpx.AsyncClient, card: AgentCard) -> None:
    while True:
        try:
            await client.post(
                f"{settings.registry_url.rstrip('/')}/registry/register",
                json=card.model_dump(mode="json"),
            )
        except httpx.HTTPError:
            logger.warning("Registry registration failed; retrying", exc_info=True)
        await asyncio.sleep(settings.registration_interval_seconds)


def create_app(handler: VisionHandler | None = None, register: bool = True) -> FastAPI:
    vision = handler or VisionHandler()
    card = agent_card()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not settings.kafka_bootstrap_servers:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required for the vision agent")
        registration_client = httpx.AsyncClient(timeout=5)
        tasks: list[asyncio.Task[Any]] = []
        if register:
            tasks.append(asyncio.create_task(register_forever(registration_client, card)))
        worker = KafkaWorker(
            settings.kafka_bootstrap_servers,
            card.task_topic,
            card.result_topic,
            "vision-agent",
            vision,
            settings.kafka_security_protocol,
        )
        tasks.append(asyncio.create_task(worker.run()))
        yield
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        await registration_client.aclose()
        await vision.close()

    app = FastAPI(title="Vision Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/.well-known/agent.json", response_model=AgentCard)
    async def discovery() -> AgentCard:
        return card

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "agent": card.name}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
