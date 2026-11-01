#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CHART_DIR="${ROOT_DIR}/charts/app"
ENV_DIR="${ROOT_DIR}/environments"

echo "=== [1/2] Linting Chart with default values (strict mode) ==="
helm lint "${CHART_DIR}" --strict

for env in dev staging prod; do
  echo "=== [2/2] Validating environment overlay: ${env} ==="
  helm template test-lint "${CHART_DIR}" \
    -f "${ENV_DIR}/base.yaml" \
    -f "${ENV_DIR}/${env}/values.yaml" > /dev/null
  echo "✔ ${env} overlay renders cleanly"
done

echo "=== All Helm chart and environment configs are valid! ==="
