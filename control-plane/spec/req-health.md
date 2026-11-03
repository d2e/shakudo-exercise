# API Pod Health Check Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only.

This file is about the **health of the control-plane API pod itself**, used by Kubernetes probes. The health of the deployments it manages (`HEALTHY`, `FAILING`, ...) is in [req-status.md](./req-status.md).

## Decision

| # | Decision |
| :-- | :--- |
| D18 | The API exposes **liveness and readiness endpoints** for its own pod, wired to Kubernetes probes. |

## Endpoints

| # | Requirement |
| :-- | :--- |
| H1 | `GET /healthz` is **liveness**. `GET /readyz` is **readiness**. Both are unversioned (P3), unauthenticated (A3), and carry `Cache-Control: no-store`. |
| H2 | **Liveness** returns 200 `{"status": "alive"}` while the process is running and able to answer. It never depends on Kubernetes, the identity provider, or a collector, so an outage elsewhere does not restart the pod. |
| H3 | **Readiness** returns 200 only when the pod can do its job: settings valid (C6), the signing key loaded, and the Kubernetes API reachable and accepting our credentials. |
| H4 | The Kubernetes check is a **live, cheap, authorized call** on every readiness request, for example listing namespaces with `limit=1` (the role allows it). It has a short timeout (2 s) below the probe timeout, and no cached result (N1). |
| H5 | The identity provider is **not** part of readiness, so a provider outage does not take every pod out of service (assumption). |
| H6 | When not ready, readiness returns **503** with the names of failing checks (`{"status": "not_ready", "checks": {"kubernetes": "failed"}}`). Bodies never contain endpoint URLs, tokens or error internals. |

## Lifecycle

| # | Requirement |
| :-- | :--- |
| H7 | **Graceful shutdown.** On `SIGTERM` readiness returns 503 immediately, in-flight requests finish, and the process exits within `terminationGracePeriodSeconds` (30, per `AGENTS.md`). |
| H8 | **Startup.** The pod is not ready until startup checks pass. A `startupProbe` is allowed for slow starts (assumption). |

## Chart and operations

| # | Requirement |
| :-- | :--- |
| H9 | The Deployment defines both `readinessProbe` (`/readyz`) and `livenessProbe` (`/healthz`), as `AGENTS.md` requires. Delay, period, timeout and failure threshold are values in `values.yaml`, mirrored in `values.schema.json`. |
| H10 | Probe requests are not logged at info level and are not audited, to avoid noise (O9). They may appear in HTTP metrics, since the route set is small (O8). |
| H11 | The probe period (10 s, assumption) keeps the live Kubernetes call from adding meaningful load. |

## Tests

| # | Requirement |
| :-- | :--- |
| H12 | Liveness returns 200 even when the Kubernetes API is unreachable. |
| H13 | Readiness returns 200 when the cluster is reachable and 503 when it is not (bad endpoint through configuration, C2), without internal details in the body. |
| H14 | Readiness returns 503 after `SIGTERM` while a request already in flight still completes. |
| H15 | Both endpoints work without a token and send `no-store`. |
| H16 | Integration: the deployed pod reaches `1/1 Ready`, and deleting the cluster credentials makes it not ready without a restart. |

## Consequences

- The existing `/readyz` in `app/main.py` returns a fixed "ready" and checks nothing. It must be replaced by the checks above.
- If the Kubernetes API is down, every pod becomes not ready and the API serves nothing. That is intended: it cannot do its job.

## Assumptions to confirm

1. The identity provider is not part of readiness (H5).
2. Namespace listing is the readiness call.
3. A 2 s check timeout and a 10 s probe period.
4. A startup probe is optional.
5. Probe paths stay unversioned.
