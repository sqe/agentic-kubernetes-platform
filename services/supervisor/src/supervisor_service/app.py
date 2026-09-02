import json
from contextlib import asynccontextmanager
from typing import Any

import httpx
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.responses import Response

from platform_runtime.contracts import JsonRpcRequest, PromptRequest, Registration
from platform_runtime.settings import settings


class RouteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill: str


class Dispatcher:
    def __init__(
        self,
        registry_url: str,
        kafka_servers: str,
        gateway_url: str | None = None,
        gateway_model: str | None = None,
        gateway_api_key: str | None = None,
    ) -> None:
        self.registry_url = registry_url.rstrip("/")
        self.kafka_servers = kafka_servers
        self.gateway_url = gateway_url.rstrip("/") if gateway_url else None
        self.gateway_model = gateway_model
        self.gateway_api_key = gateway_api_key
        self.client = httpx.AsyncClient(timeout=15)
        self.producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        if self.producer is None:
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.kafka_servers,
                acks="all",
                enable_idempotence=True,
                value_serializer=lambda value: json.dumps(value).encode(),
            )
        await self.producer.start()

    async def close(self) -> None:
        if self.producer:
            await self.producer.stop()
        await self.client.aclose()

    async def dispatch(self, request: JsonRpcRequest) -> dict[str, Any]:
        response = await self.client.get(f"{self.registry_url}/registry/skills/{request.method}")
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail=f"No agent provides {request.method}")
        response.raise_for_status()
        registration = Registration.model_validate(response.json())
        if not self.producer:
            raise RuntimeError("Dispatcher has not been started")
        await self.producer.send_and_wait(
            registration.card.task_topic,
            request.model_dump(mode="json"),
        )
        return {"jsonrpc": "2.0", "id": request.id, "result": {"status": "accepted"}}

    async def route(self, request: PromptRequest) -> dict[str, Any]:
        if request.skill:
            skill = request.skill
        else:
            if not self.gateway_url or not self.gateway_model:
                raise HTTPException(status_code=503, detail="LLM gateway routing is not configured")
            response = await self.client.get(f"{self.registry_url}/registry/agents")
            response.raise_for_status()
            registrations = [Registration.model_validate(item) for item in response.json()]
            available = [
                {"skill": skill.id, "description": skill.description}
                for registration in registrations
                for skill in registration.card.skills
            ]
            if not available:
                raise HTTPException(status_code=404, detail="No agent skills are registered")
            headers = (
                {"Authorization": f"Bearer {self.gateway_api_key}"} if self.gateway_api_key else {}
            )
            response = await self.client.post(
                f"{self.gateway_url}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.gateway_model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Select exactly one available skill. Return only JSON "
                                'with the shape {"skill":"skill.id"}. Do not invent a skill.'
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"prompt": request.prompt, "available": available}
                            ),
                        },
                    ],
                },
            )
            response.raise_for_status()
            try:
                selection = RouteSelection.model_validate_json(
                    response.json()["choices"][0]["message"]["content"]
                )
            except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
                raise HTTPException(
                    status_code=502, detail="Gateway returned an invalid route"
                ) from exc
            if selection.skill not in {item["skill"] for item in available}:
                raise HTTPException(status_code=502, detail="Gateway selected an unavailable skill")
            skill = selection.skill
        params = dict(request.params)
        params.setdefault("prompt", request.prompt)
        return await self.dispatch(JsonRpcRequest(method=skill, params=params))


def create_app(dispatcher: Dispatcher | None = None) -> FastAPI:
    service = dispatcher or Dispatcher(
        settings.registry_url,
        settings.kafka_bootstrap_servers or "localhost:9092",
        settings.llm_gateway_url,
        settings.llm_gateway_model,
        settings.llm_gateway_api_key,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        yield
        await service.close()

    app = FastAPI(title="Agent Supervisor", version="0.1.0", lifespan=lifespan)
    app.state.dispatcher = service

    @app.post("/v1/tasks")
    async def dispatch(request: JsonRpcRequest) -> dict[str, Any]:
        return await service.dispatch(request)

    @app.post("/v1/route")
    async def route(request: PromptRequest) -> dict[str, Any]:
        return await service.route(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
