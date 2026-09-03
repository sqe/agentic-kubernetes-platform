"""Versioned graph ontologies used to constrain extraction and power the UI."""

from pydantic import BaseModel, Field

from .models import Entity, ExtractedGraph, Relationship


class EntityType(BaseModel):
    id: str
    label: str
    description: str
    color: str = "#5b8cff"


class RelationshipType(BaseModel):
    id: str
    label: str
    description: str
    source_types: list[str] = Field(default_factory=list)
    target_types: list[str] = Field(default_factory=list)


class Ontology(BaseModel):
    id: str
    version: str
    description: str
    entity_types: list[EntityType]
    relationship_types: list[RelationshipType]

    def extraction_instructions(self) -> str:
        entities = ", ".join(f"{item.id} ({item.description})" for item in self.entity_types)
        relationships = ", ".join(
            f"{item.id} ({item.description})" for item in self.relationship_types
        )
        return (
            f"Ontology {self.id}@{self.version}. Entity types: {entities}. "
            f"Relationships: {relationships}."
        )

    def normalize(self, graph: ExtractedGraph) -> ExtractedGraph:
        entity_types = {item.id for item in self.entity_types}
        relationship_types = {item.id: item for item in self.relationship_types}
        entities = [
            Entity(
                name=item.name,
                type=item.type if item.type in entity_types else "concept",
                description=item.description,
                aliases=item.aliases,
            )
            for item in graph.entities
        ]
        entities_by_term = {
            term.casefold(): item for item in entities for term in [item.name, *item.aliases]
        }
        relationships: list[Relationship] = []
        seen: set[tuple[str, str, str]] = set()
        for edge in graph.relationships:
            definition = relationship_types.get(edge.type)
            source = entities_by_term.get(edge.source.casefold())
            target = entities_by_term.get(edge.target.casefold())
            if not source or not target or source.name == target.name:
                continue
            valid = definition is not None
            if (
                definition
                and definition.source_types
                and source.type not in definition.source_types
            ):
                valid = False
            if (
                definition
                and definition.target_types
                and target.type not in definition.target_types
            ):
                valid = False
            edge_type = edge.type if valid else "related_to"
            key = (source.name.casefold(), target.name.casefold(), edge_type)
            if key not in seen:
                relationships.append(
                    Relationship(
                        source=source.name,
                        target=target.name,
                        type=edge_type,
                        evidence=edge.evidence,
                    )
                )
                seen.add(key)
        return ExtractedGraph(entities=entities, relationships=relationships)


CORE = Ontology(
    id="core",
    version="1.0.0",
    description="General-purpose document knowledge graph.",
    entity_types=[
        EntityType(id="person", label="Person", description="A named human", color="#f59e0b"),
        EntityType(
            id="organization",
            label="Organization",
            description="An institution or team",
            color="#10b981",
        ),
        EntityType(
            id="place", label="Place", description="A physical or named location", color="#06b6d4"
        ),
        EntityType(id="event", label="Event", description="An occurrence in time", color="#ef4444"),
        EntityType(id="concept", label="Concept", description="A general idea or subject"),
        EntityType(
            id="document", label="Document", description="A referenced publication", color="#a78bfa"
        ),
    ],
    relationship_types=[
        RelationshipType(
            id="related_to", label="Related to", description="A supported general relation"
        ),
        RelationshipType(id="part_of", label="Part of", description="Membership or composition"),
        RelationshipType(id="created_by", label="Created by", description="Authorship or creation"),
        RelationshipType(id="located_in", label="Located in", description="Physical location"),
        RelationshipType(
            id="documented_in", label="Documented in", description="Document provenance"
        ),
    ],
)

ASTRONOMY = Ontology(
    id="astronomy",
    version="1.0.0",
    description="Observatories, missions, instruments, operations, and scientific measurements.",
    entity_types=[
        *CORE.entity_types,
        EntityType(
            id="observatory",
            label="Observatory",
            description="A space or ground observatory",
            color="#38bdf8",
        ),
        EntityType(
            id="instrument",
            label="Instrument",
            description="A scientific observing instrument",
            color="#f472b6",
        ),
        EntityType(
            id="subsystem",
            label="Subsystem",
            description="An observatory hardware or software subsystem",
            color="#fb7185",
        ),
        EntityType(
            id="mission", label="Mission", description="A scientific space mission", color="#818cf8"
        ),
        EntityType(
            id="target",
            label="Target",
            description="An astronomical observing target",
            color="#facc15",
        ),
        EntityType(
            id="measurement",
            label="Measurement",
            description="A measured physical quantity",
            color="#4ade80",
        ),
        EntityType(
            id="constraint",
            label="Constraint",
            description="An operational or observing constraint",
            color="#f97316",
        ),
        EntityType(
            id="process",
            label="Process",
            description="An operational or scientific procedure",
            color="#2dd4bf",
        ),
        EntityType(
            id="catalog",
            label="Catalog",
            description="A scientific target or reference catalog",
            color="#c084fc",
        ),
    ],
    relationship_types=[
        *CORE.relationship_types,
        RelationshipType(
            id="contains",
            label="Contains",
            description="Physical or logical containment",
            source_types=["observatory", "instrument", "subsystem"],
            target_types=["instrument", "subsystem"],
        ),
        RelationshipType(
            id="operated_by", label="Operated by", description="Operational responsibility"
        ),
        RelationshipType(
            id="observes",
            label="Observes",
            description="Observatory or instrument observes a target",
        ),
        RelationshipType(
            id="measures", label="Measures", description="Instrument produces a measurement"
        ),
        RelationshipType(
            id="supports",
            label="Supports",
            description="A subsystem or process supports another entity",
        ),
        RelationshipType(
            id="depends_on", label="Depends on", description="Operational or technical dependency"
        ),
        RelationshipType(
            id="constrained_by",
            label="Constrained by",
            description="An activity is limited by a constraint",
        ),
        RelationshipType(
            id="uses_catalog", label="Uses catalog", description="A process references a catalog"
        ),
    ],
)

INDUSTRY = Ontology(
    id="industry",
    version="1.0.0",
    description=(
        "Cross-industry assets, operations, risks, projects, logistics, and models for "
        "healthcare, manufacturing, education, finance, energy, robotics, and construction."
    ),
    entity_types=[
        EntityType(
            id="asset",
            label="Asset",
            description="Equipment, device, vehicle, robot, or instrument",
            color="#e74c3c",
        ),
        EntityType(
            id="component", label="Component", description="A sub-part of an asset", color="#e67e22"
        ),
        EntityType(
            id="location",
            label="Location",
            description="A site, building, room, zone, warehouse, or route",
            color="#2ecc71",
        ),
        EntityType(
            id="person",
            label="Person",
            description="Personnel, patient, student, or operator",
            color="#3498db",
        ),
        EntityType(
            id="organization",
            label="Organization",
            description="A company, department, team, or supplier",
            color="#9b59b6",
        ),
        EntityType(
            id="document",
            label="Document",
            description="A manual, report, policy, contract, or curriculum",
            color="#1abc9c",
        ),
        EntityType(
            id="procedure",
            label="Procedure",
            description="An SOP, workflow, protocol, or treatment plan",
            color="#f39c12",
        ),
        EntityType(
            id="event",
            label="Event",
            description="An incident, inspection, delivery, or transaction",
            color="#e91e63",
        ),
        EntityType(
            id="certification",
            label="Certification",
            description="A license, compliance record, or accreditation",
            color="#00bcd4",
        ),
        EntityType(
            id="material",
            label="Material",
            description="Raw material, inventory, supply, or medication",
            color="#795548",
        ),
        EntityType(
            id="metric",
            label="Metric",
            description="A KPI, sensor reading, vital, or financial indicator",
            color="#607d8b",
        ),
        EntityType(
            id="risk",
            label="Risk",
            description="A hazard, vulnerability, market risk, or safety flag",
            color="#f44336",
        ),
        EntityType(
            id="project",
            label="Project",
            description="A construction project, research study, or campaign",
            color="#4caf50",
        ),
        EntityType(
            id="route",
            label="Route",
            description="A logistics route, patient pathway, or data flow",
            color="#ff9800",
        ),
        EntityType(
            id="model",
            label="Model",
            description="An ML, CAD, financial, or simulation model",
            color="#673ab7",
        ),
    ],
    relationship_types=[
        RelationshipType(
            id="contains", label="Contains", description="Physical or logical containment"
        ),
        RelationshipType(
            id="part_of", label="Part of", description="Component or membership structure"
        ),
        RelationshipType(id="located_in", label="Located in", description="Physical location"),
        RelationshipType(
            id="operated_by", label="Operated by", description="Operational responsibility"
        ),
        RelationshipType(
            id="maintained_by", label="Maintained by", description="Maintenance responsibility"
        ),
        RelationshipType(
            id="monitored_by", label="Monitored by", description="Monitoring by a metric or model"
        ),
        RelationshipType(
            id="supplies", label="Supplies", description="Supplies material or a component"
        ),
        RelationshipType(
            id="consumes", label="Consumes", description="Consumes material or another resource"
        ),
        RelationshipType(
            id="documented_in", label="Documented in", description="Document provenance"
        ),
        RelationshipType(
            id="certified_for",
            label="Certified for",
            description="Certification for a procedure or asset",
        ),
        RelationshipType(
            id="complies_with",
            label="Complies with",
            description="Compliance with a certification or standard",
        ),
        RelationshipType(
            id="depends_on", label="Depends on", description="Operational or technical dependency"
        ),
        RelationshipType(id="caused_by", label="Caused by", description="Causal relation"),
        RelationshipType(id="mitigates", label="Mitigates", description="Reduces a risk"),
        RelationshipType(
            id="triggers", label="Triggers", description="Initiates a procedure or event"
        ),
        RelationshipType(id="precedes", label="Precedes", description="Temporal ordering"),
        RelationshipType(
            id="assigned_to", label="Assigned to", description="Assignment to a project or route"
        ),
        RelationshipType(
            id="produces", label="Produces", description="Produces material, output, or metric"
        ),
        RelationshipType(
            id="trained_on", label="Trained on", description="Model training provenance"
        ),
        RelationshipType(
            id="recommends", label="Recommends", description="Model or expert recommendation"
        ),
    ],
)

ONTOLOGIES = {item.id: item for item in (CORE, ASTRONOMY, INDUSTRY)}


def get_ontology(ontology_id: str) -> Ontology:
    try:
        return ONTOLOGIES[ontology_id]
    except KeyError as exc:
        raise ValueError(f"Unknown ontology: {ontology_id}") from exc
