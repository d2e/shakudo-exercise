#!/usr/bin/env bash
# Sourced by lint/render/deploy/test. Requires ROOT_DIR. CHART selects charts/<CHART> (default: app).
CHART="${CHART:-app}"
CHART_DIR="${ROOT_DIR}/charts/${CHART}"
ENV_DIR="${ROOT_DIR}/environments"

if [[ ! -f "${CHART_DIR}/Chart.yaml" ]]; then
  echo "Error: chart '${CHART}' not found in ${ROOT_DIR}/charts" >&2
  exit 1
fi

# The default chart keeps environments/<env>/values.yaml; other charts use environments/<env>/<chart>.yaml.
chart_values_file() { # env
  if [[ "${CHART}" == "app" ]]; then echo "${ENV_DIR}/$1/values.yaml"; else echo "${ENV_DIR}/$1/${CHART}.yaml"; fi
}
chart_secrets_file() { # env
  if [[ "${CHART}" == "app" ]]; then echo "${ENV_DIR}/$1/secrets.yaml"; else echo "${ENV_DIR}/$1/${CHART}.secrets.yaml"; fi
}
