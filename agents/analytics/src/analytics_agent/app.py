import asyncio
import logging
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

from .cube import CubeClient

logger = logging.getLogger(__name__)


def agent_card() -> AgentCard:
    return AgentCard(
        name="analytics",
        description="Governed BI over agent usage and conversation outcomes through Cube Core",
        endpoint=settings.agent_endpoint,
        task_topic="tasks.analytics",
        result_topic="results.analytics",
        skills=[
            Skill(
                id="analytics.usage",
                description="Agent task volume grouped by skill and status",
            ),
            Skill(id="analytics.errors", description="Failed agent task volume grouped by skill"),
        ],
    )


class AnalyticsHandler:
    def __init__(self, cube: CubeClient, cache: Cache) -> None:
        self.cube = cube
        self.cache = cache

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_task_request(payload)
        if request.method not in {"analytics.usage", "analytics.errors"}:
            return JsonRpcResponse(
                id=request.id, error=JsonRpcError(code=-32601, message="Method not found")
            ).model_dump(mode="json", exclude_none=True)
        days = min(max(int(request.params.get("days", 30)), 1), 365)
        tenant = request.params.get("tenant")
        cache_parts = [request.method, str(days), str(tenant or "all")]
        try:
            with observe_task("analytics") as observed:
                if cached := await self.cache.get("analytics", cache_parts):
                    observed["status"] = "success"
                    return JsonRpcResponse(id=request.id, result=cached).model_dump(
                        mode="json", exclude_none=True
                    )
                filters: list[dict[str, Any]] = []
                if tenant:
                    filters.append(
                        {"member": "AgentMessages.owner", "operator": "equals", "values": [tenant]}
                    )
                if request.method == "analytics.errors":
                    filters.append(
                        {
                            "member": "AgentMessages.status",
                            "operator": "equals",
                            "values": ["error"],
                        }
                    )
                query = {
                    "measures": ["AgentMessages.count"],
                    "dimensions": ["AgentMessages.skill", "AgentMessages.status"],
                    "timeDimensions": [
                        {
                            "dimension": "AgentMessages.createdAt",
                            "dateRange": f"Last {days} days",
                        }
                    ],
                    "filters": filters,
                    "order": {"AgentMessages.count": "desc"},
                    "limit": 100,
                }
                response = await self.cube.query(query)
                result = {
                    "window_days": days,
                    "tenant_scoped": bool(tenant),
                    "rows": response.get("data", []),
                    "last_refresh_time": response.get("lastRefreshTime"),
                    "query": query,
                }
                observed["status"] = "success"
                await self.cache.set("analytics", cache_parts, result)
            trace_execution("analytics", request.id, result, settings.mlflow_tracking_uri)
            return JsonRpcResponse(id=request.id, result=result).model_dump(
                mode="json", exclude_none=True
            )
        except (httpx.HTTPError, ValueError) as exc:
            return JsonRpcResponse(
                id=request.id, error=JsonRpcError(code=-32002, message=str(exc))
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


def create_app(cube: CubeClient | None = None, register: bool = True) -> FastAPI:
    client = cube or CubeClient(settings.cube_url, settings.cube_api_secret)
    cache = Cache(settings.redis_url, settings.cache_ttl_seconds)
    handler = AnalyticsHandler(client, cache)
    card = agent_card()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not settings.kafka_bootstrap_servers:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required for the analytics agent")
        registration_client = httpx.AsyncClient(timeout=5)
        tasks: list[asyncio.Task[Any]] = []
        if register:
            tasks.append(asyncio.create_task(register_forever(registration_client, card)))
        worker = KafkaWorker(
            settings.kafka_bootstrap_servers,
            card.task_topic,
            card.result_topic,
            "analytics-agent",
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

    app = FastAPI(title="Cube Analytics Agent", version="0.1.0", lifespan=lifespan)

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
