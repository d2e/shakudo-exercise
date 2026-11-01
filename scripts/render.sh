#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHART_DIR="${ROOT_DIR}/charts/app"
ENV_DIR="${ROOT_DIR}/environments"

if [[ ! -d "${ENV_DIR}/${ENV}" ]]; then
  echo "Error: Environment '${ENV}' not found in ${ENV_DIR}" >&2
  echo "Available environments: dev, staging, prod" >&2
  exit 1
fi

echo "# Rendered manifests for environment: ${ENV}"
helm template "app-${ENV}" "${CHART_DIR}" \
  --namespace "app-${ENV}" \
  -f "${ENV_DIR}/base.yaml" \
  -f "${ENV_DIR}/${ENV}/values.yaml"
