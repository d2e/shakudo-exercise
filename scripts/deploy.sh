#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=scripts/_common.sh
source "${SCRIPT_DIR}/_common.sh"

RELEASE_NAME="${2:-${CHART}-${ENV}}"
NAMESPACE="${3:-${CHART}-${ENV}}"

if [[ ! -d "${ENV_DIR}/${ENV}" ]]; then
  echo "Error: Unknown environment '${ENV}'" >&2
  exit 1
fi

EXTRA_VALUES=()
if [[ -f "$(chart_secrets_file "${ENV}")" ]]; then
  EXTRA_VALUES+=(-f "$(chart_secrets_file "${ENV}")")
fi

echo "=========================================================="
echo " Deploying ${RELEASE_NAME} (chart: ${CHART}) -> namespace: ${NAMESPACE}"
echo " Environment: ${ENV}"
echo "=========================================================="

helm upgrade "${RELEASE_NAME}" "${CHART_DIR}" \
  --install \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  -f "${ENV_DIR}/base.yaml" \
  -f "$(chart_values_file "${ENV}")" \
  ${EXTRA_VALUES[@]+"${EXTRA_VALUES[@]}"} \
  --wait \
  --timeout 300s \
  --atomic \
  --cleanup-on-fail

echo "✔ Release '${RELEASE_NAME}' successfully deployed to namespace '${NAMESPACE}'."
