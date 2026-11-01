.DEFAULT_GOAL := help
SHELL := /bin/bash

.PHONY: help lint render-dev render-staging render-prod render-all \
        deploy-dev deploy-staging deploy-prod \
        test-dev test-staging test-prod \
        status-dev status-staging status-prod \
        clean-dev clean-staging clean-prod

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

render-all: ## Render manifests for all environments
	@for env in dev staging prod; do echo "=== $$env ===" && ./scripts/render.sh $$env; done

deploy-dev: ## Deploy app to dev namespace on active cluster (k3s)
	@./scripts/deploy.sh dev app-dev app-dev

deploy-staging: ## Deploy app to staging namespace on active cluster
	@./scripts/deploy.sh staging app-staging app-staging

deploy-prod: ## Deploy app to prod namespace on active cluster
	@./scripts/deploy.sh prod app-prod app-prod

test-dev: ## Run Helm tests against the deployed dev release
	@./scripts/test.sh dev app-dev app-dev

test-staging: ## Run Helm tests against the deployed staging release
	@./scripts/test.sh staging app-staging app-staging

test-prod: ## Run Helm tests against the deployed prod release
	@./scripts/test.sh prod app-prod app-prod

status-dev: ## Inspect deployed resources in app-dev namespace
	@kubectl -n app-dev get all,pdb,ingress,networkpolicies -o wide

status-staging: ## Inspect deployed resources in app-staging namespace
	@kubectl -n app-staging get all,pdb,ingress,networkpolicies -o wide

status-prod: ## Inspect deployed resources in app-prod namespace
	@kubectl -n app-prod get all,pdb,ingress,networkpolicies -o wide

clean-dev: ## Uninstall the dev release and delete namespace
	@helm uninstall app-dev --namespace app-dev || true
	@kubectl delete namespace app-dev --ignore-not-found

clean-staging: ## Uninstall the staging release and delete namespace
	@helm uninstall app-staging --namespace app-staging || true
	@kubectl delete namespace app-staging --ignore-not-found

clean-prod: ## Uninstall the prod release and delete namespace
	@helm uninstall app-prod --namespace app-prod || true
	@kubectl delete namespace app-prod --ignore-not-found
