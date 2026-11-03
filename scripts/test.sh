#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
CHART="${CHART:-app}"
RELEASE_NAME="${2:-${CHART}-${ENV}}"
NAMESPACE="${3:-${CHART}-${ENV}}"

echo "=========================================================="
echo " Running Helm integration tests for ${RELEASE_NAME}"
echo "=========================================================="

helm test "${RELEASE_NAME}" --namespace "${NAMESPACE}" --logs
echo "✔ All tests passed for ${RELEASE_NAME}!"
