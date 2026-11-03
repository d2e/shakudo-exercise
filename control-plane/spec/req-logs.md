# Log Streaming Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only. **Built and tested end to end:** the API (ticket endpoint in `openapi.yaml`, WebSocket) and the CLI (`deploy logs`, see [req-cli.md](./req-cli.md), a stretch goal D7). The end-to-end tests run the real CLI against the real API on a real port with an in-memory cluster; a live-cluster run (LT12) is still to do.

## Decision

| # | Decision |
| :-- | :--- |
| D28 | Logs stream over **WebSocket**, and authentication happens **over HTTP first**. An authenticated `POST` returns a short-lived **ticket**. The client then opens the WebSocket and presents only the ticket. |

Why this shape:

- The existing pipeline (A1, A2) does authentication, rate limits, the namespace allowlist and the ownership checks for the `POST`. Those failures are ordinary 401, 403, 404 and 429 responses, before any upgrade.
- A browser cannot set an `Authorization` header on a WebSocket, so the later web UI could not use a Bearer-authenticated upgrade. The ticket works for the CLI and the UI alike.
- The long-lived access token never goes in a URL or a WebSocket frame (K6, R28).
- Rejected: a redirect from an HTTP response to a WebSocket (browsers treat a non-101 handshake as a failure, and the CLI never forwards a token after a redirect, R16); a Bearer header on the upgrade request (no browser support); SSE (simpler, but not the chosen direction).

## Flow

```
Client                                         API
POST /api/v1/deployments/{id}/logs/ticket
  Authorization: Bearer <access token>         normal pipeline: 401, 400, 403 (R7), 404 (D1, D2), 429
  {pod?, tailLines?, sinceTime?}          ◄──  200 {ticket, streamPath, expiresIn: 30}

GET {server, https→wss}/api/v1/logs/stream     (Upgrade: websocket)
  Sec-WebSocket-Protocol: logs.v1, ticket.<t>  validate the ticket only, re-check live (LG9)
                                          ◄──  101, then one JSON frame per log line
                                               close 4001 when the minted-from token expires (T8)
```

## Endpoints and ticket

| # | Requirement |
| :-- | :--- |
| LG1 | Two endpoints, both under `/api/v1` (P1): `POST /api/v1/deployments/{id}/logs/ticket` (Bearer, in OpenAPI, `operationId` `createLogTicket`) and `GET /api/v1/logs/stream` (WebSocket upgrade, authenticated by the ticket). |
| LG2 | **Ticket request.** Strict body `{pod?, tailLines?, sinceTime?}`: `pod` a pod name of this deployment, `tailLines` 1 to 1000 (default 100), `sinceTime` RFC 3339. Checks run in the order of A1: 401, 400, then the cluster: 404 for an unknown or unmanaged deployment (including one outside the allowlist, as for `get` and `patch`, because the request carries an id and no namespace, so there is no 403), or a pod that does not belong to it. Then 429 if the stream limit is reached (LG11). |
| LG3 | **Response** `200 {ticket, streamPath, expiresIn}`. `streamPath` is `/api/v1/logs/stream`; the client uses its own server address with the scheme swapped, so no public-URL setting is needed. |
| LG4 | **Ticket format.** A token signed with the same key as access tokens (D19 cache applies), with the claim `token_use` set to `log-ticket`, `sub`, `iss`, the deployment UID, namespace, optional pod, `tailLines`, `sinceTime`, `iat`, `exp` (30 s after `iat`), a `jti`, and `stream_until`, the `exp` of the access token that requested it. It authorizes nothing else. A ticket is rejected as an access or refresh token, and the reverse (T6). Outside dev the key comes from `SIGNING_KEY_FILE` and is always required, with a real identity provider too: startup fails without it, and the chart mounts it from a Secret (`secrets.existingSecret`). Otherwise each replica would sign with its own temporary key and a ticket would only work on the replica that issued it. |
| LG5 | **Single use, best effort.** Each replica remembers the `jti` of tickets it has accepted until they expire and refuses a repeat. With session affinity (LG19) the ticket request and the upgrade from one client reach the same replica, so a repeat by that client is caught. The service is stateless (N1), so a ticket replayed by a client without the affinity cookie could land on another replica and be accepted within its 30 s. Accepted: it only opens a read-only log stream of one deployment, for the same user. |

## Stream

| # | Requirement |
| :-- | :--- |
| LG6 | **Carriage.** The client offers `logs.v1` and `ticket.<value>` in `Sec-WebSocket-Protocol`. The server selects only `logs.v1`. The ticket is never in the URL or a query string, and never logged (K6). |
| LG7 | **One check function.** The stream route validates the ticket with a single function. A missing, malformed, expired, wrong-type or repeated ticket refuses the handshake with no upgrade and nothing echoed. The HTTP middleware does not run for WebSocket routes, so this route is listed in [req-auth.md](./req-auth.md) A3 as authenticated by ticket, and is covered by LT9. |
| LG8 | **Origin.** If an `Origin` header is present it must be in `CORS_ALLOWED_ORIGINS` (R17), otherwise the handshake is refused. WebSockets are not subject to CORS. The CLI sends no `Origin`. |
| LG9 | **Live re-check at open (N4).** About 30 s may have passed, so the namespace allowlist, the managed deployment and the pod ownership are checked again against the cluster. Anything that no longer holds refuses the handshake. |
| LG10 | **Frames.** Server to client text frames, one JSON object per log line: `{"pod": "<name>", "time": "<RFC 3339>", "line": "<text>"}`. The client sends nothing. **Close codes:** `1000` the stream ended (deployment or pods gone), `4001` token expired (the client may reconnect with a new ticket, T8), `1001` server shutting down, `1013` client too slow, `1011` cluster failure (fixed message, R24). |
| LG11 | **R21 limit.** At most 3 open streams per user. The ticket request is refused with 429 when the user already has 3 (best effort), and the upgrade is refused authoritatively. Counts are per API replica, like the rate limits (R18). With session affinity (LG19) one user's CLI runs share a replica, so the limit holds for them. A user on several machines, or after a replica restart, can exceed it. A cluster-wide count needs a shared store (not now). |
| LG12 | **Pods (D30).** Without `pod`, one stream merges the pods the deployment has at open time that are Running, Succeeded or Failed, up to the replica cap (R19); pending pods are skipped, while a named `pod` is always tried. Pods created later are not followed. The client reconnects to pick them up. One WebSocket counts as one stream for R21 (LG11), however many pods it carries. Lines from different pods are not in time order, so a client must track each pod separately. |
| LG13 | **Mechanics.** The WebSocket handler is async and the Kubernetes client is synchronous (F3), so each pod's log is read by its own daemon thread (the shared request threadpool stays free) and feeds a bounded queue. A full queue makes the readers wait, so a burst never closes a healthy client. A client whose send, or a full queue, stalls for `LOG_SEND_TIMEOUT_SECONDS` (5) is closed with `1013` and never blocks other streams. Settings: `LOG_TICKET_TTL_SECONDS`, `MAX_LOG_STREAMS_PER_USER`, `LOG_QUEUE_MAX_LINES`, `LOG_SEND_TIMEOUT_SECONDS`. |
| LG14 | **Shutdown (H7).** On `SIGTERM` open streams close with `1001` before the grace period ends, so the client reconnects to another replica. |
| LG15 | **Keepalive.** The server sends a ping every 20 s. The chart sets the Ingress read and idle timeouts above that. |
| LG16 | **Audit.** Ticket issue, stream open and stream close are logged with `sub`, deployment UID, pod and duration. Log content is never written to the API's own logs. The number of open streams is in the audit lines; a metrics gauge waits for a metrics pipeline (none exists yet). |
| LG17 | **RBAC.** `pods/log` `get` is already granted. No new verbs. |
| LG18 | **Contract.** The ticket endpoint is in `openapi.yaml` (F8–F15). OpenAPI 3.1 cannot describe a WebSocket, so LG6 and LG10 are the protocol definition and are tested. |
| LG19 | **Session affinity (D29).** Staging and prod enable Traefik cookie affinity on the Service through chart values (`service.annotations`): cookie `deploy_affinity`, `Secure`, `HttpOnly`, `SameSite=Strict`, `Max-Age` 3600. Dev has one replica and plain HTTP, so it has none. Affinity is a hint, not a control: rolling updates, scale-down, restarts and clients without the cookie ignore it, so no rule may depend on it for correctness or security. The cost is uneven load across replicas. `Strict` suits the CLI and a same-site UI. A UI on another site would need `Lax` or `None`. |

## Tests

Unit tests use a fake cluster and the framework's WebSocket test client (Q1). The fourth-stream case is also Q41 in [req-tests.md](./req-tests.md).

| # | Test | Expected |
| :-- | :--- | :--- |
| LT1 | Ticket request: no token; invalid body; namespace not allowed; unknown or unmanaged deployment; another deployment's pod; a fourth stream | 401 before anything else; 400; 403; 404; 404; 429 (LG2, LG11) |
| LT2 | Ticket contents | Valid 30 s at most, bound to user, deployment and pod; `stream_until` equals the access token's expiry (LG4) |
| LT3 | Present an access token as a ticket, and a ticket as an access token | Both refused (LG4) |
| LT4 | Open with a valid ticket | `101`, `logs.v1` selected, frames carry `pod`, `time`, `line` (LG6, LG10) |
| LT5 | Open with no ticket, a malformed, expired or repeated ticket, or a ticket for another deployment | Handshake refused, no upgrade, nothing echoed (LG5, LG7) |
| LT6 | Ticket in URL, logs, errors and `--debug` output | Never present (LG6, K6) |
| LT7 | `Origin` not allowed; no `Origin` | Refused; accepted (LG8) |
| LT8 | Deployment deleted between ticket and open; and during the stream | Refused; close `1000` (LG9, LG10) |
| LT9 | Enumerate every WebSocket route in the app | Each one validates a ticket first (LG7, A8) |
| LT10 | Stream at `stream_until`; reconnect with a new ticket and `sinceTime` | Close `4001`; no repeated lines (LG10, T8) |
| LT11 | Slow client; `SIGTERM` | Close `1013`, others unaffected; close `1001` (LG13, LG14) |
| LT12 | Integration (marked `integration`, Q2): a real pod's logs through the CLI | Lines arrive in order, with the pod name |
| LT13 | Render the chart for staging and prod, and for dev | The Service carries the six sticky annotations in staging and prod, none in dev (LG19) |

## Assumptions to confirm

1. Ticket lifetime 30 s, 1 to 1000 `tailLines`, and a 20 s ping (all adjustable settings).
2. Replay inside the ticket's 30 s on another replica is accepted (LG5).
3. Pods are merged at open and not followed afterwards (LG12).
4. The close code `4001` and the frame shape (LG10).
5. Cookie affinity at the Ingress (LG19), with a 1 h `Max-Age` and `SameSite=Strict`.
