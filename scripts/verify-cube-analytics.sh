#!/usr/bin/env bash
set -Eeuo pipefail

CLUSTER_NAME=${CLUSTER_NAME:-agentic-platform}
CONTEXT="kind-$CLUSTER_NAME"
NAMESPACE=${NAMESPACE:-agentic-platform}
BASE_URL=${BASE_URL:-http://127.0.0.1:8080}

for command in kubectl curl jq; do
  command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 1; }
done

kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=condition=Ready \
  cubecluster/agentic-analytics --timeout=5m
kubectl --context "$CONTEXT" -n "$NAMESPACE" get cubecluster agentic-analytics
kubectl --context "$CONTEXT" -n "$NAMESPACE" get pods \
  -l app.kubernetes.io/instance=agentic-analytics

for deployment in agentic-analytics-api agentic-analytics-refresh-worker; do
  if kubectl --context "$CONTEXT" -n "$NAMESPACE" logs \
    "deployment/$deployment" --since=5m | grep -q 'Corruption: CURRENT file'; then
    echo "Cube Store cache corruption detected in $deployment; run RESET_CUBESTORE_CACHE=true ./scripts/repair-cubestore-cache.sh" >&2
    exit 1
  fi
done

thread=$(curl --fail --silent --show-error "$BASE_URL/v1/threads" \
  -H 'Content-Type: application/json' \
  --data '{"title":"Cube analytics end-to-end verification"}')
thread_id=$(jq -er '.id' <<<"$thread")
accepted=$(curl --fail --silent --show-error \
  "$BASE_URL/v1/threads/$thread_id/messages" \
  -H 'Content-Type: application/json' \
  --data '{"prompt":"Summarize agent usage for the last 30 days","skill":"analytics.usage","params":{"days":30}}')
task_id=$(jq -er 'select(.status == "accepted") | .task_id' <<<"$accepted")

for _ in {1..60}; do
  thread=$(curl --fail --silent --show-error "$BASE_URL/v1/threads/$thread_id")
  result=$(jq -c --arg id "$task_id" '.messages[] | select(.id == $id)' <<<"$thread")
  status=$(jq -r '.status // "missing"' <<<"$result")
  [[ "$status" == pending || "$status" == missing ]] || break
  sleep 2
done

jq -e '
  .status == "complete" and
  .skill == "analytics.usage" and
  (.payload.result.rows | type == "array") and
  (.payload.result.query.measures == ["AgentMessages.count"]) and
  (.payload.result.query.filters[] | select(.member == "AgentMessages.owner"))
' <<<"$result" >/dev/null
echo "Analytics task $task_id completed through Cilium, Kafka, Cube Core, and PostgreSQL."
