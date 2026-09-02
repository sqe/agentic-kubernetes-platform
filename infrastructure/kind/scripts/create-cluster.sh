#!/usr/bin/env bash
set -euo pipefail

cluster_name=${1:?cluster name required}
config_path=${2:?kind config required}
for command in docker kind kubectl helm; do
  command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 1; }
done
docker info >/dev/null
if ! kind get clusters | grep -Fxq "$cluster_name"; then
  kind create cluster --config "$config_path"
fi
kubectl --context "kind-$cluster_name" cluster-info
