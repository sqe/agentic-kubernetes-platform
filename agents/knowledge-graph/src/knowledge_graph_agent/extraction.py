import asyncio
import base64
import io
import json
import re
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse

import httpx
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image
from pydantic import ValidationError
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
from .vision import VisionAgentClient

SYSTEM_PROMPT = """Extract a compact knowledge graph from the document. Return JSON only:
{"entities":[{"name":"...","type":"ontology_entity_type",
"description":"..."}],"relationships":[{"source":"exact entity name",
"target":"exact entity name","type":"ontology_relationship_type","evidence":"short quote"}]}.
Include only facts supported by the document. Deduplicate entities and use stable,
specific names. Use only the ontology identifiers supplied below.
Return AT MOST 25 entities and 25 relationships per response, keeping descriptions
under 20 words so the JSON fits in the response limit."""


class ProgressTracker:
    """Writes ingestion progress to Redis so the UI can stream it live via SSE."""

    def __init__(self, redis, task_id: str) -> None:
        self.redis = redis
        self.key = f"progress:{task_id}"
        self.pages_total = 0
        self.pages_done = 0
        self.chunks_total = 0
        self.chunks_done = 0
        self.entities = 0
        self.relationships = 0

    async def emit(self, phase: str, current: str, latest_event: str) -> None:
        if self.redis:
            await self.redis.set(
                self.key,
                json.dumps(
                    {
                        "phase": phase,
                        "current": current,
                        "latest_event": latest_event,
                        "pages_total": self.pages_total,
                        "pages_done": self.pages_done,
                        "chunks_total": self.chunks_total,
                        "chunks_done": self.chunks_done,
                        "entities": self.entities,
                        "relationships": self.relationships,
                    }
                ),
                ex=3600,
            )

    async def start_vision(self, pages_total: int) -> None:
        self.pages_total = pages_total
        await self.emit(
            "vision", f"Analyzing {pages_total} page{'s' if pages_total != 1 else ''}...", ""
        )

    async def page_done(self, page: int, caption: str) -> None:
        self.pages_done += 1
        await self.emit(
            "vision",
            f"Picture {self.pages_done}/{self.pages_total} (PDF page {page})",
            caption[:120],
        )

    async def start_extraction(self, chunks_total: int) -> None:
        self.chunks_total = chunks_total
        await self.emit(
            "extraction",
            f"Extracting from {chunks_total} chunk{'s' if chunks_total != 1 else ''}...",
            "",
        )

    async def chunk_done(self, chunk: int, n_entities: int, n_rels: int) -> None:
        self.chunks_done = chunk
        self.entities += n_entities
        self.relationships += n_rels
        await self.emit(
            "extraction",
            f"Chunk {chunk}/{self.chunks_total}",
            f"+{n_entities} entities, +{n_rels} relationships",
        )

    async def complete(self) -> None:
        await self.emit(
            "complete",
            "Ingestion complete",
            f"{self.entities} entities, {self.relationships} relationships extracted",
        )

    async def error(self, message: str) -> None:
        await self.emit("error", "Ingestion failed", message[:200])


def _complete_truncated_json(text: str) -> str | None:
    """Best-effort repair of JSON cut off by the model's output token cap.

    Cuts back to the most recent structurally valid element and closes any
    open brackets so partial extraction results can still be ingested.
    """

    def close(candidate: str) -> str | None:
        stack: list[str] = []
        in_string = False
        escape = False
        for char in candidate:
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "{[":
                stack.append(char)
            elif char in "}]":
                if not stack:
                    return None
                stack.pop()
        if in_string or not stack:
            return None
        return candidate + "".join("]" if opener == "[" else "}" for opener in reversed(stack))

    tried = 0
    for index in range(len(text) - 1, -1, -1):
        if text[index] not in "{}[]":
            continue
        tried += 1
        if tried > 100:
            return None
        repaired = close(text[: index + 1])
        if repaired is None:
            continue
        try:
            ExtractedGraph.model_validate(json.loads(repaired))
        except (json.JSONDecodeError, ValidationError):
            continue
        return repaired
    return None


def _parse_extraction_json(content: str) -> ExtractedGraph:
    """Parse model output, repairing JSON truncated by the token limit."""
    try:
        return ExtractedGraph.model_validate(json.loads(content))
    except (json.JSONDecodeError, ValidationError):
        repaired = _complete_truncated_json(content)
        if repaired is None:
            raise
        return ExtractedGraph.model_validate(json.loads(repaired))


class KnowledgeExtractor:
    def __init__(
        self,
        graph: GraphStore,
        client: httpx.AsyncClient | None = None,
        objects: ObjectStore | None = None,
        cache: Cache | None = None,
        vectors: VectorStore | None = None,
        vision: VisionAgentClient | None = None,
    ) -> None:
        self.graph = graph
        self.client = client or httpx.AsyncClient(timeout=120)
        self._owns_client = client is None
        self.objects = objects or ObjectStore()
        self.cache = cache or Cache(settings.redis_url, settings.cache_ttl_seconds)
        self.vectors = vectors or VectorStore.from_settings()
        self.vision = vision or VisionAgentClient()
        self._progress: ProgressTracker | None = None

    async def start(self) -> None:
        await self.vision.start()

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
        await self.cache.close()
        await self.vectors.close()
        await self.vision.close()

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
        await self.graph.set_document_status(tenant, document.document_id, "processing")
        self._progress = ProgressTracker(self.cache.redis, str(request.id))
        try:
            with observe_task("knowledge") as observed:
                if document.text:
                    text, visual_count = document.text, 0
                    await self._progress.start_vision(0)
                else:
                    text, visual_count = await self._download_with_visuals(
                        str(document.source_uri), tenant, document.document_id
                    )
                vector_count = await self.vectors.index_document(
                    tenant,
                    document.document_id,
                    document.title,
                    document.ontology,
                    str(document.source_uri) if document.source_uri else None,
                    text,
                )
                await self.graph.set_document_status(
                    tenant,
                    document.document_id,
                    "processing",
                    vectors=vector_count,
                    visuals=visual_count,
                )
                ontology = get_ontology(document.ontology)
                graph = ontology.normalize(
                    await self._extract(text, ontology.extraction_instructions())
                )
                # Store extracted graph JSON and full text in RustFS for RAG and auditability.
                graph_json = graph.model_dump_json(indent=2)
                extracted_uri = await self.objects.upload(
                    tenant,
                    document.document_id,
                    "extracted-graph.json",
                    io.BytesIO(graph_json.encode()),
                    "application/json",
                )
                text_uri = await self.objects.upload(
                    tenant,
                    document.document_id,
                    "extracted-text.txt",
                    io.BytesIO(text.encode()),
                    "text/plain",
                )
                await self.graph.persist(
                    tenant, document.document_id, document.title, graph, document.ontology
                )
                await self.graph.link_visuals(tenant, document.document_id, document.ontology)
                await self.graph.set_document_status(
                    tenant,
                    document.document_id,
                    "completed",
                    entities=len(graph.entities),
                    relationships=len(graph.relationships),
                    vectors=vector_count,
                    visuals=visual_count,
                    extracted_uri=extracted_uri,
                    text_uri=text_uri,
                )
                with suppress(Exception):
                    await self._progress.complete()
                namespace = f"knowledge:{tenant}"
                # Cache warming is an optimization, not part of ingestion correctness.
                with suppress(Exception):
                    await self.cache.invalidate(namespace)
                    await self.cache.set(
                        namespace,
                        ["documents", document.ontology],
                        {"documents": await self.graph.documents(tenant, document.ontology)},
                    )
                    await self.cache.set(
                        namespace,
                        ["stats", document.ontology],
                        await self.graph.metrics(tenant, document.ontology),
                    )
                    await self.cache.set(
                        namespace,
                        ["graph", document.ontology, "all", "200"],
                        await self.graph.browse(tenant, 200, document.ontology),
                    )
                observed["status"] = "success"
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            if self._progress:
                with suppress(Exception):
                    await self._progress.error(message)
            await self.graph.set_document_status(
                tenant, document.document_id, "failed", message[:500]
            )
            with suppress(Exception):
                await self.cache.invalidate(f"knowledge:{tenant}")
            raise
        result = {
            "document_id": document.document_id,
            "entities": len(graph.entities),
            "relationships": len(graph.relationships),
            "vectors": vector_count,
            "visuals": visual_count,
        }
        trace_execution("knowledge", request.id, result, settings.mlflow_tracking_uri)
        return JsonRpcResponse(
            id=request.id,
            result=result,
        ).model_dump(mode="json", exclude_none=True)

    async def _download(self, source_uri: str) -> str:
        text, _ = await self._download_with_visuals(source_uri)
        return text

    async def _download_with_visuals(
        self, source_uri: str, tenant: str | None = None, document_id: str | None = None
    ) -> tuple[str, int]:
        if source_uri.startswith("s3://"):
            content, content_type = await self.objects.download(source_uri)
        else:
            parsed = urlparse(source_uri)
            allowed_hosts = {
                host.strip() for host in settings.document_source_hosts.split(",") if host
            }
            if parsed.scheme != "https" or (allowed_hosts and parsed.hostname not in allowed_hosts):
                raise ValueError("source_uri must be HTTPS and match DOCUMENT_SOURCE_HOSTS")
            response = await self.client.get(source_uri)
            response.raise_for_status()
            content, content_type = response.content, response.headers.get("content-type", "")
        text = await self._as_text(content, content_type)
        if not self.vision.enabled or not ("pdf" in content_type or content.startswith(b"%PDF")):
            if self._progress:
                await self._progress.start_vision(0)
            return text, 0
        pages = await asyncio.to_thread(self._extract_pdf_visuals, content)
        if self._progress:
            await self._progress.start_vision(len(pages))
        if tenant and document_id:
            await self.graph.clear_visuals(tenant, document_id)
        captions = []
        for page_number, image, bounds in pages:
            caption = await self.vision.describe(base64.b64encode(image).decode(), page_number)
            if self._progress:
                await self._progress.page_done(page_number, caption)
            captions.append(f"[Visual analysis, page {page_number}]\n{caption}")
            if tenant and document_id:
                image_uri = await self.objects.upload(
                    tenant,
                    document_id,
                    f"page-{page_number:04d}.jpg",
                    io.BytesIO(image),
                    "image/jpeg",
                )
                caption_uri = await self.objects.upload(
                    tenant,
                    document_id,
                    f"page-{page_number:04d}.txt",
                    io.BytesIO(caption.encode()),
                    "text/plain",
                )
                await self.graph.persist_visual(
                    tenant,
                    document_id,
                    page_number,
                    image_uri,
                    caption_uri,
                    caption,
                    bounds,
                )
        return "\n\n".join([text, *captions]), len(captions)

    @staticmethod
    def _extract_pdf_visuals(content: bytes) -> list[tuple[int, bytes, list[float]]]:
        """Extract the largest embedded picture per page, excluding page text screenshots."""
        document = pdfium.PdfDocument(content)
        rendered: list[tuple[int, bytes, list[float]]] = []
        scale = 1.5
        for index in range(min(len(document), settings.knowledge_visual_page_limit)):
            page = document[index]
            width, height = page.get_size()
            candidates = []
            for item in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE]):
                left, bottom, right, top = item.get_bounds()
                area = max(0, right - left) * max(0, top - bottom)
                if width * height * 0.02 <= area <= width * height * 0.85:
                    candidates.append((area, (left, bottom, right, top)))
            if not candidates:
                continue
            _, (left, bottom, right, top) = max(candidates)
            page_image = page.render(scale=scale).to_pil().convert("RGB")
            margin = 8
            crop = (
                max(0, int(left * scale) - margin),
                max(0, int((height - top) * scale) - margin),
                min(page_image.width, int(right * scale) + margin),
                min(page_image.height, int((height - bottom) * scale) + margin),
            )
            image = page_image.crop(crop)
            if image.width < 120 or image.height < 80:
                continue
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=70, optimize=True)
            rendered.append((index + 1, output.getvalue(), [left, bottom, right, top]))
        return rendered

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
        chunks = [chunk for chunk in chunks if chunk.strip()]
        if self._progress:
            await self._progress.start_extraction(len(chunks))
        entities: dict[str, Entity] = {}
        relationships = []
        for index, chunk in enumerate(chunks, 1):
            partial = await self._extract_chunk(chunk, ontology_instructions)
            if self._progress:
                await self._progress.chunk_done(
                    index, len(partial.entities), len(partial.relationships)
                )
            entities.update({entity.name.casefold(): entity for entity in partial.entities})
            relationships.extend(partial.relationships)
        entities, relationships = self._deduplicate_entities(entities, relationships)
        return ExtractedGraph(
            entities=list(entities.values())[:500], relationships=relationships[:1000]
        )

    @staticmethod
    def _deduplicate_entities(
        entities: dict[str, Entity], relationships: list
    ) -> tuple[dict[str, Entity], list]:
        """Collapse conservative, same-type expanded-name aliases.

        e.g. "Roman Space Telescope" → "Nancy Grace Roman Space Telescope"
        while leaving short ambiguous names such as "Roman" untouched.
        """
        tokens = {
            name: re.findall(r"[a-z0-9]+", entity.name.casefold())
            for name, entity in entities.items()
        }
        canonical: dict[str, str] = {}
        for short_name, short_entity in entities.items():
            short_tokens = tokens[short_name]
            if len(short_tokens) < 3:
                continue
            candidates = []
            for long_name, long_entity in entities.items():
                long_tokens = tokens[long_name]
                if long_name == short_name or long_entity.type != short_entity.type:
                    continue
                if len(long_tokens) <= len(short_tokens):
                    continue
                if long_tokens[-len(short_tokens) :] == short_tokens:
                    candidates.append(long_name)
            if candidates:
                canonical[short_name] = max(candidates, key=lambda name: len(tokens[name]))
        if not canonical:
            return entities, relationships

        merged = {name: entity.model_copy(deep=True) for name, entity in entities.items()}
        for cf_name, entity in entities.items():
            target = canonical.get(cf_name)
            if not target:
                continue
            target_entity = merged[target]
            target_entity.aliases = sorted({*target_entity.aliases, entity.name})
            if len(entity.description) > len(target_entity.description):
                target_entity.description = entity.description
            del merged[cf_name]

        for rel in relationships:
            source = canonical.get(rel.source.casefold())
            target = canonical.get(rel.target.casefold())
            if source:
                rel.source = merged[source].name
            if target:
                rel.target = merged[target].name
        return merged, relationships

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
                "max_tokens": settings.knowledge_max_output_tokens,
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        content = message.get("content")
        if not content:
            raise RuntimeError(
                "Model returned no final content; disable reasoning or raise max tokens"
            )
        return _parse_extraction_json(content)
