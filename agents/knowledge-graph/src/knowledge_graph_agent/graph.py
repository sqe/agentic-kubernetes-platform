from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from .models import ExtractedGraph


class GraphStore:
    def __init__(self, uri: str, username: str, password: str) -> None:
        self.driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(username, password))

    async def close(self) -> None:
        await self.driver.close()

    async def initialize(self) -> None:
        await self.driver.execute_query(
            "CREATE CONSTRAINT entity_identity IF NOT EXISTS "
            "FOR (e:Entity) REQUIRE (e.tenant, e.ontology, e.name) IS UNIQUE"
        )

    async def persist(
        self,
        tenant: str,
        document_id: str,
        title: str,
        graph: ExtractedGraph,
        ontology: str = "core",
    ) -> None:
        await self.driver.execute_query(
            "MERGE (d:Document {tenant: $tenant, id: $document_id}) "
            "SET d.title = $title, d.ontology = $ontology",
            tenant=tenant,
            document_id=document_id,
            title=title,
            ontology=ontology,
        )
        for entity in graph.entities:
            await self.driver.execute_query(
                "MERGE (e:Entity {tenant: $tenant, ontology: $ontology, name: $name}) "
                "SET e.type = $type, e.description = $description "
                "WITH e MATCH (d:Document {tenant: $tenant, id: $document_id}) "
                "MERGE (d)-[:MENTIONS]->(e)",
                tenant=tenant,
                document_id=document_id,
                ontology=ontology,
                **entity.model_dump(),
            )
        for edge in graph.relationships:
            await self.driver.execute_query(
                "MATCH (a:Entity {tenant: $tenant, ontology: $ontology, name: $source}), "
                "(b:Entity {tenant: $tenant, ontology: $ontology, name: $target}) "
                "MERGE (a)-[r:RELATES {document_id: $document_id, type: $type}]->(b) "
                "SET r.evidence = $evidence",
                tenant=tenant,
                document_id=document_id,
                ontology=ontology,
                **edge.model_dump(),
            )

    async def search(
        self, tenant: str, query: str, limit: int = 50, ontology: str = "core"
    ) -> dict[str, Any]:
        records, _, _ = await self.driver.execute_query(
            "MATCH (e:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE toLower(e.name) CONTAINS toLower($query) "
            "OPTIONAL MATCH (e)-[r:RELATES]-(other:Entity {tenant: $tenant, ontology: $ontology}) "
            "RETURN collect(DISTINCT e {.*, id: elementId(e)})[0..$limit] AS roots, "
            "collect(DISTINCT other {.*, id: elementId(other)})[0..$limit] AS linked, "
            "collect(DISTINCT {source: elementId(startNode(r)), target: elementId(endNode(r)), "
            "type: r.type, evidence: r.evidence})[0..$limit] AS edges",
            tenant=tenant,
            query=query,
            limit=min(limit, 200),
            ontology=ontology,
        )
        if not records:
            return {"nodes": [], "edges": []}
        row = records[0]
        nodes = {item["id"]: item for item in [*row["roots"], *row["linked"]] if item}
        return {
            "nodes": list(nodes.values()),
            "edges": [edge for edge in row["edges"] if edge["source"]],
        }

    async def browse(
        self, tenant: str, limit: int = 200, ontology: str = "core", entity_type: str | None = None
    ) -> dict[str, Any]:
        """Return a bounded tenant graph for interactive visualization."""
        records, _, _ = await self.driver.execute_query(
            "MATCH (e:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE $entity_type IS NULL OR e.type = $entity_type "
            "WITH e ORDER BY e.name LIMIT $limit "
            "WITH collect(e) AS selected "
            "UNWIND selected AS e "
            "OPTIONAL MATCH (e)-[r:RELATES]->(other:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE other IN selected "
            "RETURN collect(DISTINCT e {.*, id: elementId(e)}) AS nodes, "
            "collect(DISTINCT {source: elementId(startNode(r)), target: elementId(endNode(r)), "
            "type: r.type, evidence: r.evidence}) AS edges",
            tenant=tenant,
            ontology=ontology,
            entity_type=entity_type,
            limit=min(max(limit, 1), 500),
        )
        if not records:
            return {"nodes": [], "edges": [], "stats": {"node_count": 0, "edge_count": 0}}
        row = dict(records[0])
        edges = [edge for edge in row["edges"] if edge["source"]]
        return {
            "nodes": row["nodes"],
            "edges": edges,
            "stats": {"node_count": len(row["nodes"]), "edge_count": len(edges)},
        }

    async def neighbors(
        self, tenant: str, name: str, depth: int = 1, ontology: str = "core"
    ) -> dict[str, Any]:
        records, _, _ = await self.driver.execute_query(
            "MATCH path=(root:Entity {tenant: $tenant, ontology: $ontology, name: $name})"
            "-[:RELATES*1..3]-(other:Entity) "
            "WHERE length(path) <= $depth "
            "AND all(n IN nodes(path) WHERE n.tenant = $tenant AND n.ontology = $ontology) "
            "UNWIND nodes(path) AS n UNWIND relationships(path) AS r "
            "RETURN collect(DISTINCT n {.*, id: elementId(n)}) AS nodes, "
            "collect(DISTINCT {source: elementId(startNode(r)), target: elementId(endNode(r)), "
            "type: r.type, evidence: r.evidence}) AS edges",
            tenant=tenant,
            name=name,
            depth=min(max(depth, 1), 3),
            ontology=ontology,
        )
        return dict(records[0]) if records else {"nodes": [], "edges": []}

    async def path(
        self, tenant: str, source: str, target: str, ontology: str = "core"
    ) -> dict[str, Any]:
        records, _, _ = await self.driver.execute_query(
            "MATCH path=shortestPath((a:Entity {tenant: $tenant, ontology: $ontology, "
            "name: $source})-[:RELATES*..8]-(b:Entity {tenant: $tenant, "
            "ontology: $ontology, name: $target})) "
            "WHERE all(n IN nodes(path) "
            "WHERE n.tenant = $tenant AND n.ontology = $ontology) "
            "RETURN [n IN nodes(path) | n {.*, id: elementId(n)}] AS nodes, "
            "[r IN relationships(path) | {source: elementId(startNode(r)), "
            "target: elementId(endNode(r)), type: r.type, evidence: r.evidence}] AS edges",
            tenant=tenant,
            source=source,
            target=target,
            ontology=ontology,
        )
        return dict(records[0]) if records else {"nodes": [], "edges": []}
