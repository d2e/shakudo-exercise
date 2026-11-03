# Sequence diagrams

The main flows between the `deploy` CLI, the API and Kubernetes, as built. The diagrams are Mermaid, which GitHub renders
in place. The static picture of the components is in [`architecture.md`](./architecture.md). Rule IDs (A1, V14, LG7 ...) and
decisions (D9, D28 ...) point into [`../spec/req.md`](../spec/req.md).

Cast: **CLI** (`control-plane/cli`), **Edge** (Traefik, sticky cookie), **Pipeline** (the HTTP middleware in `security.py`),
**Svc** (`DeploymentService` / `LogService`), **Adp** (`ClusterPort` implemented by `KubernetesAdapter`), **K8s** (the
Kubernetes API server).

## 1. Login and token refresh

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant API as API (pipeline + TokenService)

    CLI->>API: POST /api/v1/auth/token (header X-Bootstrap-Secret, body sub)
    API->>API: rate limit per client address (10 per minute)
    alt wrong bootstrap secret
        API-->>CLI: 401 invalid bootstrap secret
    else dev server and secret ok
        API-->>CLI: 200 access token (300 s at most) and refresh token (1 h)
        CLI->>CLI: store in ~/.config/deploy/credentials (file 0600)
    end
    Note over CLI,API: outside dev there is no token endpoint (404). A real identity provider issues the tokens.

    Note over CLI: before every later command
    alt access token has 60 s or less left
        CLI->>API: POST /api/v1/auth/refresh (refresh token)
        alt refresh rejected
            API-->>CLI: 401
            CLI->>CLI: exit 3, run deploy login
        else ok
            API-->>CLI: 200 new pair
            CLI->>CLI: store the new pair
        end
    end
```

Rules: R1, R4, T1–T6 (`req-auth.md`, `req-security.md`), CL9–CL13 (`req-cli.md`).

## 2. An ordinary command: `deploy scale <id> --replicas 3`

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant Edge as Edge (Traefik)
    participant Pipe as Pipeline
    participant Ctl as Controller + models
    participant Svc
    participant Adp
    participant K8s

    CLI->>Edge: PATCH /api/v1/deployments/UID (Bearer token, affinity cookie)
    Edge->>Pipe: same replica as before
    Pipe->>Pipe: request id, rate limits, authenticate, body size
    alt no or bad token, rate limited, body too large
        Pipe-->>CLI: 401 or 429 or 413, nothing else ran
    else authenticated
        Pipe->>Ctl: route to the controller
        Ctl->>Ctl: strict request model
        alt invalid body
            Ctl-->>CLI: 400 listing every field error
        else valid
            Ctl->>Svc: patch(uid, replicas)
            Svc->>Svc: replica cap and image registry checks
            alt over the cap
                Svc-->>CLI: 400
            else within limits
                Svc->>Adp: find_by_uid (managed only, allowed namespaces)
                Adp->>K8s: list managed deployments
                K8s-->>Adp: deployments
                alt unknown, unmanaged or outside the allowlist
                    Svc-->>CLI: 404
                else found
                    Svc->>Adp: patch_deployment
                    Adp->>K8s: PATCH Deployment
                    K8s-->>Adp: updated Deployment
                    Adp-->>Svc: snapshot (plain data, no SDK types)
                    Svc-->>CLI: 200 with replicas and health, plus an audit log line
                end
            end
        end
    end
    Note over Pipe,CLI: every response has X-Request-ID and Cache-Control no-store. Every error has one shape and a fixed message.
```

Rules: A1–A4, V1–V25, D1, D2, D21, N1–N7, R24. A caller without a valid token only ever sees 401, so a bad body or an
unknown id cannot be probed.

## 3. Create: the order of the checks

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant API as Pipeline + controller
    participant Svc
    participant Adp
    participant K8s

    CLI->>API: POST /api/v1/deployments (Bearer token)
    API->>API: authenticate, limits, strict body model
    API->>Svc: create(request)
    Svc->>Svc: resources omitted? apply the configured defaults (D31)
    alt registry not allowed, or replicas, cpu or memory over the caps
        Svc-->>CLI: 400 with every problem
    else namespace not on the allowlist
        Svc-->>CLI: 403
    else static checks pass
        Note over Svc,K8s: no cluster call has happened up to here
        opt the request has secret references
            Svc->>Adp: secret_labels
            Adp->>K8s: read Secret metadata only (never values)
            alt missing or not labelled
                Svc-->>CLI: 400 secret_not_allowed (same answer for both)
            end
        end
        Svc->>Adp: ensure_namespace
        Adp->>K8s: create namespace if missing (quota, limit range, restricted PSS label)
        Svc->>Adp: create_deployment (manifest from builder.py)
        Adp->>K8s: create Deployment (restricted security context, tmpfs /tmp, probes, managed-by label)
        alt name already used
            Svc-->>CLI: 409
        else cluster failure
            Svc-->>CLI: 500 with a fixed message, no cluster text (R24)
        else created
            Svc-->>CLI: 201 with uid, health, namespaceCreated, resourcesDefaulted
        end
    end
```

Rules: D2, D4, D8, D9, D11, D21, D31, R6–R14, R19, K7–K10.

## 4. Delete, and the namespace cleanup afterwards

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant Svc
    participant Adp
    participant K8s

    CLI->>Svc: DELETE /api/v1/deployments/UID (after the CLI asked for confirmation)
    Svc->>Adp: find_by_uid (managed only)
    Svc->>Adp: delete_deployment (foreground, cascades to ReplicaSets and Pods)
    Adp->>K8s: DELETE Deployment
    Svc-->>CLI: 202 deleting
    Note over Svc,K8s: after the response, in the background (D9, R11)
    loop until the namespace is empty, up to the grace period plus a margin
        Svc->>Adp: namespace_is_empty
        Adp->>K8s: live check of 10 kinds
    end
    alt allowlisted, created by the API, and empty
        Svc->>Adp: delete_namespace
    else anything else, or any check fails
        Note over Svc: the namespace is kept and the reason is logged
    end
    opt deploy delete --wait
        loop every 2 s
            CLI->>Svc: GET /api/v1/deployments/UID
        end
        Svc-->>CLI: 404, so it is gone
    end
```

Rules: D9, R11, CL27–CL29.

## 5. Log streaming (D28): authenticate over HTTP, then switch to WebSocket

```mermaid
sequenceDiagram
    autonumber
    participant CLI
    participant API as API pod
    participant Log as LogService + streaming
    participant K8s

    CLI->>API: POST /api/v1/deployments/UID/logs/ticket (Bearer token, tailLines, pod optional)
    API->>API: normal pipeline: authenticate, rate limit
    API->>Log: issue_ticket
    Log->>K8s: find the managed deployment, check the pod belongs to it
    alt unknown deployment or a pod of another deployment
        API-->>CLI: 404
    else the user already has 3 open streams
        API-->>CLI: 429 too_many_streams
    else ok
        API-->>CLI: 200 ticket (valid 30 s, bound to user, deployment and pod), streamPath
    end

    CLI->>API: GET wss://.../api/v1/logs/stream (Upgrade, subprotocols logs.v1 and ticket.T)
    Note over API: WebSocket routes skip the HTTP pipeline, so one check function decides
    API->>Log: authorize(ticket, Origin)
    Log->>K8s: live re-check of allowlist, deployment and pod
    alt ticket missing, invalid, expired or repeated, bad Origin, or no stream slot
        API-->>CLI: handshake refused (HTTP 403, no upgrade)
    else accepted
        API-->>CLI: 101 Switching Protocols (logs.v1)
        loop one reader thread per pod, merged through a bounded queue
            Log->>K8s: read the pod log (follow)
            K8s-->>Log: lines
            Log-->>CLI: frame with pod, time and line
        end
    end

    Note over CLI,Log: at stream_until (the access token's expiry) the server closes with 4001
    CLI->>CLI: stored login: refresh it. A supplied token cannot be refreshed, so exit 3.
    CLI->>API: new ticket with sinceTime, then reconnect
    CLI->>CLI: drop lines already shown, tracked per pod
    Log-->>CLI: close 1000 when the pod logs end (1001 restarting, 1013 too slow, 1011 cluster failure)
```

Rules: D28, D29, D30, LG1–LG19 (`req-logs.md`), R21, T8, CL30–CL34.

## 6. Errors, one shape

Whatever fails, the client gets `{"error": {"code", "message", "request_id", "details"?}}`. Kubernetes failures are
reduced to a kind in the adapter and to a fixed message in the service:

| Cluster result | Kind | HTTP | Message |
| :--- | :--- | :--- | :--- |
| 404 | `not_found` | 404 | deployment not found |
| 409 | `conflict` | 409 | name already in use |
| 401 or 403 from the cluster | `forbidden` | 500 | operation not permitted by the cluster |
| network error or timeout | `unreachable` | 500 | cluster unreachable |
| anything else | `other` | 500 | cluster operation failed |

The CLI shows the message, one line per detail, and the request id, and never raw JSON (CL4, CL5).
