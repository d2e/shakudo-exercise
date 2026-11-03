#!/usr/bin/env bash
# Set up the `deploy` CLI once; afterwards run `deploy ...` from any terminal.
#
#   ./cli.sh            set up: launcher on PATH, port-forward to the deployed API, login
#   ./cli.sh status     show what is set up
#   ./cli.sh stop       stop the port-forward (the launcher and login stay)
#   ./cli.sh uninstall  stop, and remove the launcher, saved server and login
#
# Settings (environment): DEPLOY_K8S_NAMESPACE (api-service-dev), DEPLOY_K8S_SERVICE (api-service-dev),
# DEPLOY_LOCAL_PORT (18080), DEPLOY_USER (login name, default $USER), DEPLOY_BIN_DIR (~/.local/bin).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
LAUNCHER="$ROOT/control-plane/cli/bin/deploy"
NS="${DEPLOY_K8S_NAMESPACE:-api-service-dev}"
SVC="${DEPLOY_K8S_SERVICE:-api-service-dev}"
PORT="${DEPLOY_LOCAL_PORT:-18080}"
BIN_DIR="${DEPLOY_BIN_DIR:-$HOME/.local/bin}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}/deploy"
URL="http://127.0.0.1:$PORT"
PID_FILE="$CONF/port-forward.pid"

say()  { echo "cli.sh: $*"; }
fail() { echo "cli.sh: $*" >&2; exit 1; }
api_up() { curl -s -o /dev/null --max-time 2 "$URL/healthz"; }
pf_pid() { [[ -s "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null && cat "$PID_FILE"; }

stop_forward() {
  local pid
  if pid="$(pf_pid)"; then kill "$pid" && say "port-forward stopped (pid $pid)"; else say "no port-forward running"; fi
  rm -f "$PID_FILE"
}

status() {
  echo "launcher:     $(command -v deploy || echo "not on PATH ($BIN_DIR/deploy)")"
  echo "server:       $(cat "$CONF/server" 2>/dev/null || echo 'not saved (default http://localhost:8000)')"
  echo "port-forward: $(pf_pid >/dev/null && echo "running, pid $(pf_pid)" || echo 'not running')"
  echo "API:          $(api_up && echo "answering on $URL" || echo "not answering on $URL")"
  echo "login:        $([[ -s "$CONF/credentials" ]] && echo stored || echo 'none (run ./cli.sh)')"
}

setup() {
  for tool in kubectl curl base64; do command -v "$tool" >/dev/null || fail "$tool is required"; done
  [[ -x "$LAUNCHER" ]] || fail "launcher missing: $LAUNCHER"
  "$LAUNCHER" --version >/dev/null || fail "the CLI does not start (check $ROOT/.venv or set DEPLOY_PYTHON)"

  # 1. launcher on PATH
  mkdir -p "$BIN_DIR"
  ln -sfn "$LAUNCHER" "$BIN_DIR/deploy"
  say "launcher: $BIN_DIR/deploy -> $LAUNCHER"
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "WARNING: $BIN_DIR is not on PATH; add it to your shell profile" ;; esac

  # 2. port-forward to the deployed API (kept running in the background)
  mkdir -p -m 700 "$CONF"
  if api_up; then
    say "API already answering on $URL"
  else
    local stale; stale="$(pf_pid || true)"
    if [[ -n "$stale" ]]; then kill "$stale" 2>/dev/null; say "stopped a stale port-forward (pid $stale; the API pod was probably replaced)"; fi
    kubectl -n "$NS" get svc "$SVC" >/dev/null 2>&1 || fail "service $SVC not found in namespace $NS (is the API deployed? make deploy-dev CHART=api-service)"
    setsid nohup kubectl -n "$NS" port-forward "svc/$SVC" "$PORT:80" >"$CONF/port-forward.log" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    for _ in $(seq 1 15); do api_up && break; sleep 1; done
    api_up || { stop_forward; fail "the API did not answer on $URL (see $CONF/port-forward.log)"; }
    say "port-forward started (pid $(<"$PID_FILE")) on $URL"
  fi
  umask 077; echo "$URL" > "$CONF/server"

  # 3. login with the dev bootstrap secret, read from the cluster into a private temp file
  local secret; secret="$(mktemp)"
  trap 'rm -f "$secret"' RETURN
  kubectl -n "$NS" get secret "${SVC}-secrets" -o jsonpath='{.data.bootstrap-secret}' | base64 -d > "$secret"
  [[ -s "$secret" ]] || fail "could not read the bootstrap secret ${SVC}-secrets in $NS"
  "$LAUNCHER" login --user "${DEPLOY_USER:-${USER:-cli-user}}" --bootstrap-secret-file "$secret"

  echo
  say "done. Try:  deploy list    deploy --help    (./cli.sh status | stop)"
  say "the login lasts about an hour; run ./cli.sh again to renew it or to restart the port-forward"
}

case "${1:-setup}" in
  setup) setup ;;
  status) status ;;
  stop) stop_forward ;;
  uninstall)
    stop_forward
    [[ "$(readlink -f "$BIN_DIR/deploy" 2>/dev/null)" == "$LAUNCHER" ]] && rm -f "$BIN_DIR/deploy" && say "launcher removed"
    rm -f "$CONF/server" "$CONF/credentials" "$CONF/port-forward.log" && say "saved server and login removed" ;;
  -h|--help|help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "unknown command '$1' (setup, status, stop, uninstall)" ;;
esac
