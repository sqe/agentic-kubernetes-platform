.PHONY: install test lint image qwen3-vl-training-image helm terraform kind-up kind-down kind-status check

install:
	python3 -m venv .venv
	.venv/bin/pip install -e '.[test]'

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check src agents services tests images

image:
	docker build -f images/runtime/Dockerfile -t agentic-platform-runtime:dev .

qwen3-vl-training-image:
	docker build -f images/qwen3-vl-training/Dockerfile -t agentic-platform-qwen3-vl-training:dev .

helm:
	helm lint deploy/helm/agentic-platform
	helm template platform deploy/helm/agentic-platform >/dev/null
	helm template platform deploy/helm/agentic-platform -f deploy/helm/agentic-platform/values-baremetal.yaml >/dev/null

terraform:
	terraform fmt -check -recursive infrastructure
	for d in infrastructure/aws infrastructure/proxmox infrastructure/keycloak infrastructure/kind; do terraform -chdir=$$d init -backend=false && terraform -chdir=$$d validate; done

kind-up:
	terraform -chdir=infrastructure/kind init
	terraform -chdir=infrastructure/kind apply

kind-down:
	terraform -chdir=infrastructure/kind destroy

kind-status:
	kubectl --context kind-agentic-platform get pods,gateway,httproute -A

check: lint test helm terraform
