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
        await self.driver.execute_query(
            "CREATE CONSTRAINT document_identity IF NOT EXISTS "
            "FOR (d:Document) REQUIRE (d.tenant, d.id) IS UNIQUE"
        )
        await self.driver.execute_query(
            "MATCH (d:Document)-[:MENTIONS]->(e:Entity) "
            "WHERE e.type <> 'category' AND e.type IS NOT NULL "
            "MERGE (category:Entity {tenant: e.tenant, ontology: e.ontology, "
            "name: 'Category · ' + e.type}) "
            "SET category.type = 'category', "
            "category.description = 'Ontology category for ' + e.type + ' entities' "
            "MERGE (d)-[:MENTIONS]->(category) "
            "MERGE (e)-[:RELATES {document_id: d.id, type: 'classified_as'}]->(category)"
        )
        await self.driver.execute_query(
            "MATCH (:Entity)-[r:ILLUSTRATED_BY]->(v:Visual) "
            "WHERE v.bounds IS NULL DELETE r"
        )
        await self.driver.execute_query(
            "MATCH (d:Document)-[:HAS_VISUAL]->(v:Visual) "
            "MATCH (e:Entity {tenant: d.tenant, ontology: d.ontology}) "
            "WHERE e.type <> 'category' AND v.bounds IS NOT NULL "
            "WITH d, e, v, [term IN [e.name] + coalesce(e.aliases, []) "
            "WHERE size(term) >= 3] AS terms "
            "WHERE any(term IN terms WHERE toLower(v.caption) CONTAINS toLower(term)) "
            "MERGE (e)-[:ILLUSTRATED_BY {document_id: d.id}]->(v)"
        )
        await self.driver.execute_query(
            "MATCH (e:Entity) OPTIONAL MATCH (e)-[:ILLUSTRATED_BY]->(v:Visual) "
            "WITH e, count(DISTINCT v) AS visual_count SET e.visual_count = visual_count"
        )

    async def queue_document(
        self,
        tenant: str,
        document_id: str,
        title: str,
        ontology: str,
        source_uri: str | None,
        task_id: str,
    ) -> None:
        await self.driver.execute_query(
            "MERGE (d:Document {tenant: $tenant, id: $document_id}) "
            "SET d.title = $title, d.ontology = $ontology, d.source_uri = $source_uri, "
            "d.task_id = $task_id, d.status = 'queued', d.error = null, "
            "d.queued_at = datetime(), d.updated_at = datetime()",
            tenant=tenant,
            document_id=document_id,
            title=title,
            ontology=ontology,
            source_uri=source_uri,
            task_id=task_id,
        )

    async def set_document_status(
        self,
        tenant: str,
        document_id: str,
        status: str,
        error: str | None = None,
        entities: int | None = None,
        relationships: int | None = None,
        vectors: int | None = None,
        visuals: int | None = None,
        extracted_uri: str | None = None,
        text_uri: str | None = None,
    ) -> None:
        await self.driver.execute_query(
            "MATCH (d:Document {tenant: $tenant, id: $document_id}) "
            "SET d.status = $status, d.error = $error, d.updated_at = datetime(), "
            "d.entity_count = coalesce($entities, d.entity_count), "
            "d.relationship_count = coalesce($relationships, d.relationship_count), "
            "d.vector_count = coalesce($vectors, d.vector_count), "
            "d.visual_count = coalesce($visuals, d.visual_count), "
            "d.extracted_uri = coalesce($extracted_uri, d.extracted_uri), "
            "d.text_uri = coalesce($text_uri, d.text_uri), "
            "d.completed_at = CASE WHEN $status = 'completed' "
            "THEN datetime() ELSE d.completed_at END",
            tenant=tenant,
            document_id=document_id,
            status=status,
            error=error,
            entities=entities,
            relationships=relationships,
            vectors=vectors,
            visuals=visuals,
            extracted_uri=extracted_uri,
            text_uri=text_uri,
        )

    async def documents(self, tenant: str, ontology: str | None = None) -> list[dict[str, Any]]:
        records, _, _ = await self.driver.execute_query(
            "MATCH (d:Document {tenant: $tenant}) "
            "WHERE $ontology IS NULL OR d.ontology = $ontology "
            "RETURN d {.*, queued_at: toString(d.queued_at), updated_at: toString(d.updated_at), "
            "completed_at: toString(d.completed_at)} AS document "
            "ORDER BY d.queued_at DESC",
            tenant=tenant,
            ontology=ontology,
        )
        return [dict(record["document"]) for record in records]

    async def document(self, tenant: str, document_id: str) -> dict[str, Any] | None:
        records, _, _ = await self.driver.execute_query(
            "MATCH (d:Document {tenant: $tenant, id: $document_id}) RETURN d {.*} AS document",
            tenant=tenant,
            document_id=document_id,
        )
        return dict(records[0]["document"]) if records else None

    async def persist_visual(
        self,
        tenant: str,
        document_id: str,
        page: int,
        image_uri: str,
        caption_uri: str,
        caption: str,
        bounds: list[float],
    ) -> None:
        await self.driver.execute_query(
            "MATCH (d:Document {tenant: $tenant, id: $document_id}) "
            "MERGE (v:Visual {tenant: $tenant, document_id: $document_id, page: $page}) "
            "SET v.image_uri = $image_uri, v.caption_uri = $caption_uri, "
            "v.caption = $caption, v.bounds = $bounds, v.updated_at = datetime() "
            "MERGE (d)-[:HAS_VISUAL]->(v)",
            tenant=tenant,
            document_id=document_id,
            page=page,
            image_uri=image_uri,
            caption_uri=caption_uri,
            caption=caption,
            bounds=bounds,
        )

    async def clear_visuals(self, tenant: str, document_id: str) -> None:
        await self.driver.execute_query(
            "MATCH (:Document {tenant: $tenant, id: $document_id})-[:HAS_VISUAL]->(v:Visual) "
            "DETACH DELETE v",
            tenant=tenant,
            document_id=document_id,
        )

    async def link_visuals(self, tenant: str, document_id: str, ontology: str) -> None:
        """Link each extracted picture only to entities named in its vision caption."""
        await self.driver.execute_query(
            "MATCH (d:Document {tenant: $tenant, id: $document_id})-[:HAS_VISUAL]->(v:Visual), "
            "(d)-[:MENTIONS]->(e:Entity {ontology: $ontology}) "
            "WITH e, v, [term IN [e.name] + coalesce(e.aliases, []) "
            "WHERE size(term) >= 3] AS terms "
            "WHERE any(term IN terms WHERE toLower(v.caption) CONTAINS toLower(term)) "
            "MERGE (e)-[:ILLUSTRATED_BY {document_id: $document_id}]->(v)",
            tenant=tenant,
            document_id=document_id,
            ontology=ontology,
        )
        await self.driver.execute_query(
            "MATCH (e:Entity {tenant: $tenant, ontology: $ontology}) "
            "OPTIONAL MATCH (e)-[:ILLUSTRATED_BY]->(v:Visual) "
            "WITH e, count(DISTINCT v) AS visual_count SET e.visual_count = visual_count",
            tenant=tenant,
            ontology=ontology,
        )

    async def visuals(self, tenant: str, document_id: str) -> list[dict[str, Any]]:
        records, _, _ = await self.driver.execute_query(
            "MATCH (:Document {tenant: $tenant, id: $document_id})-[:HAS_VISUAL]->(v:Visual) "
            "RETURN v {.*} AS visual ORDER BY v.page",
            tenant=tenant,
            document_id=document_id,
        )
        return [dict(record["visual"]) for record in records]

    async def metrics(self, tenant: str, ontology: str | None = None) -> dict[str, Any]:
        records, _, _ = await self.driver.execute_query(
            "CALL { MATCH (d:Document {tenant: $tenant}) "
            "WHERE $ontology IS NULL OR d.ontology = $ontology "
            "RETURN count(d) AS documents, "
            "count(CASE WHEN d.status = 'completed' THEN 1 END) AS completed, "
            "count(CASE WHEN d.status = 'processing' THEN 1 END) AS processing, "
            "count(CASE WHEN d.status = 'queued' THEN 1 END) AS queued, "
            "count(CASE WHEN d.status = 'failed' THEN 1 END) AS failed, "
            "sum(coalesce(d.vector_count, 0)) AS vectors, "
            "sum(coalesce(d.visual_count, 0)) AS visuals } "
            "CALL { MATCH (e:Entity {tenant: $tenant}) "
            "WHERE $ontology IS NULL OR e.ontology = $ontology "
            "RETURN count(e) AS entities } "
            "CALL { MATCH (a:Entity {tenant: $tenant})-[r:RELATES]->() "
            "WHERE $ontology IS NULL OR a.ontology = $ontology "
            "RETURN count(r) AS relationships } "
            "RETURN documents, completed, processing, queued, failed, vectors, "
            "visuals, entities, relationships",
            tenant=tenant,
            ontology=ontology,
        )
        return (
            dict(records[0])
            if records
            else {
                "documents": 0,
                "completed": 0,
                "processing": 0,
                "queued": 0,
                "failed": 0,
                "vectors": 0,
                "visuals": 0,
                "entities": 0,
                "relationships": 0,
            }
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
                "SET e.type = $type, e.description = $description, e.aliases = $aliases "
                "WITH e MATCH (d:Document {tenant: $tenant, id: $document_id}) "
                "MERGE (d)-[:MENTIONS]->(e) "
                "MERGE (category:Entity {tenant: $tenant, ontology: $ontology, "
                "name: 'Category · ' + $type}) "
                "SET category.type = 'category', "
                "category.description = 'Ontology category for ' + $type + ' entities' "
                "MERGE (d)-[:MENTIONS]->(category) "
                "MERGE (e)-[:RELATES {document_id: $document_id, "
                "type: 'classified_as'}]->(category)",
                tenant=tenant,
                document_id=document_id,
                ontology=ontology,
                **entity.model_dump(),
            )
            for alias in entity.aliases:
                await self._merge_alias(tenant, ontology, entity.name, alias)
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

    async def _merge_alias(
        self, tenant: str, ontology: str, canonical_name: str, alias: str
    ) -> None:
        """Rewire an existing alias node into its canonical entity."""
        if alias.casefold() == canonical_name.casefold():
            return
        parameters = {
            "tenant": tenant,
            "ontology": ontology,
            "canonical_name": canonical_name,
            "alias": alias,
        }
        await self.driver.execute_query(
            "MATCH (canonical:Entity {tenant: $tenant, ontology: $ontology, "
            "name: $canonical_name}) "
            "MATCH (alias:Entity {tenant: $tenant, ontology: $ontology, name: $alias}) "
            "MATCH (document:Document)-[:MENTIONS]->(alias) "
            "MERGE (document)-[:MENTIONS]->(canonical)",
            **parameters,
        )
        await self.driver.execute_query(
            "MATCH (canonical:Entity {tenant: $tenant, ontology: $ontology, "
            "name: $canonical_name}) "
            "MATCH (alias:Entity {tenant: $tenant, ontology: $ontology, name: $alias}) "
            "MATCH (alias)-[old:RELATES]->(target:Entity) "
            "WHERE target <> canonical "
            "MERGE (canonical)-[new:RELATES {document_id: old.document_id, "
            "type: old.type}]->(target) "
            "SET new.evidence = old.evidence",
            **parameters,
        )
        await self.driver.execute_query(
            "MATCH (canonical:Entity {tenant: $tenant, ontology: $ontology, "
            "name: $canonical_name}) "
            "MATCH (alias:Entity {tenant: $tenant, ontology: $ontology, name: $alias}) "
            "MATCH (source:Entity)-[old:RELATES]->(alias) "
            "WHERE source <> canonical "
            "MERGE (source)-[new:RELATES {document_id: old.document_id, "
            "type: old.type}]->(canonical) "
            "SET new.evidence = old.evidence",
            **parameters,
        )
        await self.driver.execute_query(
            "MATCH (alias:Entity {tenant: $tenant, ontology: $ontology, name: $alias}) "
            "DETACH DELETE alias",
            **parameters,
        )

    async def search(
        self,
        tenant: str,
        query: str,
        limit: int = 50,
        ontology: str = "core",
        document_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        records, _, _ = await self.driver.execute_query(
            "MATCH (e:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE toLower(e.name) CONTAINS toLower($query) "
            "AND (size($document_ids) = 0 OR EXISTS { "
            "MATCH (d:Document {tenant: $tenant})-[:MENTIONS]->(e) "
            "WHERE d.id IN $document_ids }) "
            "OPTIONAL MATCH (e)-[r:RELATES]-(other:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE size($document_ids) = 0 OR r.document_id IN $document_ids "
            "RETURN collect(DISTINCT e {.*, id: elementId(e)})[0..$limit] AS roots, "
            "collect(DISTINCT other {.*, id: elementId(other)})[0..$limit] AS linked, "
            "collect(DISTINCT {source: elementId(startNode(r)), target: elementId(endNode(r)), "
            "type: r.type, evidence: r.evidence})[0..$limit] AS edges",
            tenant=tenant,
            query=query,
            limit=min(limit, 200),
            ontology=ontology,
            document_ids=document_ids or [],
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
        self,
        tenant: str,
        limit: int = 200,
        ontology: str = "core",
        entity_type: str | None = None,
        document_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Return a bounded tenant graph for interactive visualization."""
        records, _, _ = await self.driver.execute_query(
            "MATCH (e:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE ($entity_type IS NULL OR e.type = $entity_type) "
            "AND (size($document_ids) = 0 OR EXISTS { "
            "MATCH (d:Document {tenant: $tenant})-[:MENTIONS]->(e) "
            "WHERE d.id IN $document_ids }) "
            "WITH e ORDER BY e.name LIMIT $limit "
            "WITH collect(e) AS selected "
            "UNWIND selected AS e "
            "OPTIONAL MATCH (e)-[r:RELATES]->(other:Entity {tenant: $tenant, ontology: $ontology}) "
            "WHERE other IN selected "
            "AND (size($document_ids) = 0 OR r.document_id IN $document_ids) "
            "RETURN collect(DISTINCT e {.*, id: elementId(e)}) AS nodes, "
            "collect(DISTINCT {source: elementId(startNode(r)), target: elementId(endNode(r)), "
            "type: r.type, evidence: r.evidence}) AS edges",
            tenant=tenant,
            ontology=ontology,
            entity_type=entity_type,
            document_ids=document_ids or [],
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

    async def visuals_for_entity(
        self, tenant: str, name: str, ontology: str = "core"
    ) -> list[dict[str, Any]]:
        """Return pictures whose vision captions explicitly identify the entity."""
        records, _, _ = await self.driver.execute_query(
            "MATCH (e:Entity {tenant: $tenant, ontology: $ontology, name: $name})"
            "-[:ILLUSTRATED_BY]->(v:Visual)<-[:HAS_VISUAL]-"
            "(d:Document {tenant: $tenant}) "
            "WHERE v.bounds IS NOT NULL "
            "RETURN v.page AS page, v.caption AS caption, v.image_uri AS image_uri, "
            "d.id AS document_id, d.title AS document_title "
            "ORDER BY v.page",
            tenant=tenant,
            name=name,
            ontology=ontology,
        )
        return [dict(record) for record in records if record["page"] is not None]
