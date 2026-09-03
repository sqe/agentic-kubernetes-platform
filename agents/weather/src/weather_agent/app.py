import asyncio
import logging
import re
from contextlib import asynccontextmanager, suppress
from typing import Any

import httpx
from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from platform_runtime.cache import Cache
from platform_runtime.contracts import (
    AgentCard,
    JsonRpcError,
    JsonRpcResponse,
    Skill,
    normalize_task_request,
)
from platform_runtime.kafka import KafkaWorker
from platform_runtime.observability import observe_task, trace_execution
from platform_runtime.settings import settings

from .domain import OpenMeteoClient, extract_location

logger = logging.getLogger(__name__)


def agent_card() -> AgentCard:
    return AgentCard(
        name="weather",
        description="Current conditions and forecasts from Open-Meteo",
        endpoint=settings.agent_endpoint,
        task_topic="tasks.weather",
        result_topic="results.weather",
        skills=[
            Skill(id="weather.current", description="Current weather for a location"),
            Skill(id="weather.forecast", description="One-to-sixteen-day location forecast"),
        ],
    )


class WeatherHandler:
    def __init__(self, weather: OpenMeteoClient, cache: Cache) -> None:
        self.weather = weather
        self.cache = cache

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_task_request(payload)
        params = request.params
        location = params.get("location") or extract_location(params.get("prompt", ""))
        if not location:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32602, message="params.location is required"),
            ).model_dump(mode="json", exclude_none=True)
        try:
            with observe_task("weather") as observed:
                prompt = str(params.get("prompt", ""))
                combined = "forecast" in prompt.casefold() and bool(
                    re.search(r"(?i)\b(?:weather|current|now)\b", prompt)
                )
                cache_parts = [
                    request.method,
                    "combined" if combined else "single",
                    str(location),
                    str(params.get("days", 7)),
                ]
                if cached := await self.cache.get("weather", cache_parts):
                    observed["status"] = "success"
                    return JsonRpcResponse(id=request.id, result=cached).model_dump(
                        mode="json", exclude_none=True
                    )
                if combined and request.method in {"weather.current", "weather.forecast"}:
                    current, forecast = await asyncio.gather(
                        self.weather.current(location),
                        self.weather.forecast(location, int(params.get("days", 7))),
                    )
                    result = {"current": current, "forecast": forecast}
                elif request.method == "weather.current":
                    result = await self.weather.current(location)
                elif request.method == "weather.forecast":
                    result = await self.weather.forecast(location, int(params.get("days", 7)))
                else:
                    return JsonRpcResponse(
                        id=request.id,
                        error=JsonRpcError(code=-32601, message="Method not found"),
                    ).model_dump(mode="json", exclude_none=True)
                observed["status"] = "success"
                await self.cache.set("weather", cache_parts, result)
            trace_execution("weather", request.id, result, settings.mlflow_tracking_uri)
            return JsonRpcResponse(id=request.id, result=result).model_dump(
                mode="json", exclude_none=True
            )
        except (httpx.HTTPError, ValueError) as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(code=-32001, message=str(exc)),
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


def create_app(weather: OpenMeteoClient | None = None, register: bool = True) -> FastAPI:
    client = weather or OpenMeteoClient()
    cache = Cache(settings.redis_url, settings.cache_ttl_seconds)
    handler = WeatherHandler(client, cache)
    card = agent_card()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not settings.kafka_bootstrap_servers:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required for the weather agent")
        tasks: list[asyncio.Task[Any]] = []
        registration_client = httpx.AsyncClient(timeout=5)
        if register:
            tasks.append(asyncio.create_task(register_forever(registration_client, card)))
        worker = KafkaWorker(
            settings.kafka_bootstrap_servers,
            card.task_topic,
            card.result_topic,
            "weather-agent",
            handler,
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
        await cache.close()
        await client.close()

    app = FastAPI(title="Weather Agent", version="0.1.0", lifespan=lifespan)

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
