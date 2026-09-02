# Knowledge graph agent

This showcase deliberately separates cheap serving from expensive extraction:

- `knowledge-api` stays available for authenticated uploads, graph queries, MCP
  tools, and the 2D/3D explorer.
- `knowledge-worker` scales from zero on `tasks.knowledge`, extracts PDF/text,
  asks an OpenAI-compatible model for a validated graph, writes Neo4j, and
  publishes `results.knowledge`.

The split keeps GPU inference and large-document processing off the request
path. See `examples/knowledge` for the JWST Cycle 5 walkthrough.
