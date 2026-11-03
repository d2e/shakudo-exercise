# Testing the CLI by hand (commands and payloads)

Companion to [testing-the-api.md](../../api-service/doc/testing-the-api.md), which does the same with curl. Here each
`deploy` command is shown with the HTTP request it sends and the JSON payload. Spec: [req-cli.md](../../spec/req-cli.md).
Automated checks, no cluster needed: `cd control-plane/cli && ../../.venv/bin/pytest -q`.

## 0. Setup (once)

From the repository root:

```bash
./cli.sh
```

It links a `deploy` launcher into `~/.local/bin` (runs the CLI from source, nothing is installed into the venv), starts a
background port-forward to the deployed API on `127.0.0.1:18080`, saves that address in `~/.config/deploy/server`, and
logs you in with the dev bootstrap secret read from the cluster. Then `deploy ...` works in any terminal and directory.

```bash
deploy --help                 # commands; deploy <command> --help for flags
./cli.sh status               # launcher, server, port-forward, API reachable, login
./cli.sh stop                 # stop the port-forward
./cli.sh uninstall            # remove launcher, saved server and login
```

Re-run `./cli.sh` when the login expires (about an hour; commands then exit 3) or the port-forward stops (the API pod
restarted; commands exit 1 with a connection error). It is safe to run repeatedly. Other releases:
`DEPLOY_K8S_NAMESPACE=... DEPLOY_K8S_SERVICE=... DEPLOY_LOCAL_PORT=... ./cli.sh`.

Prerequisite: the API is deployed (`make load-api-image && make deploy-dev CHART=api-service`) and the `demo` namespace,
the dev allowlist, exists. Any other namespace gets 403.

Exit codes: `0` ok, `1` the API or network failed the request, `2` bad flags or an action the CLI refused, `3`
authentication needed or failed.

### Manual setup instead (what `./cli.sh` does, by hand)

```bash
cd control-plane/cli
export PATH="$PWD/bin:$PATH"                     # bin/deploy runs the CLI from source
export XDG_CONFIG_HOME=$(mktemp -d)              # optional: keep test credentials out of ~/.config/deploy
kubectl -n api-service-dev port-forward svc/api-service-dev 18081:80 &    # 18081: 18080 may be taken by ./cli.sh
export DEPLOY_SERVER=http://127.0.0.1:18081      # plain http is allowed to 127.0.0.1 only
```

Then log in as in step 2.

### Which API the CLI talks to

`--server`, else `DEPLOY_SERVER`, else the address saved by `./cli.sh`, else `http://localhost:8000`.

| Target | Server value | Notes |
| :--- | :--- | :--- |
| Deployed API via port-forward | `http://127.0.0.1:18080` | What `./cli.sh` sets up. Verified. |
| API run locally | `http://localhost:8000` | Start it as in [testing-the-api.md](../../api-service/doc/testing-the-api.md) option C. |
| Deployed API via Ingress | `http://api.dev.local --insecure` | Needs `127.0.0.1 api.dev.local` in `/etc/hosts` (the CLI cannot set a Host header). Not verified. |
| A real environment | `https://<host>` | Plain `http` to a non-local host is refused unless `--insecure`. `--ca-file` for a private CA. |

## 1. Preview a payload without sending anything

`create_body` turns flags into the request body. Printing it needs no server and no login:

```bash
cd control-plane/cli && PYTHONPATH=src python3 - <<'EOF'
import json
from deploy_cli.main import build_parser, glue_values
from deploy_cli.commands import create_body
from deploy_cli.context import Context

argv = ["create", "--name", "web", "--namespace", "demo", "--image", "nginx:1.27.0",
        "--replicas", "2", "--env", "LOG_LEVEL=info", "--label", "team=platform"]
print(json.dumps(create_body(build_parser(Context.system()).parse_args(glue_values(argv))), indent=2))
EOF
```

Change `argv` to try any flag combination from step 3.

## 2. Login (`POST /api/v1/auth/token`)

`./cli.sh` already did this. By hand: the bootstrap secret goes in a private file and is sent as the
`X-Bootstrap-Secret` header, never as an argument.

```bash
boot=$(umask 077; mktemp)
kubectl -n api-service-dev get secret api-service-dev-secrets -o jsonpath='{.data.bootstrap-secret}' | base64 -d > "$boot"
deploy login --user cli-tester --bootstrap-secret-file "$boot"; rm -f "$boot"
```

Request body: `{"sub": "cli-tester"}` (default user: your login name). Expect `Logged in as cli-tester on
http://127.0.0.1:18080. The access token is valid for 300 s.` Tokens are stored in `~/.config/deploy/credentials`
(directory 0700, file 0600) and refreshed automatically (`POST /api/v1/auth/refresh`, body `{"refresh_token": "…"}`)
until the refresh token expires after an hour. Logged out, any command exits 3 with `not logged in`; a wrong secret exits 3.

## 3. Create (`POST /api/v1/deployments`)

```bash
RES="--cpu-request 25m --memory-request 32Mi --cpu-limit 100m --memory-limit 64Mi"    # used below
```

**A. All flags**

```bash
deploy create --name http-echo --namespace demo --image hashicorp/http-echo:1.0.0 --replicas 2 --port 5678 $RES \
  --env LOG_LEVEL=info --env MODE=demo --label team=platform \
  --command /http-echo --arg=-text=hello --arg=-listen=:5678 --wait
```

```json
{
  "name": "http-echo", "image": "hashicorp/http-echo:1.0.0", "namespace": "demo",
  "replicas": 2, "port": 5678,
  "env": [{"name": "LOG_LEVEL", "value": "info"}, {"name": "MODE", "value": "demo"}],
  "labels": {"team": "platform"},
  "command": ["/http-echo"],
  "args": ["-text=hello", "-listen=:5678"],
  "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}}
}
```

Expect `Waiting for HEALTHY...`, `PROGRESSING (0/2 ready)`, `HEALTHY (2/2 ready)`, then `Created deployment http-echo in
demo` with the UID, exit 0. Add `-o json` for the API's response instead of the summary.

**B. Required flags only:** `deploy create --name web --namespace demo --image nginx:1.27.0` sends
`{"name": "web", "image": "nginx:1.27.0", "namespace": "demo"}`. The deployed API rejects it (see Known gaps).

**C. A secret from an existing Kubernetes Secret:** `--secret-env DB_PASSWORD=db-creds/password` adds

```json
"env": [{"name": "DB_PASSWORD", "secretRef": {"name": "db-creds", "key": "password"}}]
```

Accepted only if `db-creds` exists in that namespace and carries `control-plane.io/allow-reference=true`.

**D. Zero replicas:** `--replicas 0` sends `"replicas": 0`.

Checked before anything is sent (exit 2; the login check comes first, so logged out you get exit 3): the four resource
flags are all-or-none; `--env` and `--label` need `KEY=VALUE`; `--secret-env` needs `NAME=SECRET/KEY`. A value
starting with a dash works as `--arg=-text=x` or `--arg -text=x`.

## 4. Read, change, delete

```bash
deploy list -n demo                                    # GET /api/v1/deployments?namespace=demo, no body
UID_=$(deploy list -n demo -o json | python3 -c 'import sys,json;print(next(d["uid"] for d in json.load(sys.stdin)["items"] if d["name"]=="http-echo"))')
deploy status  ${UID_:0:8}                             # GET /api/v1/deployments/{uid}
deploy scale   ${UID_:0:8} --replicas 3 --wait         # PATCH /api/v1/deployments/{uid}   {"replicas": 3}
deploy restart ${UID_:0:8} --wait                      # POST  /api/v1/deployments/{uid}/restart, no body
deploy delete  $UID_ -y --wait                         # GET (to show the name), then DELETE /api/v1/deployments/{uid}
```

- `status`, `scale` and `restart` accept a UID prefix of 8+ characters. The CLI first calls `GET /api/v1/deployments` and
  matches it; an ambiguous prefix lists the candidates and exits 2. `delete` needs the **full** UID.
- `scale --replicas 0` and `delete` ask for confirmation. With no terminal they refuse (exit 2, nothing sent) unless `-y`.
- `--wait` polls until `HEALTHY` (exit 1 on `FAILING` or timeout, `--wait-timeout`, default 120 s); for `delete`, until gone.
- There is no image-update command. Use `PATCH {"image": "…"}` with curl (see the API testing guide).

## 5. Run a deployment end to end (the assessment scenario)

A real workload, then a deliberately bad image; read the result from the exit code. Images must be pinned (`:latest` is
refused) and run as non-root (root images cannot start).

```bash
RES="--cpu-request 50m --memory-request 64Mi --cpu-limit 200m --memory-limit 128Mi"

deploy create --name web --namespace demo --image nginxinc/nginx-unprivileged:1.27.0 --replicas 3 --port 8080 $RES --wait
echo "exit=$?"     # 0: PROGRESSING (0/3 ready) ... HEALTHY (3/3 ready)

deploy create --name web-bad --namespace demo --image nginx:nonexistent --replicas 1 --port 80 $RES --wait --wait-timeout 60
echo "exit=$?"     # 1: FAILING (0/1 ready)
```

For the bad image expect `Error: web-bad is FAILING: pod web-bad-…, container web-bad: ErrImagePull (…)`; the pod and the
reason come from the cluster. Then `deploy list -n demo` (web `3/3 HEALTHY`, web-bad `0/1`) and `deploy status <web-bad
prefix>` (`Health: FAILING`, the pod waiting with `ErrImagePull`). Because there is no image-update command, the bad image
is created fresh; for the rollout case (old pods keep running while the new one fails) patch with curl.

## 6. Confirm against the real cluster

The CLI's output is only trusted if Kubernetes agrees. Read the cluster directly:

```bash
kubectl -n demo get deploy http-echo -o jsonpath='uid={.metadata.uid} replicas={.spec.replicas} {.spec.template.metadata.annotations}{"\n"}'
kubectl -n demo get pods
```

The `uid` equals the CLI's; after `scale --replicas 3` the cluster shows `replicas=3`; after `restart` the
`kubectl.kubernetes.io/restartedAt` annotation is present and the pod names changed; after `delete --wait`,
`kubectl -n demo get deploy,rs,pods` no longer lists it.

## 7. Error cases

```bash
deploy create --name Bad_Name --namespace demo --image nginx:latest $RES   # exit 1: name and image problems both listed
deploy create --name x --namespace other --image nginx:1.27.0 $RES         # exit 1: namespace is not allowed
deploy status 11111111                                                     # exit 1: no deployment has a UID starting with 11111111
deploy --server http://api.dev.local list                                  # exit 2: plain http refused (--insecure overrides)
deploy delete <full-uid> </dev/null                                        # exit 2: no terminal to confirm on, nothing sent
deploy create --name a --namespace demo --image nginx:1.27.0 --cpu-request 25m   # exit 2: all four resource flags or none
```

Server errors print the API's message and its `request id`, never a traceback.

## 8. Other ways to give a token, and the traffic

```bash
deploy --token-file ./tokfile list -n demo             # used as given, never refreshed
cat ./tokfile | deploy --token-stdin list -n demo      # then confirmations need -y
DEPLOY_TOKEN=$(cat ./tokfile) deploy list -n demo -o json
deploy --debug list                                    # debug: GET /api/v1/deployments -> 200
```

Token precedence: `--token-file`, `--token-stdin`, `DEPLOY_TOKEN`, stored login. Tokens are never printed. `--debug` shows
method, path and status only; for bodies use step 1 or the curl guide.

## 8b. Stream logs (`deploy logs`)

Needs API image `1.1.0` or later (log streaming, D28); with `1.0.0` the ticket request returns 404 `resource not found`.

```bash
deploy logs <id>                         # every pod of the deployment, merged, each line prefixed [pod]; Ctrl-C to stop
deploy logs <id> --tail 20               # history lines per pod to start with (default 100, max 1000)
deploy logs <id> --pod <pod-name>        # one pod, no prefix
deploy logs <id> --since 10m             # only newer lines (s, m, h, d)
```

Traffic: `POST /api/v1/deployments/{uid}/logs/ticket` with the Bearer token returns a short-lived ticket, then the CLI
opens the WebSocket `/api/v1/logs/stream` with only that ticket. Expect history first, then new lines as they are written
(for `http-echo`, each request it serves logs a line). A pod outside the deployment gives `Error: pod not found`. Streams
end when the token expires; with a stored login the CLI refreshes and reconnects without repeating lines.

## 9. Clean up

```bash
deploy delete <full-uid> -y --wait       # each deployment you created (steps 3 to 5 make up to three)
kubectl -n demo get deploy               # should be empty
./cli.sh stop                            # or, after the manual setup: kill %1; rm -rf "$XDG_CONFIG_HOME"
```

## Troubleshooting

| Symptom | Cause and fix |
| :--- | :--- |
| `bash: ../../.venv/bin/python: No such file or directory` | An old shell function named `deploy` is shadowing the launcher. `unset -f deploy; hash -r` (or open a new terminal). `type deploy` must show `~/.local/bin/deploy`. |
| `deploy: command not found` | `~/.local/bin` is not on PATH. Add it to your shell profile, or run `control-plane/cli/bin/deploy`. |
| exit 3, `not logged in` / `session has expired` | Run `./cli.sh` again. |
| exit 1 with a connection error | The port-forward stopped. `./cli.sh status`, then `./cli.sh`. |
| `refusing to send credentials over plain http` | The server is `http://` and not localhost. Use the port-forward, https, or `--insecure`. |

## Known gaps (as of this writing)

- **`create` without resource flags fails against the deployed API** (400, `resources: Field required`). The spec (D31) makes
  `resources` optional with server defaults and the CLI follows it, but the deployed API image still requires them. Pass all four flags.
- **After `--wait`** the printed summary shows the health at creation (`PROGRESSING`) although the wait line says `HEALTHY`.
- **`deploy list` lags on pod failures.** Right after creating a bad image, `status` and `--wait` said `FAILING` while `list`
  still said `PROGRESSING`; a few minutes later `list` showed `FAILING` too. The API's list computes health without reading
  pods, so it only sees the failure once the 60 s progress deadline passes. Trust `status`. Spec S4 expects the same health in both.
