#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CLUSTER_NAME=${CLUSTER_NAME:-agentic-platform}
CONTEXT="kind-$CLUSTER_NAME"
NAMESPACE=${NAMESPACE:-agentic-platform}
CUBE_OPERATOR_ROOT=${CUBE_OPERATOR_ROOT:-"$ROOT_DIR/../cube-microk8s-operator"}
OPERATOR_IMAGE=${OPERATOR_IMAGE:-cube-operator:v0.1.0}
RUNTIME_IMAGE=${RUNTIME_IMAGE:-agentic-platform-runtime:dev}
BUILD_RUNTIME_IMAGE=${BUILD_RUNTIME_IMAGE:-true}

for command in docker kind kubectl helm python3; do
  command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 1; }
done
[[ -f "$CUBE_OPERATOR_ROOT/config/default/kustomization.yaml" ]] || {
  echo "Cube operator not found at $CUBE_OPERATOR_ROOT; set CUBE_OPERATOR_ROOT." >&2
  exit 1
}
kubectl --context "$CONTEXT" get namespace "$NAMESPACE" >/dev/null

docker build -t "$OPERATOR_IMAGE" "$CUBE_OPERATOR_ROOT"
kind load docker-image "$OPERATOR_IMAGE" --name "$CLUSTER_NAME"
kubectl --context "$CONTEXT" apply -k "$CUBE_OPERATOR_ROOT/config/default"
kubectl --context "$CONTEXT" -n cube-system rollout restart deployment/cube-operator
kubectl --context "$CONTEXT" -n cube-system rollout status deployment/cube-operator --timeout=5m

secret_value() {
  kubectl --context "$CONTEXT" -n "$NAMESPACE" get secret "$1" \
    -o "go-template={{index .data \"$2\" | base64decode}}"
}
database=$(secret_value platform-postgresql-secret database)
username=$(secret_value platform-postgresql-secret username)
password=$(secret_value platform-postgresql-secret password)
api_secret=$(kubectl --context "$CONTEXT" -n "$NAMESPACE" get secret cube-configuration \
  -o 'go-template={{index .data "CUBEJS_API_SECRET" | base64decode}}' 2>/dev/null || true)
[[ -n "$api_secret" ]] || api_secret=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
kubectl --context "$CONTEXT" -n "$NAMESPACE" create secret generic cube-configuration \
  --from-literal=CUBEJS_DB_TYPE=postgres \
  --from-literal=CUBEJS_DB_HOST=postgresql \
  --from-literal=CUBEJS_DB_PORT=5432 \
  --from-literal=CUBEJS_DB_NAME="$database" \
  --from-literal=CUBEJS_DB_USER="$username" \
  --from-literal=CUBEJS_DB_PASS="$password" \
  --from-literal=CUBEJS_API_SECRET="$api_secret" \
  --dry-run=client -o yaml | kubectl --context "$CONTEXT" apply -f -

kubectl --context "$CONTEXT" apply -k "$ROOT_DIR/deploy/cube-analytics"
architecture=$(kubectl --context "$CONTEXT" get node -o jsonpath='{.items[0].status.nodeInfo.architecture}')
if [[ "$architecture" == arm64 ]]; then
  store_image='cubejs/cubestore:arm64v8@sha256:d9254a2166513e99f888da6f85362362357805116cd4d70f2b22e318e6ca5007'
else
  store_image='cubejs/cubestore:v1.7.20@sha256:cd5fe68049204640704a6412a39e7a09eb391fc70890577dd21b5480d85cb219'
fi
kubectl --context "$CONTEXT" -n "$NAMESPACE" patch cubecluster agentic-analytics \
  --type merge -p "{\"spec\":{\"cubeStore\":{\"image\":\"$store_image\"}}}"

if [[ "$BUILD_RUNTIME_IMAGE" == true ]]; then
  docker build -f "$ROOT_DIR/images/runtime/Dockerfile" -t "$RUNTIME_IMAGE" "$ROOT_DIR"
  kind load docker-image "$RUNTIME_IMAGE" --name "$CLUSTER_NAME"
fi
helm upgrade --install platform "$ROOT_DIR/deploy/helm/agentic-platform" \
  --kube-context "$CONTEXT" -n "$NAMESPACE" \
  -f "$ROOT_DIR/deploy/helm/agentic-platform/values-kind.yaml" \
  -f "$ROOT_DIR/deploy/helm/agentic-platform/values-cube-kind.yaml" --wait --timeout 15m
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout restart deployment/platform-agentic-platform-analytics
kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout status deployment/platform-agentic-platform-analytics --timeout=5m
kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=condition=Ready cubecluster/agentic-analytics --timeout=15m

echo "Cube analytics is ready. Run scripts/verify-cube-analytics.sh."
