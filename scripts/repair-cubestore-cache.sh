#!/usr/bin/env bash
set -Eeuo pipefail

CLUSTER_NAME=${CLUSTER_NAME:-agentic-platform}
CONTEXT="kind-$CLUSTER_NAME"
NAMESPACE=${NAMESPACE:-agentic-platform}
INSTANCE=${INSTANCE:-agentic-analytics}
PVC=${PVC:-agentic-cubestore-data}

if [[ ${RESET_CUBESTORE_CACHE:-false} != true ]]; then
  echo "This removes only Cube Store's rebuildable cache. Re-run with RESET_CUBESTORE_CACHE=true." >&2
  exit 2
fi

restore_operator() {
  kubectl --context "$CONTEXT" -n cube-system scale deployment/cube-operator --replicas=1 >/dev/null
  kubectl --context "$CONTEXT" -n cube-system rollout status deployment/cube-operator --timeout=5m
}
trap restore_operator EXIT

kubectl --context "$CONTEXT" -n cube-system scale deployment/cube-operator --replicas=0
kubectl --context "$CONTEXT" -n cube-system wait --for=delete pod \
  -l app.kubernetes.io/name=cube-operator --timeout=5m
kubectl --context "$CONTEXT" -n "$NAMESPACE" scale deployment \
  "$INSTANCE-api" "$INSTANCE-refresh-worker" "$INSTANCE-cubestore" --replicas=0
kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=delete pod \
  -l "app.kubernetes.io/instance=$INSTANCE" --timeout=5m
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete pod cube-cache-repair --ignore-not-found --wait
kubectl --context "$CONTEXT" -n "$NAMESPACE" run cube-cache-repair \
  --image=busybox:1.38.0 --restart=Never --overrides="$(cat <<JSON
{"spec":{"containers":[{"name":"cube-cache-repair","image":"busybox:1.38.0","command":["sh","-c","rm -rf /cube/remote/cachestore-* /cube/remote/cachestore-current"],"volumeMounts":[{"name":"remote","mountPath":"/cube/remote"}]}],"volumes":[{"name":"remote","persistentVolumeClaim":{"claimName":"$PVC"}}]}}
JSON
)"
kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=jsonpath='{.status.phase}'=Succeeded \
  pod/cube-cache-repair --timeout=5m
kubectl --context "$CONTEXT" -n "$NAMESPACE" delete pod cube-cache-repair --wait
restore_operator
trap - EXIT
for deployment in "$INSTANCE-api" "$INSTANCE-refresh-worker" "$INSTANCE-cubestore"; do
  kubectl --context "$CONTEXT" -n "$NAMESPACE" rollout status \
    "deployment/$deployment" --timeout=15m
done
kubectl --context "$CONTEXT" -n "$NAMESPACE" wait --for=condition=Ready \
  "cubecluster/$INSTANCE" --timeout=15m
echo "Cube Store cache rebuilt; PostgreSQL source data and the PVC were preserved."
