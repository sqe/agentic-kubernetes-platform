import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from platform_runtime.kafka import KafkaWorker
from platform_runtime.settings import settings

from .extraction import KnowledgeExtractor
from .graph import GraphStore


def create_app(graph: GraphStore | None = None) -> FastAPI:
    store = graph or GraphStore(
        settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password
    )
    extractor = KnowledgeExtractor(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not settings.kafka_bootstrap_servers:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required for the knowledge worker")
        await store.initialize()
        worker = KafkaWorker(
            settings.kafka_bootstrap_servers,
            "tasks.knowledge",
            "results.knowledge",
            "knowledge-agent",
            extractor,
            settings.kafka_security_protocol,
        )
        task: asyncio.Task[Any] = asyncio.create_task(worker.run())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await extractor.close()
        await store.close()

    app = FastAPI(title="Knowledge Graph Worker", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy", "worker": "knowledge"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
