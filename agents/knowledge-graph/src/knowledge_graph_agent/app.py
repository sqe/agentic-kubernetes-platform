import asyncio
import json
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from aiokafka import AIOKafkaProducer
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from platform_runtime.cache import Cache
from platform_runtime.contracts import AgentCard, JsonRpcRequest, Skill
from platform_runtime.settings import settings

from .auth import verify_jwt
from .graph import GraphStore
from .models import DocumentIngest, McpCall
from .object_store import ObjectStore
from .ontology import ONTOLOGIES, Ontology, get_ontology
from .users import UserProfile, UserStore
from .vector import VectorStore

Claims = Annotated[dict[str, Any], Depends(verify_jwt)]
logger = logging.getLogger(__name__)


def require_ontology(ontology_id: str) -> Ontology:
    try:
        return get_ontology(ontology_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def agent_card() -> AgentCard:
    return AgentCard(
        name="knowledge-graph",
        description="Asynchronous document knowledge graph extraction and traversal",
        endpoint=settings.knowledge_agent_endpoint,
        task_topic="tasks.knowledge",
        result_topic="results.knowledge",
        skills=[
            Skill(id="knowledge.ingest", description="Extract a graph from a stored document"),
            Skill(id="graph.search", description="Search graph entities"),
            Skill(
                id="graph.visualize", description="Browse a bounded graph for 2D or 3D rendering"
            ),
            Skill(id="graph.neighbors", description="Traverse neighboring entities"),
            Skill(id="graph.path", description="Find the shortest path between entities"),
            Skill(id="graph.ontology", description="Retrieve a versioned graph ontology"),
            Skill(id="vector.search", description="Semantic search over document chunks"),
        ],
    )


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


def create_app(
    graph: GraphStore | None = None,
    objects: ObjectStore | None = None,
    register: bool = True,
    vectors: VectorStore | None = None,
    users: UserStore | None = None,
) -> FastAPI:
    store = graph or GraphStore(
        settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password
    )
    object_store = objects or ObjectStore()
    vector_store = vectors or VectorStore.from_settings()
    user_store = users or UserStore(settings.postgres_url)
    cache = Cache(settings.redis_url, settings.cache_ttl_seconds)
    producer: AIOKafkaProducer | None = None
    card = agent_card()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal producer
        await store.initialize()
        await user_store.start()
        registration_client = httpx.AsyncClient(timeout=5)
        registration_task: asyncio.Task[Any] | None = None
        if register:
            registration_task = asyncio.create_task(register_forever(registration_client, card))
        if settings.kafka_bootstrap_servers:
            producer = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                acks="all",
                enable_idempotence=True,
                value_serializer=lambda value: json.dumps(value).encode(),
            )
            await producer.start()
        yield
        if producer:
            await producer.stop()
        if registration_task:
            registration_task.cancel()
            with suppress(asyncio.CancelledError):
                await registration_task
        await registration_client.aclose()
        await cache.close()
        await vector_store.close()
        await user_store.close()
        await store.close()

    app = FastAPI(title="Knowledge Graph API", version="0.1.0", lifespan=lifespan)
    app.state.graph = store

    @app.get("/.well-known/agent.json", response_model=AgentCard)
    async def discovery() -> AgentCard:
        return card

    @app.get("/auth/config")
    async def auth_config() -> dict[str, Any]:
        return {
            "enabled": not settings.auth_disabled,
            "client_id": settings.oidc_client_id,
            "authorization_endpoint": settings.oidc_authorization_endpoint,
            "token_endpoint": settings.oidc_token_endpoint,
            "logout_endpoint": settings.oidc_logout_endpoint,
            "registration_endpoint": settings.oidc_registration_endpoint,
            "scope": "openid profile email",
        }

    @app.get("/v1/users/me", response_model=UserProfile)
    async def current_user(claims: Claims) -> UserProfile:
        try:
            return await user_store.sync(claims, settings.jwt_issuer)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    async def queue(document: DocumentIngest, tenant: str) -> dict[str, str]:
        require_ontology(document.ontology)
        if not producer:
            raise HTTPException(
                status_code=503, detail="Kafka is required for background ingestion"
            )
        request = JsonRpcRequest(
            id=str(uuid4()),
            method="knowledge.ingest",
            params={"tenant": tenant, "document": document.model_dump(mode="json")},
        )
        await producer.send_and_wait("tasks.knowledge", request.model_dump(mode="json"))
        return {"task_id": request.id, "status": "accepted"}

    @app.post("/v1/knowledge/documents", status_code=202)
    async def ingest(document: DocumentIngest, claims: Claims) -> dict[str, str]:
        return await queue(document, claims["sub"])

    @app.post("/v1/knowledge/documents/upload", status_code=202)
    async def upload(
        claims: Claims,
        title: Annotated[str, Form(min_length=1, max_length=500)],
        file: Annotated[UploadFile, File()],
        ontology: Annotated[str, Form()] = "core",
    ) -> dict[str, str]:
        if file.content_type not in {"application/pdf", "text/plain", "application/json"}:
            raise HTTPException(status_code=415, detail="Only PDF, text, and JSON are supported")
        selected_ontology = require_ontology(ontology)
        document_id = str(uuid4())
        uri = await object_store.upload(
            claims["sub"],
            document_id,
            file.filename or "document",
            file.file,
            file.content_type,
        )
        return await queue(
            DocumentIngest(
                document_id=document_id,
                title=title,
                source_uri=uri,
                ontology=selected_ontology.id,
            ),
            claims["sub"],
        )

    @app.get("/v1/knowledge/ontologies")
    async def ontologies(_: Claims) -> dict[str, Any]:
        return {"ontologies": [item.model_dump(mode="json") for item in ONTOLOGIES.values()]}

    @app.get("/v1/knowledge/ontologies/{ontology_id}")
    async def ontology(ontology_id: str, _: Claims) -> dict[str, Any]:
        try:
            return get_ontology(ontology_id).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/v1/knowledge/search")
    async def search(
        q: str, claims: Claims, limit: int = 50, ontology: str = "core"
    ) -> dict[str, Any]:
        require_ontology(ontology)
        namespace = f"knowledge:{claims['sub']}"
        parts = ["search", ontology, q, str(limit)]
        if cached := await cache.get(namespace, parts):
            return cached
        result = await store.search(claims["sub"], q, limit, ontology)
        await cache.set(namespace, parts, result)
        return result

    @app.get("/v1/knowledge/graph")
    async def visualize(
        claims: Claims,
        ontology: str = "core",
        entity_type: str | None = None,
        center: str | None = None,
        depth: int = 2,
        limit: int = 200,
    ) -> dict[str, Any]:
        selected = require_ontology(ontology)
        valid_types = {item.id for item in selected.entity_types}
        if entity_type and entity_type not in valid_types:
            raise HTTPException(status_code=422, detail=f"Unknown entity type: {entity_type}")
        if center:
            result = await store.neighbors(claims["sub"], center, depth, ontology)
            result["stats"] = {
                "node_count": len(result.get("nodes", [])),
                "edge_count": len(result.get("edges", [])),
            }
            return result
        return await store.browse(claims["sub"], limit, ontology, entity_type)

    @app.get("/v1/knowledge/semantic-search")
    async def semantic_search(
        q: str, claims: Claims, limit: int = 10, ontology: str | None = None
    ) -> dict[str, Any]:
        if ontology:
            require_ontology(ontology)
        try:
            points = await vector_store.search(claims["sub"], q, limit, ontology)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"results": points}

    @app.get("/v1/knowledge/neighbors/{name}")
    async def neighbors(
        name: str, claims: Claims, depth: int = 1, ontology: str = "core"
    ) -> dict[str, Any]:
        require_ontology(ontology)
        namespace = f"knowledge:{claims['sub']}"
        parts = ["neighbors", ontology, name, str(depth)]
        if cached := await cache.get(namespace, parts):
            return cached
        result = await store.neighbors(claims["sub"], name, depth, ontology)
        await cache.set(namespace, parts, result)
        return result

    @app.get("/v1/knowledge/path")
    async def path(
        source: str, target: str, claims: Claims, ontology: str = "core"
    ) -> dict[str, Any]:
        require_ontology(ontology)
        namespace = f"knowledge:{claims['sub']}"
        parts = ["path", ontology, source, target]
        if cached := await cache.get(namespace, parts):
            return cached
        result = await store.path(claims["sub"], source, target, ontology)
        await cache.set(namespace, parts, result)
        return result

    @app.get("/mcp/tools")
    async def tools(_: Claims) -> dict[str, Any]:
        return {
            "tools": [
                {"name": "graph.search", "arguments": ["query", "limit", "ontology"]},
                {"name": "graph.visualize", "arguments": ["limit", "ontology", "entity_type"]},
                {"name": "graph.neighbors", "arguments": ["name", "depth", "ontology"]},
                {"name": "graph.path", "arguments": ["source", "target", "ontology"]},
                {"name": "graph.ontology", "arguments": ["ontology_id"]},
                {"name": "vector.search", "arguments": ["query", "limit", "ontology"]},
            ]
        }

    @app.post("/mcp/call")
    async def call_tool(call: McpCall, claims: Claims) -> dict[str, Any]:
        tenant = claims["sub"]
        if call.name == "graph.search":
            return await store.search(
                tenant,
                call.arguments["query"],
                call.arguments.get("limit", 50),
                call.arguments.get("ontology", "core"),
            )
        if call.name == "graph.visualize":
            ontology_id = call.arguments.get("ontology", "core")
            selected = require_ontology(ontology_id)
            entity_type = call.arguments.get("entity_type")
            if entity_type and entity_type not in {item.id for item in selected.entity_types}:
                raise HTTPException(status_code=422, detail=f"Unknown entity type: {entity_type}")
            return await store.browse(
                tenant,
                call.arguments.get("limit", 200),
                ontology_id,
                entity_type,
            )
        if call.name == "graph.neighbors":
            return await store.neighbors(
                tenant,
                call.arguments["name"],
                call.arguments.get("depth", 1),
                call.arguments.get("ontology", "core"),
            )
        if call.name == "graph.path":
            return await store.path(
                tenant,
                call.arguments["source"],
                call.arguments["target"],
                call.arguments.get("ontology", "core"),
            )
        if call.name == "graph.ontology":
            return require_ontology(call.arguments.get("ontology_id", "core")).model_dump(
                mode="json"
            )
        if call.name == "vector.search":
            try:
                return {
                    "results": await vector_store.search(
                        tenant,
                        call.arguments["query"],
                        call.arguments.get("limit", 10),
                        call.arguments.get("ontology"),
                    )
                }
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail="Unknown MCP tool")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/", include_in_schema=False)
    async def ui() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "knowledge.html")

    return app


app = create_app()
