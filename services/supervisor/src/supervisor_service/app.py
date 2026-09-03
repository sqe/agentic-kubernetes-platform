import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from aiokafka import AIOKafkaProducer
from fastapi import Depends, FastAPI, HTTPException, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, Response

from platform_runtime.auth import verify_jwt
from platform_runtime.contracts import JsonRpcRequest, PromptRequest, Registration
from platform_runtime.settings import settings

from .conversations import ConversationStore, ResultCollector
from .gateway import LlmGateway

Claims = Annotated[dict[str, Any], Depends(verify_jwt)]


class ThreadCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=200)


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
        self.client = httpx.AsyncClient(timeout=15)
        self.gateway = LlmGateway(gateway_url, gateway_model, gateway_api_key, self.client)
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

    async def available_skills(self) -> list[dict[str, str]]:
        response = await self.client.get(f"{self.registry_url}/registry/agents")
        response.raise_for_status()
        registrations = [Registration.model_validate(item) for item in response.json()]
        return [
            {"skill": skill.id, "description": skill.description}
            for registration in registrations
            for skill in registration.card.skills
        ]

    async def route(self, request: PromptRequest, task_id: str | None = None) -> dict[str, Any]:
        if request.skill:
            skill = request.skill
        else:
            available = await self.available_skills()
            if not available:
                raise HTTPException(status_code=404, detail="No agent skills are registered")
            self.gateway.client = self.client
            skill = await self.gateway.select(request.prompt, available)
        params = dict(request.params)
        params.setdefault("prompt", request.prompt)
        result = await self.dispatch(
            JsonRpcRequest(id=task_id or str(uuid4()), method=skill, params=params)
        )
        result["result"]["skill"] = skill
        return result


def create_app(
    dispatcher: Dispatcher | None = None,
    conversation_store: ConversationStore | None = None,
    result_collector: ResultCollector | None = None,
) -> FastAPI:
    service = dispatcher or Dispatcher(
        settings.registry_url,
        settings.kafka_bootstrap_servers or "localhost:9092",
        settings.llm_gateway_url,
        settings.llm_gateway_model,
        settings.llm_gateway_api_key,
    )
    store = conversation_store or ConversationStore(settings.postgres_url)
    collector = result_collector or ResultCollector(
        settings.kafka_bootstrap_servers or "localhost:9092", store
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.start()
        await service.start()
        await collector.start()
        yield
        await collector.close()
        await service.close()
        await store.close()

    app = FastAPI(title="Agent Supervisor", version="0.1.0", lifespan=lifespan)
    app.state.dispatcher = service
    app.state.conversations = store

    @app.get("/dashboard", include_in_schema=False)
    @app.get("/share/{token}", include_in_schema=False)
    async def dashboard(token: str | None = None) -> FileResponse:
        return FileResponse(Path(__file__).with_name("static") / "dashboard.html")

    @app.get("/v1/dashboard/auth")
    async def auth_config() -> dict[str, Any]:
        return {
            "enabled": not settings.auth_disabled,
            "client_id": settings.oidc_client_id,
            "authorization_endpoint": settings.oidc_authorization_endpoint,
            "token_endpoint": settings.oidc_token_endpoint,
            "logout_endpoint": settings.oidc_logout_endpoint,
            "registration_endpoint": settings.oidc_registration_endpoint,
            "scope": "openid profile email",
            "automatic_routing": bool(
                settings.llm_gateway_url and settings.llm_gateway_model
            ),
        }

    @app.post("/v1/tasks")
    async def dispatch(request: JsonRpcRequest) -> dict[str, Any]:
        return await service.dispatch(request)

    @app.post("/v1/route")
    async def route(request: PromptRequest) -> dict[str, Any]:
        return await service.route(request)

    @app.get("/v1/skills")
    async def available_skills() -> list[dict[str, str]]:
        return await service.available_skills()

    @app.post("/v1/threads", status_code=201)
    async def create_thread(request: ThreadCreate, claims: Claims) -> dict[str, Any]:
        return await store.create(str(claims["sub"]), request.title)

    @app.get("/v1/threads")
    async def list_threads(claims: Claims) -> list[dict[str, Any]]:
        return await store.list(str(claims["sub"]))

    @app.get("/v1/threads/{thread_id}")
    async def get_thread(thread_id: str, claims: Claims) -> dict[str, Any]:
        return await store.get(str(claims["sub"]), thread_id)

    @app.post("/v1/threads/{thread_id}/messages", status_code=202)
    async def prompt_thread(
        thread_id: str, prompt: PromptRequest, claims: Claims
    ) -> dict[str, Any]:
        task_id = str(uuid4())
        owner = str(claims["sub"])
        await store.add_pending(owner, thread_id, task_id, prompt.prompt)
        routed_prompt = prompt.model_copy(
            update={"params": {**prompt.params, "tenant": owner}}
        )
        try:
            result = await service.route(routed_prompt, task_id)
            await store.set_skill(task_id, result["result"]["skill"])
            return {"task_id": task_id, **result["result"]}
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "Unable to dispatch prompt"
            await store.fail(task_id, str(detail))
            raise

    @app.post("/v1/threads/{thread_id}/share", status_code=201)
    async def share_thread(thread_id: str, request: Request, claims: Claims) -> dict[str, str]:
        token = await store.share(str(claims["sub"]), thread_id)
        return {"url": f"{str(request.base_url).rstrip('/')}/share/{token}"}

    @app.delete("/v1/threads/{thread_id}/share", status_code=204)
    async def unshare_thread(thread_id: str, claims: Claims) -> Response:
        await store.unshare(str(claims["sub"]), thread_id)
        return Response(status_code=204)

    @app.get("/v1/shared/{token}")
    async def shared_thread(token: str) -> dict[str, Any]:
        return await store.shared(token)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
