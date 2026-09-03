#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CLUSTER_NAME=${CLUSTER_NAME:-agentic-platform}
CONTEXT="kind-$CLUSTER_NAME"
CHART_VERSION=${CHART_VERSION:-88.6.3}

for command in helm kubectl; do
  command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 1; }
done

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version "$CHART_VERSION" --kube-context "$CONTEXT" -n monitoring --create-namespace \
  -f "$ROOT_DIR/deploy/monitoring/values-monitoring.yaml" --wait --timeout 15m
kubectl --context "$CONTEXT" apply -f "$ROOT_DIR/deploy/monitoring/redpanda-metrics.yaml"
kubectl --context "$CONTEXT" apply -f "$ROOT_DIR/deploy/monitoring/dashboard-agentic-platform.yaml"
kubectl --context "$CONTEXT" apply -f "$ROOT_DIR/deploy/monitoring/httproute-grafana.yaml"
kubectl --context "$CONTEXT" -n monitoring rollout status deployment/monitoring-operator --timeout=5m
kubectl --context "$CONTEXT" -n monitoring rollout status deployment/monitoring-grafana --timeout=5m
kubectl --context "$CONTEXT" -n monitoring rollout status statefulset/prometheus-monitoring-prometheus --timeout=5m

echo "Grafana: http://127.0.0.1:8080/grafana/"
echo "Admin password: kubectl --context $CONTEXT -n monitoring get secret monitoring-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo"
