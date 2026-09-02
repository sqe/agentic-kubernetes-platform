import asyncio
import io
import json
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from platform_runtime.cache import Cache
from platform_runtime.contracts import JsonRpcError, JsonRpcResponse, normalize_task_request
from platform_runtime.observability import observe_task, trace_execution
from platform_runtime.settings import settings

from .graph import GraphStore
from .models import DocumentIngest, Entity, ExtractedGraph
from .object_store import ObjectStore
from .ontology import get_ontology
from .vector import VectorStore

SYSTEM_PROMPT = """Extract a compact knowledge graph from the document. Return JSON only:
{"entities":[{"name":"...","type":"ontology_entity_type",
"description":"..."}],"relationships":[{"source":"exact entity name",
"target":"exact entity name","type":"ontology_relationship_type","evidence":"short quote"}]}.
Include only facts supported by the document. Deduplicate entities and use stable,
specific names. Use only the ontology identifiers supplied below."""


class KnowledgeExtractor:
    def __init__(
        self,
        graph: GraphStore,
        client: httpx.AsyncClient | None = None,
        objects: ObjectStore | None = None,
        cache: Cache | None = None,
        vectors: VectorStore | None = None,
    ) -> None:
        self.graph = graph
        self.client = client or httpx.AsyncClient(timeout=120)
        self._owns_client = client is None
        self.objects = objects or ObjectStore()
        self.cache = cache or Cache(settings.redis_url, settings.cache_ttl_seconds)
        self.vectors = vectors or VectorStore.from_settings()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
        await self.cache.close()
        await self.vectors.close()

    async def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = normalize_task_request(payload)
        tenant = str(request.params.get("tenant") or request.params.get("user_id", ""))
        ontology_id = str(request.params.get("ontology", "core"))
        if request.method == "graph.ontology":
            ontology_id = str(
                request.params.get("ontology") or request.params.get("prompt", "core")
            )
            result = get_ontology(ontology_id).model_dump(mode="json")
            return JsonRpcResponse(id=request.id, result=result).model_dump(
                mode="json", exclude_none=True
            )
        if request.method == "graph.search":
            result = await self.graph.search(
                tenant,
                str(request.params.get("query") or request.params["prompt"]),
                int(request.params.get("limit", 50)),
                ontology_id,
            )
            return JsonRpcResponse(id=request.id, result=result).model_dump(
                mode="json", exclude_none=True
            )
        if request.method == "graph.neighbors":
            result = await self.graph.neighbors(
                tenant,
                str(request.params.get("name") or request.params["prompt"]),
                int(request.params.get("depth", 1)),
                ontology_id,
            )
            return JsonRpcResponse(id=request.id, result=result).model_dump(
                mode="json", exclude_none=True
            )
        if request.method == "graph.path":
            result = await self.graph.path(
                tenant,
                str(request.params["source"]),
                str(request.params["target"]),
                ontology_id,
            )
            return JsonRpcResponse(id=request.id, result=result).model_dump(
                mode="json", exclude_none=True
            )
        if request.method != "knowledge.ingest":
            return JsonRpcResponse(
                id=request.id, error=JsonRpcError(code=-32601, message="Method not found")
            ).model_dump(mode="json", exclude_none=True)
        document = DocumentIngest.model_validate(request.params["document"])
        tenant = str(request.params["tenant"])
        with observe_task("knowledge") as observed:
            text = document.text or await self._download(str(document.source_uri))
            ontology = get_ontology(document.ontology)
            graph = ontology.normalize(
                await self._extract(text, ontology.extraction_instructions())
            )
            await self.graph.persist(
                tenant, document.document_id, document.title, graph, document.ontology
            )
            vector_count = await self.vectors.index_document(
                tenant,
                document.document_id,
                document.title,
                document.ontology,
                str(document.source_uri) if document.source_uri else None,
                text,
            )
            await self.cache.invalidate(f"knowledge:{tenant}")
            observed["status"] = "success"
        result = {
            "document_id": document.document_id,
            "entities": len(graph.entities),
            "relationships": len(graph.relationships),
            "vectors": vector_count,
        }
        trace_execution("knowledge", request.id, result, settings.mlflow_tracking_uri)
        return JsonRpcResponse(
            id=request.id,
            result=result,
        ).model_dump(mode="json", exclude_none=True)

    async def _download(self, source_uri: str) -> str:
        if source_uri.startswith("s3://"):
            content, content_type = await self.objects.download(source_uri)
            return await self._as_text(content, content_type)
        parsed = urlparse(source_uri)
        allowed_hosts = {host.strip() for host in settings.document_source_hosts.split(",") if host}
        if parsed.scheme != "https" or (allowed_hosts and parsed.hostname not in allowed_hosts):
            raise ValueError("source_uri must be HTTPS and match DOCUMENT_SOURCE_HOSTS")
        response = await self.client.get(source_uri)
        response.raise_for_status()
        return await self._as_text(response.content, response.headers.get("content-type", ""))

    async def _as_text(self, content: bytes, content_type: str) -> str:
        if "pdf" in content_type or content.startswith(b"%PDF"):
            return await asyncio.to_thread(
                lambda: "\n".join(
                    page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages
                )
            )
        if "text/" in content_type or "json" in content_type:
            return content.decode(errors="replace")
        raise ValueError("Only PDF, text, and JSON documents are supported")

    async def _extract(self, text: str, ontology_instructions: str) -> ExtractedGraph:
        if not settings.openai_base_url or not settings.openai_model:
            raise RuntimeError("OPENAI_BASE_URL and OPENAI_MODEL are required for graph extraction")
        chunks = [
            text[index : index + settings.knowledge_chunk_chars]
            for index in range(0, len(text), settings.knowledge_chunk_chars)
        ][: settings.knowledge_max_chunks]
        partials = [
            await self._extract_chunk(chunk, ontology_instructions)
            for chunk in chunks
            if chunk.strip()
        ]
        entities: dict[str, Entity] = {}
        relationships = []
        for partial in partials:
            entities.update({entity.name.casefold(): entity for entity in partial.entities})
            relationships.extend(partial.relationships)
        return ExtractedGraph(
            entities=list(entities.values())[:500], relationships=relationships[:1000]
        )

    async def _extract_chunk(self, text: str, ontology_instructions: str) -> ExtractedGraph:
        headers = {"Content-Type": "application/json"}
        if settings.openai_api_key:
            headers["Authorization"] = f"Bearer {settings.openai_api_key}"
        response = await self.client.post(
            f"{settings.openai_base_url.rstrip('/')}/v1/chat/completions",
            headers=headers,
            json={
                "model": settings.openai_model,
                "messages": [
                    {"role": "system", "content": f"{SYSTEM_PROMPT}\n{ontology_instructions}"},
                    {"role": "user", "content": text},
                ],
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return ExtractedGraph.model_validate(json.loads(content))
