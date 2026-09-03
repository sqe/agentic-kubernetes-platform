import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, HTTPException
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from platform_runtime.kafka import KafkaWorker
from platform_runtime.settings import settings

from .extraction import KnowledgeExtractor
from .graph import GraphStore

logger = logging.getLogger(__name__)


def create_app(graph: GraphStore | None = None) -> FastAPI:
    store = graph or GraphStore(
        settings.neo4j_uri, settings.neo4j_username, settings.neo4j_password
    )
    extractor = KnowledgeExtractor(store)

    def report_worker_exit(task: asyncio.Task[Any]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error:
            logger.error(
                "Knowledge Kafka worker exited",
                exc_info=(type(error), error, error.__traceback__),
            )
        else:
            logger.error("Knowledge Kafka worker stopped unexpectedly")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not settings.kafka_bootstrap_servers:
            raise RuntimeError("KAFKA_BOOTSTRAP_SERVERS is required for the knowledge worker")
        await store.initialize()
        await extractor.start()
        worker = KafkaWorker(
            settings.kafka_bootstrap_servers,
            "tasks.knowledge",
            "results.knowledge",
            "knowledge-agent",
            extractor,
            settings.kafka_security_protocol,
        )
        task: asyncio.Task[Any] = asyncio.create_task(worker.run())
        task.add_done_callback(report_worker_exit)
        app.state.worker_task = task
        yield
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        await extractor.close()
        await store.close()

    app = FastAPI(title="Knowledge Graph Worker", version="0.1.0", lifespan=lifespan)
    app.state.worker_task = None

    @app.get("/health")
    async def health() -> dict[str, str]:
        task = app.state.worker_task
        if task is None or task.done():
            raise HTTPException(status_code=503, detail="Kafka worker is not running")
        return {"status": "healthy", "worker": "knowledge"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
