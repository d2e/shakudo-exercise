.DEFAULT_GOAL := help
SHELL := /bin/bash

CHART_DIR := charts/app
ENV_DIR := environments

.PHONY: help lint render-dev render-staging render-prod deploy-dev deploy-staging deploy-prod test-dev status-dev clean-dev

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

lint: ## Run Helm lint and validate all environment overlays
	@./scripts/lint.sh

render-dev: ## Render manifests for the dev environment
	@./scripts/render.sh dev

render-staging: ## Render manifests for the staging environment
	@./scripts/render.sh staging

render-prod: ## Render manifests for the production environment
	@./scripts/render.sh prod

deploy-dev: ## Deploy app to dev namespace on active cluster (k3s)
	@./scripts/deploy.sh dev app-dev app-dev

deploy-staging: ## Deploy app to staging namespace on active cluster
	@./scripts/deploy.sh staging app-staging app-staging

deploy-prod: ## Deploy app to prod namespace on active cluster
	@./scripts/deploy.sh prod app-prod app-prod

test-dev: ## Run Helm tests against the deployed dev release
	@./scripts/test.sh dev app-dev app-dev

status-dev: ## Inspect deployed pods, services, and endpoints in app-dev
	@kubectl -n app-dev get all,pdb,ingress,networkpolicies -o wide

clean-dev: ## Uninstall the dev release and delete namespace
	@helm uninstall app-dev --namespace app-dev || true
	@kubectl delete namespace app-dev --ignore-not-found
