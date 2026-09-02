# JWST knowledge-graph showcase

The showcase uses **JWST Observatory — User Documentation for Cycle 5**, a
139-page guide published by the Space Telescope Science Institute. The source
document credits NASA, ESA, CSA, STScI, and AURA and identifies its text as
CC BY 4.0. It is not duplicated in this repository; point the script at a
lawfully obtained copy.

The reference file used during development had SHA-256:
`d393b661d57e7f284a9478c17570fed8fbde2de636caab831f4e75f5abbae711`.

```bash
export JWST_PDF="$HOME/Downloads/JWST Observatory.pdf"
export API_URL=http://localhost:8200
export JWT_TOKEN=... # omit only when AUTH_DISABLED=true in local development
./examples/knowledge/jwst-ingest.sh
```

The API streams the PDF to the configured S3-compatible document bucket. The
background worker extracts its text, chunks it, calls the configured
OpenAI-compatible model, validates the structured graph, and upserts it into
Neo4j using the `astronomy@1.0.0` ontology. Search for `JWST`, `NIRCam`, or
`Fine Guidance Sensor`, then trace paths
such as `NIRCam` → `Wavefront Sensing and Control` in the web explorer.
