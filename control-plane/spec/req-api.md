# API Requirements

Part of the spec set indexed in [`req.md`](./req.md). Endpoints and the request schema are in `req.md` (Part 1), with every path under `/api/v1` (D16). Health and response fields: [req-status.md](./req-status.md). Requirements only.

## Decisions

| # | Topic | Decision |
| :-- | :--- | :--- |
| D1 | `:id` | The Deployment **Kubernetes UID**. The API resolves it by listing managed deployments; an unknown UID gives 404. |
| D2 | Managed scope | Only deployments the API created. Label `app.kubernetes.io/managed-by=control-plane` is set on create. All others are invisible (404) and never modified. |
| D4 | Image tags | A missing tag or `latest` is rejected with **400**. The `nginx:latest` scenario uses a pinned tag (follows `AGENTS.md`). |
| D6 | `port` | Sets `containerPort` only. No Service is created. |
| D8 | Pod security | The API always applies the PSS **restricted** settings. Callers cannot relax them. Demo images must run non-root (for example `nginxinc/nginx-unprivileged` or `hashicorp/http-echo`). |
| D9 | Namespaces | Created if missing. After DELETE, the namespace is **deleted if it is now empty**. |
| D16 | Versioning | The API is **v1**: every endpoint is under `/api/v1`. |

## Versioning (D16)

| # | Requirement |
| :-- | :--- |
| P1 | The assessment's `/api/deployments...` paths become `/api/v1/deployments...`: `POST` and `GET /api/v1/deployments`, `GET`, `PATCH` and `DELETE /api/v1/deployments/{id}`, `POST /api/v1/deployments/{id}/restart`. |
| P2 | Auth endpoints are already under `/api/v1/auth/...` ([req-auth.md](./req-auth.md)). |
| P3 | Probes (`/healthz`, `/readyz`) and the OIDC discovery and JWKS documents stay at their standard unversioned paths (assumption). |
| P4 | A request to an unversioned `/api/deployments...` path returns 404. No redirect, no alias (assumption). |
| P5 | Within v1, changes are backward compatible only: new optional fields and endpoints are allowed, removing or renaming fields is not. Breaking changes need `/api/v2`. |
| P6 | The OpenAPI document is served and states the version (`1.0.0`). |

## Behaviour

- **Errors** follow the assessment: 400 invalid input, 404 unknown id, 409 duplicate name in the namespace, 500 for Kubernetes failures with a meaningful message. Bodies are structured JSON (`error.code`, `error.message`, `request_id`; shape is an assumption).
- **PATCH** accepts `replicas` and/or `image` (at least one). Other fields are rejected with 400. A new image triggers a rolling update.
- **Create** requires `name`, `namespace`, `image` and all four resource values. Defaults: `replicas` 1, `env` and `labels` empty. It sets `progressDeadlineSeconds` to 60 (assumption) so a bad image reaches `FAILING` quickly.
- **Delete** cascades to ReplicaSets and Pods (foreground propagation), then applies D9.
- **Restart** patches the pod-template annotation `kubectl.kubernetes.io/restartedAt`.

## No caching

**Every request must call the Kubernetes API through the SDK.** The control plane keeps no copy of cluster state.

| # | Requirement |
| :-- | :--- |
| N1 | **No state cache.** No in-memory, file or database copy of deployments, pods, namespaces or their status. No informers, watch caches or background sync. |
| N2 | **Fresh reads.** Each request makes its own SDK calls and returns what the cluster says at that moment: list, detail, health, and the `:id` lookup. |
| N3 | **No memoizing.** No `lru_cache` or TTL cache on Kubernetes calls, and no sharing of calls across concurrent requests. |
| N4 | **Checks are live too.** Namespace-exists, duplicate-name, and the empty-namespace check before delete each query the cluster when needed. |
| N5 | **Clients may be reused.** A shared SDK client object (connection pool, loaded kubeconfig) is allowed, because it holds no cluster data. |
| N6 | **Responses are not cacheable.** `Cache-Control: no-store`. |
| N7 | **Test:** change the cluster directly (outside the API) and show the next GET reflects it. |

The one exception is the token signing key, which is cached and validated against (D19, N8–N15 in [req-auth.md](./req-auth.md)). That cache holds identity keys only, never cluster data.

## Consequences

- The `:id` lookup lists managed deployments on every request: slower on large clusters, accepted.
- If the cluster is unreachable, the API returns 500 and serves no stale data. Load grows with request volume, so rate limiting is the control.
- **Kubernetes RBAC** must grow beyond the current role: `patch` and `delete` on deployments, and `delete` on namespaces.
- D9 is the riskiest decision: a cluster-wide namespace delete permission, and an emptiness check that can race with a new deployment landing in the same namespace. Mitigations: only delete namespaces carrying the managed label, and re-check emptiness immediately before deleting.
- D8: arbitrary public images that need root or a writable root filesystem will fail to start. That is expected.

## Assumptions to confirm

1. The required fields and defaults above.
2. The error body shape.
3. Namespace deletion applies only to namespaces the API created (managed label on the namespace).
4. Out of scope: Service or Ingress creation, multi-container pods, multi-cluster, secrets in `env`.
