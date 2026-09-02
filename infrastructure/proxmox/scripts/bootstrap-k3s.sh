#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
: "${SSH_PRIVATE_KEY:?Set SSH_PRIVATE_KEY}"
: "${METALLB_ADDRESS_POOL:?Set METALLB_ADDRESS_POOL, for example 192.168.20.200-192.168.20.220}"
KUBECONFIG_PATH=${KUBECONFIG_PATH:-$ROOT/kubeconfig}

for command in terraform jq ssh scp helm kubectl; do command -v "$command" >/dev/null || { echo "Missing $command" >&2; exit 1; }; done
nodes=$(terraform -chdir="$ROOT" output -json node_addresses)
primary=$(terraform -chdir="$ROOT" output -json primary_node | jq -r .address)
username=$(terraform -chdir="$ROOT" output -raw ssh_username)
ssh_opts=(-i "$SSH_PRIVATE_KEY" -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
remote() { local host=$1; shift; ssh "${ssh_opts[@]}" "$username@$host" "$@"; }

while read -r address; do
  until remote "$address" true 2>/dev/null; do sleep 5; done
  remote "$address" "sudo cloud-init status --wait"
done < <(jq -r '.[]' <<<"$nodes")

remote "$primary" "curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='server --cluster-init --flannel-backend=none --disable-network-policy --disable=servicelb --disable=traefik' sh -"
token=$(remote "$primary" "sudo cat /var/lib/rancher/k3s/server/node-token")
while IFS=$'\t' read -r name address; do
  [[ "$address" == "$primary" ]] && continue
  remote "$address" "curl -sfL https://get.k3s.io | K3S_URL=https://$primary:6443 K3S_TOKEN='$token' INSTALL_K3S_EXEC='server --flannel-backend=none --disable-network-policy --disable=servicelb --disable=traefik' sh -"
done < <(jq -r 'to_entries[] | [.key,.value] | @tsv' <<<"$nodes")

remote "$primary" "sudo cat /etc/rancher/k3s/k3s.yaml" | sed "s/127.0.0.1/$primary/" >"$KUBECONFIG_PATH"
chmod 0600 "$KUBECONFIG_PATH"
export KUBECONFIG="$KUBECONFIG_PATH"

kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
helm repo add cilium https://helm.cilium.io >/dev/null
helm upgrade --install cilium cilium/cilium --version 1.20.0 -n kube-system \
  -f "$ROOT/../../deploy/cilium/values-common.yaml" -f "$ROOT/../../deploy/cilium/values-baremetal.yaml" \
  --set k8sServiceHost="$primary" --set k8sServicePort=6443
kubectl -n kube-system rollout status daemonset/cilium --timeout=10m

helm repo add metallb https://metallb.github.io/metallb >/dev/null
helm upgrade --install metallb metallb/metallb --version 0.15.2 -n metallb-system --create-namespace
kubectl -n metallb-system rollout status deployment/metallb-controller --timeout=5m
cat <<EOF | kubectl apply -f -
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata: {name: platform, namespace: metallb-system}
spec: {addresses: ["$METALLB_ADDRESS_POOL"]}
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata: {name: platform, namespace: metallb-system}
spec: {ipAddressPools: [platform]}
EOF

gpu_nodes=$(terraform -chdir="$ROOT" output -json gpu_nodes)
while IFS=$'\t' read -r name memory; do
  kubectl label node "$name" accelerator=nvidia gpu-memory-class="$memory" workload=knowledge-extraction --overwrite
  kubectl taint node "$name" nvidia.com/gpu=true:NoSchedule --overwrite
done < <(jq -r 'to_entries[] | [.key,.value] | @tsv' <<<"$gpu_nodes")

if [[ $(jq length <<<"$gpu_nodes") -gt 0 ]]; then
  helm repo add nvidia https://helm.ngc.nvidia.com/nvidia >/dev/null
  helm upgrade --install gpu-operator nvidia/gpu-operator --version v25.3.4 \
    -n gpu-operator --create-namespace
fi

echo "K3s, Cilium Gateway API, and MetalLB are ready. Kubeconfig: $KUBECONFIG_PATH"
