# Testing the API by hand (curl, with payloads)

Step by step, from a deployed release or a local run. For automated checks use `make test-api-unit` and
`make test-integration` instead (see [README.md](./README.md)). Every call below was run against the dev
deployment; the verify step compares what the API says with what Kubernetes actually holds.

The same flow with the `deploy` CLI, including the JSON payload each command sends, is in
[testing-the-cli.md](../../cli/doc/testing-the-cli.md).

## 0. Prerequisites

- The release is deployed: `make load-api-image && make deploy-dev CHART=api-service`, and the `demo` namespace
  exists (it is the dev allowlist: `ALLOWED_NAMESPACES`). Any other namespace gets 403.
- `kubectl`, `curl`, `python3`. Images in payloads must be **pinned** (no `:latest`) and run as **non-root**
  (the API forces the PSS restricted profile). `hashicorp/http-echo:1.0.0` is a good test image.

## 1. Point at the API

Pick one.

**A. Deployed, through the Ingress** (Traefik on k3s answers on the node's port 80):

```bash
API=http://127.0.0.1
HOST='Host: api.dev.local'          # add -H "$HOST" to every curl below
```

**B. Deployed, through a port-forward** (no host header needed):

```bash
kubectl -n api-service-dev port-forward svc/api-service-dev 8080:80 &
API=http://127.0.0.1:8080; HOST='X-Unused: 1'
```

**C. Run locally** (uses your kubeconfig; from `control-plane/api-service/src`):

```bash
printf 'local-secret' > /tmp/cp-bootstrap-local
ENVIRONMENT=dev HOST=127.0.0.1 PORT=8000 BOOTSTRAP_SECRET_FILE=/tmp/cp-bootstrap-local ALLOWED_NAMESPACES=demo \
  ../../../.venv/bin/python -m api_service.main
API=http://127.0.0.1:8000; HOST='X-Unused: 1'    # secret to use in step 3: local-secret
```

## 2. Probes and the auth gate

```bash
curl -s -H "$HOST" $API/healthz        # {"status":"alive"}
curl -s -H "$HOST" $API/readyz         # {"status":"ready"}  (503 if the cluster is unreachable)
curl -si -H "$HOST" $API/api/v1/deployments | head -3   # 401, WWW-Authenticate: Bearer
```

## 3. Get a token (5 minutes; mint again when it expires)

The bootstrap secret is read from the cluster into a private file and sent as a header file, so it never
appears in your shell history or `ps`.

```bash
umask 077
printf 'X-Bootstrap-Secret: %s' "$(kubectl -n api-service-dev get secret api-service-dev-secrets \
  -o jsonpath='{.data.bootstrap-secret}' | base64 -d)" > /tmp/cp-bootstrap-header    # option C: use 'local-secret'

mint() {
  curl -s -X POST -H "$HOST" -H @/tmp/cp-bootstrap-header -H 'content-type: application/json' \
    -d '{"sub":"tester"}' $API/api/v1/auth/token \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'
}
TOKEN=$(mint); A="Authorization: Bearer $TOKEN"; J='content-type: application/json'
```

A wrong or missing secret gives 401. An expired token gives 401 `token expired`: run `TOKEN=$(mint); A=...` again.

## 4. Create a deployment (`POST /api/v1/deployments`)

Required: `name`, `namespace`, `image`, `resources` (all four values). Defaults: `replicas` 1, empty `env` and
`labels`. A full payload:

```bash
curl -s -w '\nHTTP %{http_code}\n' -X POST -H "$HOST" -H "$A" -H "$J" $API/api/v1/deployments -d '{
  "name": "http-echo",
  "namespace": "demo",
  "image": "hashicorp/http-echo:1.0.0",
  "replicas": 2,
  "port": 5678,
  "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
  "env": [{"name": "LOG_LEVEL", "value": "info"}],
  "labels": {"team": "platform"},
  "command": ["/http-echo"],
  "args": ["-text=hello", "-listen=:5678"]
}'
```

Expect `201` with `uid`, `health` `PROGRESSING`, and `"namespaceCreated": false` (`demo` already existed).
Save the id: `UID_=<uid from the response>`.

## 5. Read it back

```bash
curl -s -H "$HOST" -H "$A" "$API/api/v1/deployments?namespace=demo"      # list, optional ?namespace=
curl -s -H "$HOST" -H "$A" $API/api/v1/deployments/$UID_                  # detail: counts, conditions, pods
```

Within seconds the detail shows `"health": "HEALTHY"`, `replicas.ready` 2, and one entry per pod with
`phase`, `node`, `restartCount`, `terminating` and `containerStatuses` (`state`, `reason`).

## 6. Change it

```bash
curl -s -X PATCH -H "$HOST" -H "$A" -H "$J" $API/api/v1/deployments/$UID_ -d '{"replicas": 3}'         # scale up
curl -s -X PATCH -H "$HOST" -H "$A" -H "$J" $API/api/v1/deployments/$UID_ -d '{"replicas": 0}'         # scale down
curl -s -X PATCH -H "$HOST" -H "$A" -H "$J" $API/api/v1/deployments/$UID_ -d '{"image":"hashicorp/http-echo:0.2.3"}'
curl -s -X POST  -H "$HOST" -H "$A" $API/api/v1/deployments/$UID_/restart                               # 202
# put it back to a running state: step 8 needs pods to exist
curl -s -X PATCH -H "$HOST" -H "$A" -H "$J" $API/api/v1/deployments/$UID_ -d '{"image":"hashicorp/http-echo:1.0.0","replicas":2}'
```

`PATCH` takes `replicas` and/or `image` (at least one); anything else is 400.

## 7. Verify against the real cluster (the validation gate)

The API's answer is only trusted if Kubernetes agrees. Read the cluster directly, not through the API:

```bash
kubectl -n demo get deploy http-echo -o jsonpath='uid={.metadata.uid} replicas={.spec.replicas} image={.spec.template.spec.containers[0].image}{"\n"}'
kubectl -n demo get deploy http-echo -o jsonpath='{.spec.template.metadata.annotations}{"\n"}'   # restartedAt after step 6
kubectl -n demo get pods                                                                         # same pods as the API detail
```

Check: the `uid` equals the API's `uid`; `replicas` and `image` equal what you last patched; after a restart the
`kubectl.kubernetes.io/restartedAt` annotation is present and the pod names changed.

## 8. Failure cases and what to expect

| Send | Expect |
| :--- | :--- |
| `"image": "nginx:latest"` or `"nginx"` (no tag) | 400 `invalid_request`, `details[].field` = `image` |
| `"name": "Bad_Name"`, `"replicas": -1`, or a missing `resources` | 400, every problem listed at once |
| an unknown field, e.g. `"replica": 3` | 400 `extra_forbidden` |
| `"namespace": "other"` (not on the allowlist) | 403 `namespace_not_allowed`, nothing created |
| the same `name` and `namespace` twice | 409 `name already in use` |
| `GET /api/v1/deployments/abc` | 400 (malformed id) |
| `GET /api/v1/deployments/11111111-1111-4111-8111-111111111111` | 404 |
| a deployment not created by the API (no managed label) | 404 on every verb, never modified |
| no `Authorization` header | 401 |
| `env` with `{"name":"P","secretRef":{"name":"nope","key":"k"}}` | 400 `secret_not_allowed` (same answer for missing and unlabeled) |
| more than 30 writes or 300 reads a minute | 429 with `Retry-After` (covered by unit tests, not run here) |
| `/api/deployments` (no `/v1`) with a token | 404 |

A secret reference works only for a Secret in the target namespace that carries
`control-plane.io/allow-reference=true`: `kubectl -n demo label secret <name> control-plane.io/allow-reference=true`.

**The bad-image scenario** (the assessment's check). Start from a healthy deployment with replicas above 0
(wait until step 5 shows `HEALTHY` and `ready` 2), then patch to a tag that does not exist:

```bash
curl -s -X PATCH -H "$HOST" -H "$A" -H "$J" $API/api/v1/deployments/$UID_ -d '{"image":"hashicorp/http-echo:0.0.0-nonexistent"}'
curl -s -H "$HOST" -H "$A" $API/api/v1/deployments/$UID_ | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["health"],[(p["phase"],c["state"],c["reason"]) for p in d["pods"] if not p["terminating"] for c in p["containerStatuses"]])'
```

After a few seconds: `FAILING`, one pod `('Pending', 'waiting', 'ErrImagePull')` (later `ImagePullBackOff`), and the two
old pods still `('Running', 'running', None)`. With zero replicas there is nothing to fail, so the deployment would
stay `HEALTHY`. Roll back by patching the previous image.

## 9. Delete and clean up

```bash
curl -s -w ' HTTP %{http_code}\n' -X DELETE -H "$HOST" -H "$A" $API/api/v1/deployments/$UID_    # 202
kubectl -n demo get deploy,rs,pods                                  # empty once the pods have exited (up to 30 s)
curl -s -o /dev/null -w '%{http_code}\n' -H "$HOST" -H "$A" $API/api/v1/deployments/$UID_      # 404
rm -f /tmp/cp-bootstrap-header /tmp/cp-bootstrap-local; kill %1 2>/dev/null   # port-forward / local server, if used
```

`demo` stays: the API only removes namespaces it created, and under the chart's RBAC it cannot check that they are
empty (decision A in [req-api.md](../../spec/req-api.md)). The interactive API docs are at `$API/docs` in dev only.
