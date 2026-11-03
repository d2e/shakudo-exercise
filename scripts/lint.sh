#!/usr/bin/env bash
# Lint chart(s) and render every environment overlay. CHART=<name> limits it to one chart; default: all charts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -n "${CHART:-}" ]]; then
  CHARTS=("${CHART}")
else
  CHARTS=()
  for f in "${ROOT_DIR}"/charts/*/Chart.yaml; do CHARTS+=("$(basename "$(dirname "${f}")")"); done
fi

for CHART in "${CHARTS[@]}"; do
  export CHART
  # shellcheck source=scripts/_common.sh
  source "${SCRIPT_DIR}/_common.sh"

  echo "=== [${CHART}] [1/2] Linting chart with default values (strict mode) ==="
  helm lint "${CHART_DIR}" --strict

  for env in dev staging prod; do
    echo "=== [${CHART}] [2/2] Validating environment overlay: ${env} ==="
    helm template test-lint "${CHART_DIR}" \
      -f "${ENV_DIR}/base.yaml" \
      -f "$(chart_values_file "${env}")" > /dev/null
    echo "✔ ${CHART}: ${env} overlay renders cleanly"
  done
done

echo "=== All Helm chart and environment configs are valid! ==="
