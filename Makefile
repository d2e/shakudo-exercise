.DEFAULT_GOAL := help
SHELL := /bin/bash

# Charts live in charts/<name>. Pick one with CHART=<name> (default: app, e.g. `make deploy-dev CHART=api-service`).
# `make lint` and `make render-all` cover every chart. Release and namespace default to <chart>-<env>.
CHARTS := $(sort $(notdir $(patsubst %/,%,$(dir $(wildcard charts/*/Chart.yaml)))))
C := $(or $(CHART),app)

.PHONY: help lint render-dev render-staging render-prod render-all \
        build-image build-api-image load-api-image test-unit test-api-unit test-cli test-integration openapi test-dev test-staging test-prod test-e2e test-all \
        deploy-dev deploy-staging deploy-prod \
        status-dev status-staging status-prod \
        clean-dev clean-staging clean-prod

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build-image: ## Build the hardened OIDC & K8s pod listing container image
	@docker build -t shakudo-k8s-service:v1.0.0 .

build-api-image: ## Build the control-plane API image (api-service:1.0.0)
	@docker build -t api-service:1.0.0 control-plane/api-service

load-api-image: build-api-image ## Import the API image into k3s containerd (no registry; uses sudo)
	@docker save api-service:1.0.0 | sudo k3s ctr images import -

lint: ## Run Helm lint and validate all environment overlays (all charts, or CHART=<name>)
	@./scripts/lint.sh

render-dev: ## Render manifests for the dev environment
	@./scripts/render.sh dev

render-staging: ## Render manifests for the staging environment
	@./scripts/render.sh staging

render-prod: ## Render manifests for the production environment
	@./scripts/render.sh prod

render-all: ## Render manifests for all charts and environments
	@for c in $(CHARTS); do for env in dev staging prod; do echo "=== $$c / $$env ===" && CHART=$$c ./scripts/render.sh $$env || exit 1; done; done

deploy-dev: ## Deploy app to dev namespace on active cluster (k3s)
	@./scripts/deploy.sh dev

deploy-staging: ## Deploy app to staging namespace on active cluster
	@./scripts/deploy.sh staging

deploy-prod: ## Deploy app to prod namespace on active cluster
	@./scripts/deploy.sh prod

test-unit: ## Run pytest unit test suite for OIDC, JWT, and Kubernetes services
	@if [ -f ./.venv/bin/pytest ]; then ./.venv/bin/pytest -v; else pytest -v; fi

test-api-unit: ## Run control-plane API unit tests (fake cluster, no network)
	@cd control-plane/api-service && ../../.venv/bin/pytest -q

test-cli: ## Run the deploy CLI tests (fake API, no cluster)
	@cd control-plane/cli && ../../.venv/bin/pytest -q

test-integration: ## Run control-plane API integration tests against the local cluster (needs kubectl context)
	@cd control-plane/api-service && ../../.venv/bin/pytest -q -m integration -p no:cacheprovider test/integration

openapi: ## Regenerate control-plane/spec/openapi.yaml from the API code
	@./.venv/bin/python control-plane/scripts/export_openapi.py

test-dev: ## Run Helm tests against the deployed dev release
	@./scripts/test.sh dev

test-e2e: ## Run end-to-end OIDC, JWT, and RBAC pod listing tests against live cluster
	@if [ -f ./.venv/bin/python ]; then ./.venv/bin/python ./scripts/test_e2e.py; else python3 ./scripts/test_e2e.py; fi

test-staging: ## Run Helm tests against the deployed staging release
	@./scripts/test.sh staging

test-prod: ## Run Helm tests against the deployed prod release
	@./scripts/test.sh prod

test-all: test-unit test-dev test-e2e ## Run all unit, Helm integration, and live E2E tests

status-dev: ## Inspect deployed resources in app-dev namespace
	@kubectl -n $(C)-dev get all,pdb,ingress,networkpolicies -o wide

status-staging: ## Inspect deployed resources in app-staging namespace
	@kubectl -n $(C)-staging get all,pdb,ingress,networkpolicies -o wide

status-prod: ## Inspect deployed resources in app-prod namespace
	@kubectl -n $(C)-prod get all,pdb,ingress,networkpolicies -o wide

clean-dev: ## Uninstall the dev release and delete namespace
	@helm uninstall $(C)-dev --namespace $(C)-dev || true
	@kubectl delete namespace $(C)-dev --ignore-not-found

clean-staging: ## Uninstall the staging release and delete namespace
	@helm uninstall $(C)-staging --namespace $(C)-staging || true
	@kubectl delete namespace $(C)-staging --ignore-not-found

clean-prod: ## Uninstall the prod release and delete namespace
	@helm uninstall $(C)-prod --namespace $(C)-prod || true
	@kubectl delete namespace $(C)-prod --ignore-not-found
