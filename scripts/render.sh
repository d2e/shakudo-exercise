#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "${SCRIPT_DIR}/_common.sh"

if [[ ! -d "${ENV_DIR}/${ENV}" ]]; then
  echo "Error: Environment '${ENV}' not found in ${ENV_DIR}" >&2
  echo "Available environments: dev, staging, prod" >&2
  exit 1
fi

echo "# Rendered manifests for chart ${CHART}, environment: ${ENV}"
helm template "${CHART}-${ENV}" "${CHART_DIR}" \
  --namespace "${CHART}-${ENV}" \
  -f "${ENV_DIR}/base.yaml" \
  -f "$(chart_values_file "${ENV}")"
