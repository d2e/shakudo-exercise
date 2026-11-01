#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
RELEASE_NAME="${2:-app-${ENV}}"
NAMESPACE="${3:-app-${ENV}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHART_DIR="${ROOT_DIR}/charts/app"
ENV_DIR="${ROOT_DIR}/environments"

if [[ ! -d "${ENV_DIR}/${ENV}" ]]; then
  echo "Error: Unknown environment '${ENV}'" >&2
  exit 1
fi

EXTRA_VALUES=()
if [[ -f "${ENV_DIR}/${ENV}/secrets.yaml" ]]; then
  EXTRA_VALUES+=(-f "${ENV_DIR}/${ENV}/secrets.yaml")
fi

echo "=========================================================="
echo " Deploying ${RELEASE_NAME} -> namespace: ${NAMESPACE}"
echo " Environment: ${ENV}"
echo "=========================================================="

helm upgrade "${RELEASE_NAME}" "${CHART_DIR}" \
  --install \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  -f "${ENV_DIR}/base.yaml" \
  -f "${ENV_DIR}/${ENV}/values.yaml" \
  ${EXTRA_VALUES[@]+"${EXTRA_VALUES[@]}"} \
  --wait \
  --timeout 300s \
  --atomic \
  --cleanup-on-fail

echo "✔ Release '${RELEASE_NAME}' successfully deployed to namespace '${NAMESPACE}'."
