import io
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from knowledge_graph_agent.app import agent_card, create_app
from knowledge_graph_agent.auth import verify_jwt
from knowledge_graph_agent.extraction import KnowledgeExtractor
from knowledge_graph_agent.graph import GraphStore
from knowledge_graph_agent.models import DocumentIngest, Entity, ExtractedGraph, Relationship
from knowledge_graph_agent.object_store import ObjectStore
from knowledge_graph_agent.ontology import get_ontology
from knowledge_graph_agent.users import UserProfile, UserStore
from knowledge_graph_agent.vector import VectorStore
from pydantic import ValidationError

from platform_runtime.settings import settings


class FakeGraph:
    initialized = False
    closed = False

    async def initialize(self):
        self.initialized = True

    async def close(self):
        self.closed = True

    async def persist(self, tenant, document_id, title, graph, ontology="core"):
        self.persisted = (tenant, document_id, title, graph, ontology)

    async def search(self, tenant, query, limit=50, ontology="core"):
        return {"nodes": [{"id": "1", "name": query, "tenant": tenant}], "edges": []}

    async def browse(self, tenant, limit=200, ontology="core", entity_type=None):
        return {
            "nodes": [{"id": "1", "name": "JWST", "type": entity_type or "observatory"}],
            "edges": [],
            "stats": {"node_count": 1, "edge_count": 0},
        }

    async def neighbors(self, tenant, name, depth=1, ontology="core"):
        return {"nodes": [{"id": "1", "name": name, "depth": depth}], "edges": []}

    async def path(self, tenant, source, target, ontology="core"):
        return {"nodes": [{"name": source}, {"name": target}], "edges": []}


class FakeObjects:
    async def upload(self, tenant, document_id, filename, body, content_type):
        assert body.read() == b"document"
        return f"s3://agent-documents/{tenant}/{document_id}/{filename}"

    async def download(self, uri):
        return b"stored text", "text/plain"


class FakeCache:
    invalidated = []

    async def invalidate(self, namespace):
        self.invalidated.append(namespace)

    async def close(self):
        pass


class FakeVectors:
    def __init__(self):
        self.indexed = None
        self.closed = False

    async def index_document(self, *args):
        self.indexed = args
        return 1

    async def search(self, tenant, query, limit=10, ontology=None):
        return [{"score": 0.9, "payload": {"tenant": tenant, "title": query}}]

    async def close(self):
        self.closed = True


class FakeUsers:
    started = False
    closed = False

    async def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def sync(self, claims, default_issuer):
        now = datetime.now(UTC)
        return UserProfile(
            issuer=claims.get("iss", default_issuer),
            subject=claims["sub"],
            email=claims.get("email"),
            display_name=claims.get("name"),
            created_at=now,
            last_seen_at=now,
        )


def test_document_validation_and_card():
    assert agent_card().task_topic == "tasks.knowledge"
    assert DocumentIngest(document_id="1", title="Title", text="body").text == "body"
    with pytest.raises(ValidationError, match="exactly one"):
        DocumentIngest(document_id="1", title="Title")
    with pytest.raises(ValidationError, match="s3:// or https://"):
        DocumentIngest(document_id="1", title="Title", source_uri="http://example.test/a")


def test_ontology_constraints():
    astronomy = get_ontology("astronomy")
    graph = astronomy.normalize(
        ExtractedGraph(
            entities=[
                Entity(name="JWST", type="observatory"),
                Entity(name="MIRI", type="instrument"),
                Entity(name="Unknown", type="invented"),
            ],
            relationships=[
                Relationship(source="JWST", target="MIRI", type="contains"),
                Relationship(source="Unknown", target="MIRI", type="contains"),
                Relationship(source="JWST", target="Missing", type="contains"),
                Relationship(source="JWST", target="MIRI", type="invented"),
            ],
        )
    )
    assert graph.entities[2].type == "concept"
    assert [edge.type for edge in graph.relationships] == ["contains"]
    assert "Entity types" in astronomy.extraction_instructions()
    industry = get_ontology("industry")
    assert len(industry.entity_types) == 15
    assert len(industry.relationship_types) == 20
    with pytest.raises(ValueError, match="Unknown ontology"):
        get_ontology("missing")


def test_knowledge_api_queries_and_mcp(monkeypatch):
    monkeypatch.setattr(settings, "kafka_bootstrap_servers", None)
    monkeypatch.setattr(settings, "auth_disabled", False)
    monkeypatch.setattr(settings, "oidc_client_id", "graph-ui")
    monkeypatch.setattr(settings, "oidc_authorization_endpoint", "https://login.test/authorize")
    monkeypatch.setattr(settings, "oidc_token_endpoint", "https://login.test/token")
    monkeypatch.setattr(settings, "oidc_registration_endpoint", "https://login.test/register")
    monkeypatch.setattr(settings, "oidc_logout_endpoint", "https://login.test/logout")
    graph = FakeGraph()
    vectors = FakeVectors()
    users = FakeUsers()
    app = create_app(graph, FakeObjects(), register=False, vectors=vectors, users=users)
    app.dependency_overrides[verify_jwt] = lambda: {
        "iss": "https://identity.test",
        "sub": "tenant-a",
        "email": "user@example.test",
        "name": "Example User",
    }
    with TestClient(app) as client:
        assert client.get("/.well-known/agent.json").json()["name"] == "knowledge-graph"
        assert client.get("/auth/config").json() == {
            "enabled": True,
            "client_id": "graph-ui",
            "authorization_endpoint": "https://login.test/authorize",
            "token_endpoint": "https://login.test/token",
            "logout_endpoint": "https://login.test/logout",
            "registration_endpoint": "https://login.test/register",
            "scope": "openid profile email",
        }
        assert client.get("/v1/knowledge/search", params={"q": "JWST"}).status_code == 200
        graph_response = client.get(
            "/v1/knowledge/graph",
            params={"ontology": "astronomy", "entity_type": "observatory"},
        )
        assert graph_response.json()["stats"]["node_count"] == 1
        assert (
            client.get(
                "/v1/knowledge/graph", params={"ontology": "astronomy", "entity_type": "invalid"}
            ).status_code
            == 422
        )
        assert client.get("/v1/users/me").json()["subject"] == "tenant-a"
        assert client.get("/v1/knowledge/neighbors/MIRI", params={"depth": 2}).status_code == 200
        assert (
            client.get("/v1/knowledge/path", params={"source": "JWST", "target": "MIRI"}).json()[
                "nodes"
            ][1]["name"]
            == "MIRI"
        )
        assert len(client.get("/mcp/tools").json()["tools"]) == 6
        assert (
            client.get("/v1/knowledge/semantic-search", params={"q": "mirror"}).status_code == 200
        )
        ontologies = client.get("/v1/knowledge/ontologies").json()["ontologies"]
        assert {item["id"] for item in ontologies} == {"core", "astronomy", "industry"}
        assert client.get("/v1/knowledge/ontologies/astronomy").status_code == 200
        assert client.get("/v1/knowledge/ontologies/missing").status_code == 404
        for name, arguments in [
            ("graph.search", {"query": "NIRCam"}),
            ("graph.visualize", {"ontology": "astronomy"}),
            ("graph.neighbors", {"name": "NIRCam", "depth": 2}),
            ("graph.path", {"source": "JWST", "target": "NIRCam"}),
            ("graph.ontology", {"ontology_id": "astronomy"}),
            ("vector.search", {"query": "mirror"}),
        ]:
            response = client.post("/mcp/call", json={"name": name, "arguments": arguments})
            assert response.status_code == 200
        assert client.post("/mcp/call", json={"name": "unknown"}).status_code == 404
        assert client.get("/health").json() == {"status": "healthy"}
        assert client.get("/metrics").status_code == 200
        ui = client.get("/")
        assert ui.status_code == 200
        assert "Create account" in ui.text
        assert "code_challenge_method: 'S256'" in ui.text
        assert "request('/v1/users/me')" in ui.text
        assert (
            client.post(
                "/v1/knowledge/documents",
                json={"document_id": "1", "title": "Test", "text": "text"},
            ).status_code
            == 503
        )
        assert (
            client.post(
                "/v1/knowledge/documents/upload",
                data={"title": "Test"},
                files={"file": ("bad.bin", b"document", "application/octet-stream")},
            ).status_code
            == 415
        )
    assert graph.initialized and graph.closed and vectors.closed
    assert users.started and users.closed


@pytest.mark.asyncio
async def test_postgresql_user_profile_sync(monkeypatch):
    now = datetime.now(UTC)

    class FakePool:
        closed = False

        async def execute(self, query):
            self.schema = query

        async def fetchrow(self, query, *args):
            self.synced = (query, args)
            return {
                "issuer": args[0],
                "subject": args[1],
                "email": args[2],
                "display_name": args[3],
                "created_at": now,
                "last_seen_at": now,
            }

        async def close(self):
            self.closed = True

    pool = FakePool()

    async def create_pool(*args, **kwargs):
        return pool

    monkeypatch.setattr("knowledge_graph_agent.users.asyncpg.create_pool", create_pool)
    store = UserStore("postgresql://database")
    await store.start()
    profile = await store.sync(
        {"iss": "https://identity.test", "sub": "user-1", "email": "user@example.test"},
        None,
    )
    assert profile.subject == "user-1"
    assert profile.display_name == "user@example.test"
    await store.close()
    assert pool.closed


@pytest.mark.asyncio
async def test_extractor_text_and_model(monkeypatch):
    monkeypatch.setattr(settings, "openai_base_url", "http://model")
    monkeypatch.setattr(settings, "openai_model", "demo")
    graph = FakeGraph()
    cache = FakeCache()
    vectors = FakeVectors()

    def model_response(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"entities":[{"name":"JWST","type":"observatory"},'
                                '{"name":"MIRI","type":"instrument"}],'
                                '"relationships":[{"source":"JWST",'
                                '"target":"MIRI","type":"contains"}]}'
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(model_response))
    extractor = KnowledgeExtractor(
        graph, client=client, objects=FakeObjects(), cache=cache, vectors=vectors
    )
    result = await extractor(
        {
            "jsonrpc": "2.0",
            "id": "task-1",
            "method": "knowledge.ingest",
            "params": {
                "tenant": "tenant-a",
                "document": {
                    "document_id": "doc-1",
                    "title": "JWST",
                    "text": "Space telescope",
                    "ontology": "astronomy",
                },
            },
        }
    )
    assert result["result"] == {
        "document_id": "doc-1",
        "entities": 2,
        "relationships": 1,
        "vectors": 1,
    }
    assert graph.persisted[0:3] == ("tenant-a", "doc-1", "JWST")
    assert graph.persisted[4] == "astronomy"
    assert cache.invalidated == ["knowledge:tenant-a"]
    assert vectors.indexed[0:4] == ("tenant-a", "doc-1", "JWST", "astronomy")
    search = await extractor(
        {"jsonrpc": "2.0", "id": "search", "method": "graph.search", "params": {"query": "JWST"}}
    )
    assert search["result"]["nodes"][0]["name"] == "JWST"
    routed_search = await extractor(
        {
            "jsonrpc": "2.0",
            "id": "routed-search",
            "method": "tasks.execute",
            "params": {"skill": "graph.search", "prompt": "MIRI", "user_id": "U1"},
        }
    )
    assert routed_search["result"]["nodes"][0] == {"id": "1", "name": "MIRI", "tenant": "U1"}
    neighbors = await extractor(
        {
            "jsonrpc": "2.0",
            "id": "neighbors",
            "method": "graph.neighbors",
            "params": {"name": "MIRI", "depth": 2},
        }
    )
    assert neighbors["result"]["nodes"][0]["depth"] == 2
    path = await extractor(
        {
            "jsonrpc": "2.0",
            "id": "path",
            "method": "graph.path",
            "params": {"source": "JWST", "target": "MIRI"},
        }
    )
    assert path["result"]["nodes"][1]["name"] == "MIRI"
    ontology = await extractor(
        {
            "jsonrpc": "2.0",
            "id": "ontology",
            "method": "graph.ontology",
            "params": {"ontology": "astronomy"},
        }
    )
    assert ontology["result"]["id"] == "astronomy"
    invalid = await extractor({"jsonrpc": "2.0", "id": "2", "method": "other"})
    assert invalid["error"]["code"] == -32601
    await extractor.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_qdrant_index_and_tenant_filtered_search(monkeypatch):
    monkeypatch.setattr(settings, "embedding_chunk_chars", 10)
    qdrant_calls = []

    def qdrant_response(request):
        assert request.headers["api-key"] == "qdrant-secret"
        qdrant_calls.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/points/query"):
            return httpx.Response(
                200,
                json={"result": {"points": [{"score": 0.8, "payload": {"title": "JWST"}}]}},
            )
        return httpx.Response(200, json={"status": "ok"})

    def embedding_response(request):
        assert "api-key" not in request.headers
        assert request.headers["authorization"] == "Bearer embedding-secret"
        inputs = request.read()
        count = len(__import__("json").loads(inputs)["input"])
        return httpx.Response(
            200,
            json={"data": [{"index": index, "embedding": [0.1, 0.2]} for index in range(count)]},
        )

    qdrant_client = httpx.AsyncClient(transport=httpx.MockTransport(qdrant_response))
    embedding_client = httpx.AsyncClient(transport=httpx.MockTransport(embedding_response))
    store = VectorStore(
        "http://qdrant",
        "qdrant-secret",
        "chunks",
        "http://embeddings",
        "embed-model",
        "embedding-secret",
        qdrant_client,
        embedding_client,
    )
    assert (
        await store.index_document(
            "tenant-a", "doc-1", "JWST", "astronomy", "s3://documents/doc-1", "1234567890abc"
        )
        == 2
    )
    results = await store.search("tenant-a", "mirror", ontology="astronomy")
    assert results[0]["payload"]["title"] == "JWST"
    query = __import__("json").loads(qdrant_calls[-1].read())["filter"]["must"]
    assert query == [
        {"key": "tenant", "match": {"value": "tenant-a"}},
        {"key": "ontology", "match": {"value": "astronomy"}},
    ]
    await qdrant_client.aclose()
    await embedding_client.aclose()


@pytest.mark.asyncio
async def test_extractor_sources(monkeypatch):
    extractor = KnowledgeExtractor(
        FakeGraph(),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, text="remote text"))
        ),
        objects=FakeObjects(),
        cache=FakeCache(),
    )
    assert await extractor._download("s3://agent-documents/doc") == "stored text"
    monkeypatch.setattr(settings, "document_source_hosts", "example.test")
    assert await extractor._download("https://example.test/doc.txt") == "remote text"
    with pytest.raises(ValueError, match="DOCUMENT_SOURCE_HOSTS"):
        await extractor._download("https://other.test/doc.txt")
    with pytest.raises(ValueError, match="Only PDF"):
        await extractor._as_text(b"binary", "application/octet-stream")
    await extractor.client.aclose()


class FakeS3:
    def upload_fileobj(self, body, bucket, key, ExtraArgs):
        self.uploaded = (body.read(), bucket, key, ExtraArgs)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(b"downloaded"), "ContentType": "text/plain"}


@pytest.mark.asyncio
async def test_object_store_paths():
    store = object.__new__(ObjectStore)
    store.bucket = "agent-documents"
    store.client = FakeS3()
    uri = await store.upload(
        "tenant", "doc", "../JWST Observatory.pdf", io.BytesIO(b"pdf"), "application/pdf"
    )
    assert uri.endswith("/doc/JWST_Observatory.pdf")
    assert await store.download(uri) == (b"downloaded", "text/plain")
    with pytest.raises(ValueError, match="outside"):
        await store.download("s3://other/document")


class FakeDriver:
    def __init__(self):
        self.responses = []
        self.calls = []

    async def execute_query(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return self.responses.pop(0) if self.responses else ([], None, None)

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_graph_store_queries():
    store = object.__new__(GraphStore)
    store.driver = FakeDriver()
    graph = ExtractedGraph(
        entities=[Entity(name="JWST")],
        relationships=[Relationship(source="JWST", target="MIRI")],
    )
    await store.initialize()
    await store.persist("tenant", "doc", "Title", graph)
    store.driver.responses.append(
        ([{"roots": [{"id": "1", "name": "JWST"}], "linked": [], "edges": []}], None, None)
    )
    assert (await store.search("tenant", "JWST"))["nodes"][0]["name"] == "JWST"
    assert await store.search("tenant", "missing") == {"nodes": [], "edges": []}
    assert await store.browse("tenant") == {
        "nodes": [],
        "edges": [],
        "stats": {"node_count": 0, "edge_count": 0},
    }
    assert await store.neighbors("tenant", "JWST") == {"nodes": [], "edges": []}
    assert await store.path("tenant", "JWST", "MIRI") == {"nodes": [], "edges": []}
    await store.close()
    assert store.driver.closed
