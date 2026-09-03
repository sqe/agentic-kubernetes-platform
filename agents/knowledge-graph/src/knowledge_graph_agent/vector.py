import hashlib
import math
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from platform_runtime.settings import settings


class VectorStore:
    def __init__(
        self,
        qdrant_url: str | None,
        qdrant_api_key: str | None,
        collection: str,
        embedding_url: str | None,
        embedding_model: str | None,
        embedding_api_key: str | None = None,
        qdrant_client: httpx.AsyncClient | None = None,
        embedding_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.qdrant_url = qdrant_url.rstrip("/") if qdrant_url else None
        self.collection = collection
        self.embedding_url = embedding_url.rstrip("/") if embedding_url else None
        self.embedding_model = embedding_model
        self.qdrant_headers = {"api-key": qdrant_api_key} if qdrant_api_key else {}
        self.embedding_headers = (
            {"Authorization": f"Bearer {embedding_api_key}"} if embedding_api_key else {}
        )
        self.qdrant = qdrant_client or httpx.AsyncClient(timeout=60)
        self.embedding = embedding_client or httpx.AsyncClient(timeout=60)
        self._owns_qdrant = qdrant_client is None
        self._owns_embedding = embedding_client is None

    @classmethod
    def from_settings(cls) -> "VectorStore":
        return cls(
            settings.qdrant_url,
            settings.qdrant_api_key,
            settings.qdrant_collection,
            settings.embedding_base_url,
            settings.embedding_model,
            settings.embedding_api_key,
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self.qdrant_url
            and self.embedding_model
            and (self.embedding_url or self.embedding_model == "local-hash-v1")
        )

    async def close(self) -> None:
        if self._owns_qdrant:
            await self.qdrant.aclose()
        if self._owns_embedding:
            await self.embedding.aclose()

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        if self.embedding_model == "local-hash-v1":
            return [self._local_embedding(text) for text in texts]
        if not self.embedding_url or not self.embedding_model:
            raise RuntimeError("EMBEDDING_BASE_URL and EMBEDDING_MODEL are required")
        response = await self.embedding.post(
            f"{self.embedding_url}/v1/embeddings",
            headers=self.embedding_headers,
            json={"model": self.embedding_model, "input": texts},
        )
        response.raise_for_status()
        rows = sorted(response.json()["data"], key=lambda item: item["index"])
        return [row["embedding"] for row in rows]

    @staticmethod
    def _local_embedding(text: str, dimensions: int = 384) -> list[float]:
        """Cheap deterministic lexical embedding for local demos without another model."""
        words = re.findall(r"[\w-]+", text.casefold())
        features = words + [
            word[index : index + 3] for word in words for index in range(len(word) - 2)
        ]
        vector = [0.0] * dimensions
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % dimensions] += 1 if digest[4] & 1 else -1
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def _ensure_collection(self, dimensions: int) -> None:
        response = await self.qdrant.get(
            f"{self.qdrant_url}/collections/{self.collection}", headers=self.qdrant_headers
        )
        if response.status_code != 404:
            response.raise_for_status()
            return
        response = await self.qdrant.put(
            f"{self.qdrant_url}/collections/{self.collection}",
            headers=self.qdrant_headers,
            json={"vectors": {"size": dimensions, "distance": "Cosine"}},
        )
        if response.status_code == 409:
            return
        response.raise_for_status()

    async def index_document(
        self,
        tenant: str,
        document_id: str,
        title: str,
        ontology: str,
        source_uri: str | None,
        text: str,
    ) -> int:
        if not self.enabled:
            return 0
        chunks = [
            text[index : index + settings.embedding_chunk_chars]
            for index in range(0, len(text), settings.embedding_chunk_chars)
        ][: settings.knowledge_max_chunks]
        chunks = [chunk for chunk in chunks if chunk.strip()]
        if not chunks:
            return 0
        vectors = await self._embed(chunks)
        await self._ensure_collection(len(vectors[0]))
        points = [
            {
                "id": str(uuid5(NAMESPACE_URL, f"{tenant}/{document_id}/{index}")),
                "vector": vector,
                "payload": {
                    "tenant": tenant,
                    "document_id": document_id,
                    "title": title,
                    "ontology": ontology,
                    "source_uri": source_uri,
                    "chunk": chunk,
                    "chunk_index": index,
                    "embedding_model": self.embedding_model,
                },
            }
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        response = await self.qdrant.put(
            f"{self.qdrant_url}/collections/{self.collection}/points",
            headers=self.qdrant_headers,
            params={"wait": "true"},
            json={"points": points},
        )
        response.raise_for_status()
        return len(points)

    async def search(
        self,
        tenant: str,
        query: str,
        limit: int = 10,
        ontology: str | None = None,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled:
            raise RuntimeError("Qdrant semantic search is not configured")
        vector = (await self._embed([query]))[0]
        must: list[dict[str, Any]] = [{"key": "tenant", "match": {"value": tenant}}]
        if ontology:
            must.append({"key": "ontology", "match": {"value": ontology}})
        if document_ids:
            must.append({"key": "document_id", "match": {"any": document_ids}})
        response = await self.qdrant.post(
            f"{self.qdrant_url}/collections/{self.collection}/points/query",
            headers=self.qdrant_headers,
            json={
                "query": vector,
                "filter": {"must": must},
                "limit": min(max(limit, 1), 50),
                "with_payload": True,
            },
        )
        response.raise_for_status()
        return response.json()["result"]["points"]
