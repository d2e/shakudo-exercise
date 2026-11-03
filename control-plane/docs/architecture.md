# Control plane architecture

How a `deploy` command reaches the cluster, end to end, as built. Requirements are in [`../spec/req.md`](../spec/req.md);
this page shows the shape. Decision numbers (D#) and rule IDs point into the spec.

## 1. Components

```
  OPERATOR'S MACHINE                         CLUSTER (k3s / Kubernetes)
 +-----------------------------+        +----------------------------------------------------------------------+
 | deploy CLI  (control-plane/cli)       |                                                                      |
 |                             |        |   +-----------------+                                                |
 |  argparse -> commands       |  HTTPS |   | Traefik Ingress |  sticky cookie "deploy_affinity" (D29)         |
 |   login create list status  |  /WSS  |   |  (TLS, WS upgrade) --> same API replica per client             |
 |   scale restart delete logs |------->|   +--------+--------+                                                |
 |                             |        |            |                                                         |
 |  auth: token sources        |        |   +--------v----------------------------------------------------+   |
 |   file > stdin > env > store|        |   | API pods  (FastAPI, uvicorn, N replicas, PSS restricted)    |   |
 |  ~/.config/deploy/          |        |   |                                                             |   |
 |   credentials (0600)        |        |   |  HTTP pipeline (security.py), runs on every HTTP request:   |   |
 |   cookies     (0600)        |        |   |   request id -> rate limit -> Bearer auth -> body limit     |   |
 |  http client: no redirects, |        |   |   -> audit log -> security headers                          |   |
 |   plain http only localhost |        |   |                                                             |   |
 +-----------------------------+        |   |  Controllers                  WebSocket route               |   |
                                        |   |   /auth/token, /auth/refresh    /api/v1/logs/stream         |   |
                                        |   |   /deployments (CRUD,restart)   (NOT in the pipeline:       |   |
                                        |   |   /deployments/{id}/logs/ticket  one ticket-check function) |   |
                                        |   |   /healthz /readyz                                          |   |
                                        |   |            |                         |                      |   |
                                        |   |  Services  v                         v                      |   |
                                        |   |   DeploymentService            LogService                   |   |
                                        |   |   (validate -> allowlist       (ticket, R21 limit,          |   |
                                        |   |    -> cluster)                  live re-check, pumping)     |   |
                                        |   |            |                         |                      |   |
                                        |   |   TokenService                       |                      |   |
                                        |   |    dev: mock identity (signs JWTs)   |                      |   |
                                        |   |    else: validates IdP tokens (JWKS) |                      |   |
                                        |   |    always: signs log tickets  <- Secret "signing-key"       |   |
                                        |   |            |                         |                      |   |
                                        |   |  ClusterPort (interface, D27) -------+                      |   |
                                        |   |            |                                                |   |
                                        |   |  KubernetesAdapter  (only code that imports the K8s SDK)    |   |
                                        |   +------------+------------------------------------------------+   |
                                        |                | ServiceAccount token, in-cluster                   |
                                        |   +------------v-----------+       RBAC (chart templates)           |
                                        |   | Kubernetes API server  |<--- ClusterRole: namespaces, deployments|
                                        |   +------------+-----------+      replicasets, pods, pods/log        |
                                        |                |                 Role per allowed namespace:         |
                                        |   +------------v-----------+      secrets get, list kinds            |
                                        |   | allowed namespaces (D21)|                                       |
                                        |   |  Deployments -> RS -> Pods   (managed-by=control-plane only, D2) |
                                        |   +-------------------------+                                       |
                                        +----------------------------------------------------------------------+

 Packaging: charts/api-service + environments/{base,dev,staging,prod}   Secrets: ESO / Vault / SOPS (never in git)
```

## 2. An ordinary command (`deploy scale <id> --replicas 3`)

```
CLI                              Traefik          API pod                              K8s API
 | resolve token (refresh if     |                 |                                      |
 |  <= 60 s left)                |                 |                                      |
 | PATCH /api/v1/deployments/{uid}                 |                                      |
 |   Authorization: Bearer <JWT> |                 |                                      |
 |   Cookie: deploy_affinity     |                 |                                      |
 |------------------------------>|---------------->| 1 authenticate            (401 if bad)|
 |                               |                 | 2 rate limit, body size   (429/413)  |
 |                               |                 | 3 validate input          (400)      |
 |                               |                 | 4 allowlist check         (403)      |
 |                               |                 | 5 find deployment by UID, managed    |
 |                               |                 |   only                    (404)      |
 |                               |                 |--- ClusterPort.patch ---->| apply  |
 |                               |                 |<--------------------------|        |
 |<------------------------------|<----------------| 200 {uid,name,replicas,health,...}   |
 | print table / JSON            |                 |   (every error: fixed message + request_id)
```

## 3. Log streaming (D28): authenticate over HTTP, then switch to WebSocket

```
CLI                                   API pod (replica A)                         K8s API
 | 1 POST /deployments/{id}/logs/ticket   |                                          |
 |   Bearer <access JWT>, {pod?, tail}    |                                          |
 |--------------------------------------->| normal pipeline: auth, limits            |
 |                                        | allowlist, deployment + pod ownership    |
 |                                        | R21: fewer than 3 open streams?          |
 |                                        | sign ticket (30 s, bound to user,        |
 |<---------------------------------------|  deployment, pod; stream_until = JWT exp)|
 |   {ticket, streamPath, expiresIn:30}   |                                          |
 |                                        |                                          |
 | 2 GET wss://.../api/v1/logs/stream     |                                          |
 |   Upgrade: websocket                   |                                          |
 |   Sec-WebSocket-Protocol: logs.v1, ticket.<t>   (ticket never in the URL)         |
 |--------------------------------------->| ONE check function: ticket valid, unused,|
 |                                        |  Origin ok, live re-check, stream slot   |
 |<---------------------------------------| 101 Switching Protocols (logs.v1)        |
 |                                        |                                          |
 |                                        |  one reader thread per pod  ------------>| pods/log
 |<== {"pod","time","line"} frames ========| bounded queue, merged                    |   (follow)
 |   prints "[pod] line"                  |                                          |
 |                                        |                                          |
 |   close 4001 at token expiry  <--------| stream_until reached                     |
 | 3 refresh stored login, new ticket with sinceTime = oldest per-pod position,      |
 |   reconnect, drop lines already shown (per pod)                                   |
 |   close 1000 = logs ended, 1001 = restarting, 1013 = too slow, 1011 = cluster error
```

## 4. Where each rule is enforced

| Concern | Enforced in | Spec |
| :--- | :--- | :--- |
| Authentication before anything else | HTTP pipeline (`security.py`); for the WebSocket, the one ticket check (`log_service.py`) | A1–A4, LG7 |
| Input validation, 400 before any cluster call | request models and `validation.py`, checked in `service.py` | V1–V25 |
| Namespace allowlist, managed-only scope | `service.py`, then the per-namespace Roles in the chart | D2, D21, R6–R8 |
| No caching of cluster data | every call goes through `KubernetesAdapter` | N1–N7 |
| Sanitised errors (no hostnames or RBAC text) | `KubernetesAdapter` reduces SDK errors to a `KubeFailure` kind | R24 |
| Stream limits, expiry, backpressure | `log_service.py`, `streaming.py` | R21, LG11, LG13 |
| Pod security (restricted, tmpfs `/tmp`, resources) | `builder.py` for workloads, the chart for the API itself | D8, AGENTS.md |
| Token never printed, plain http refused, no redirects | CLI `client.py`, `auth.py` | CL9–CL14 |
| Terminal-safe log text | CLI `logs.py` | CL30 |

## 5. Code map

| Part | Path |
| :--- | :--- |
| CLI | `control-plane/cli/src/deploy_cli/` (`main`, `commands`, `client`, `auth`, `logs`, `render`, `store`) |
| API app and wiring | `control-plane/api-service/src/api_service/app.py` (the one place the adapter is chosen) |
| HTTP controllers | `.../controllers/` (`deployments`, `logs`, `auth`, `probes`) |
| Use cases | `.../service.py` (deployments), `.../log_service.py` and `streaming.py` (logs) |
| Cluster interface and adapter | `.../ports.py` (`ClusterPort`), `.../kube.py` (`KubernetesAdapter`) |
| Identity | `.../auth/` (mock identity, JWT validation, signing-key cache) |
| Chart and environments | `charts/api-service/`, `environments/` |
| Contract | `control-plane/spec/openapi.yaml` (generated, `make openapi`) |

## 6. What the tests cover

- **Real in the end-to-end tests** (`control-plane/cli/test/test_e2e_cli.py`): the CLI, the HTTP pipeline, tokens, the
  ticket endpoint, the WebSocket handshake, the stream limit, and expiry with refresh and resume.
- **Faked:** only the cluster, behind `ClusterPort` (an in-memory fake).
- **Not yet covered:** Traefik with the sticky cookie, real Kubernetes pod logs (LT12), and a real identity provider.

Run them with `make test-api-unit`, `make test-cli` and `make lint`.
