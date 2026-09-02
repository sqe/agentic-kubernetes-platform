#!/usr/bin/env bash
set -euo pipefail

cluster_name=${1:?cluster name required}
cilium_version=${2:?Cilium version required}
gateway_api_version=${3:?Gateway API version required}
keda_version=${4:?KEDA version required}
keycloak_version=${5:?Keycloak Operator version required}
runtime_image=${6:?runtime image required}
build_image=${7:?build flag required}
repository_root=${8:?repository root required}
context="kind-$cluster_name"
namespace=agentic-platform

kubectl --context "$context" apply --server-side -f \
  "https://github.com/kubernetes-sigs/gateway-api/releases/download/v${gateway_api_version}/standard-install.yaml"
helm repo add cilium https://helm.cilium.io --force-update
helm upgrade --install cilium cilium/cilium --kube-context "$context" -n kube-system \
  --version "$cilium_version" -f "$repository_root/deploy/cilium/values-common.yaml" \
  --set k8sServiceHost="${cluster_name}-control-plane" --set k8sServicePort=6443 \
  --set gatewayAPI.hostNetwork.enabled=true \
  --set envoy.securityContext.capabilities.keepCapNetBindService=true \
  --set 'envoy.securityContext.capabilities.envoy[0]=NET_BIND_SERVICE' \
  --wait --timeout 10m

helm repo add kedacore https://kedacore.github.io/charts --force-update
helm upgrade --install keda kedacore/keda --kube-context "$context" -n keda \
  --create-namespace --version "$keda_version" --wait --timeout 10m

kubectl --context "$context" create namespace "$namespace" --dry-run=client -o yaml | \
  kubectl --context "$context" apply -f -
operator_manifest=$(mktemp)
trap 'rm -f "$operator_manifest"' EXIT
curl -fsSL \
  "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${keycloak_version}/kubernetes/kubernetes.yml" |
  sed 's/namespace: keycloak/namespace: agentic-platform/g' >"$operator_manifest"
kubectl --context "$context" apply -f \
  "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${keycloak_version}/kubernetes/keycloaks.k8s.keycloak.org-v1.yml"
kubectl --context "$context" apply -f \
  "https://raw.githubusercontent.com/keycloak/keycloak-k8s-resources/${keycloak_version}/kubernetes/keycloakrealmimports.k8s.keycloak.org-v1.yml"
kubectl --context "$context" -n "$namespace" apply -f "$operator_manifest"

kubectl --context "$context" create namespace messaging --dry-run=client -o yaml |
  kubectl --context "$context" apply -f -
kubectl --context "$context" -n messaging apply -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata: {name: kafka}
spec:
  replicas: 1
  selector: {matchLabels: {app: kafka}}
  template:
    metadata: {labels: {app: kafka}}
    spec:
      containers:
        - name: redpanda
          image: redpandadata/redpanda:v25.2.5
          args: [redpanda, start, --mode, dev-container, --smp, "1", --memory, 1G, --reserve-memory, 0M, --kafka-addr, internal://0.0.0.0:9092, --advertise-kafka-addr, internal://kafka.messaging.svc:9092]
          ports: [{name: kafka, containerPort: 9092}]
          resources: {requests: {cpu: 250m, memory: 1Gi}, limits: {cpu: "1", memory: 2Gi}}
---
apiVersion: v1
kind: Service
metadata: {name: kafka}
spec: {selector: {app: kafka}, ports: [{name: kafka, port: 9092, targetPort: kafka}]}
YAML

kubectl --context "$context" -n "$namespace" apply -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata: {name: rustfs}
spec:
  replicas: 1
  selector: {matchLabels: {app: rustfs}}
  template:
    metadata: {labels: {app: rustfs}}
    spec:
      containers:
        - name: rustfs
          image: rustfs/rustfs:v1.0.0
          args: [/data]
          env:
            - {name: RUSTFS_ACCESS_KEY, value: local-access-key}
            - {name: RUSTFS_SECRET_KEY, value: local-secret-key}
          ports: [{name: s3, containerPort: 9000}]
          volumeMounts: [{name: data, mountPath: /data}]
      volumes: [{name: data, emptyDir: {}}]
---
apiVersion: v1
kind: Service
metadata: {name: rustfs}
spec: {selector: {app: rustfs}, ports: [{name: s3, port: 9000, targetPort: s3}]}
YAML

kubectl --context "$context" -n "$namespace" create secret generic platform-runtime-secrets \
  --from-literal=POSTGRES_URL=postgresql://agents:local-password@postgresql:5432/agents \
  --from-literal=REDIS_URL=redis://default:local-password@redis:6379/0 \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic platform-postgresql-secret \
  --from-literal=database=agents --from-literal=username=agents --from-literal=password=local-password \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic platform-qdrant-secret \
  --from-literal=api-key=local-qdrant-key --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic platform-cache-secret \
  --from-literal='users.acl=user default on >local-password ~* &* +@all' \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic knowledge-secrets \
  --from-literal=neo4j-auth=neo4j/local-password --from-literal=neo4j-password=local-password \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic knowledge-runtime-secrets \
  --from-literal=AUTH_DISABLED=true --from-literal=AWS_ACCESS_KEY_ID=local-access-key \
  --from-literal=AWS_SECRET_ACCESS_KEY=local-secret-key \
  --from-literal=AWS_DEFAULT_REGION=us-east-1 \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic keycloak-secrets \
  --from-literal=database-username=keycloak --from-literal=database-password=local-password \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -
kubectl --context "$context" -n "$namespace" create secret generic keycloak-bootstrap-admin \
  --from-literal=username=admin --from-literal=password=local-admin-password \
  --dry-run=client -o yaml | kubectl --context "$context" apply -f -

if [[ "$build_image" == "true" ]]; then
  docker build -f "$repository_root/images/runtime/Dockerfile" -t "$runtime_image" "$repository_root"
fi
kind load docker-image "$runtime_image" --name "$cluster_name"
helm upgrade --install platform "$repository_root/deploy/helm/agentic-platform" \
  --kube-context "$context" -n "$namespace" \
  -f "$repository_root/deploy/helm/agentic-platform/values-kind.yaml" --wait --timeout 15m
kubectl --context "$context" -n "$namespace" wait --for=condition=Programmed \
  gateway/agentic-platform --timeout=5m
