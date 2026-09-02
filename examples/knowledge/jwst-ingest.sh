#!/usr/bin/env bash
set -Eeuo pipefail

API_URL=${API_URL:-http://localhost:8200}
JWST_PDF=${JWST_PDF:-$HOME/Downloads/JWST Observatory.pdf}
AUTH_HEADER=()
if [[ -n "${JWT_TOKEN:-}" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer $JWT_TOKEN")
fi

if [[ ! -f "$JWST_PDF" ]]; then
  echo "Set JWST_PDF to the Cycle 5 observatory PDF." >&2
  exit 1
fi

curl --fail-with-body "${AUTH_HEADER[@]}" \
  -F 'title=JWST Observatory — User Documentation for Cycle 5' \
  -F 'ontology=astronomy' \
  -F "file=@${JWST_PDF};type=application/pdf" \
  "$API_URL/v1/knowledge/documents/upload"
