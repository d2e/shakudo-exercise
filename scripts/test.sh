#!/usr/bin/env bash
set -euo pipefail

ENV="${1:-dev}"
RELEASE_NAME="${2:-app-${ENV}}"
NAMESPACE="${3:-app-${ENV}}"

echo "=========================================================="
echo " Running Helm integration tests for ${RELEASE_NAME}"
echo "=========================================================="

helm test "${RELEASE_NAME}" --namespace "${NAMESPACE}" --logs
echo "✔ All tests passed for ${RELEASE_NAME}!"
