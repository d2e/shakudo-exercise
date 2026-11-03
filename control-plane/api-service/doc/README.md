# api-service

FastAPI service that creates and manages Kubernetes Deployments. Requirements: [`../../spec/req.md`](../../spec/req.md).
Contract: [`../../spec/openapi.yaml`](../../spec/openapi.yaml) (generated, `make openapi`).

## Layout (`src/api_service/`)

| Module | Role |
| :--- | :--- |
| `app.py`, `main.py` | app factory, OpenAPI shaping, uvicorn entry (`python -m api_service.main`) |
| `security.py` | one pipeline: request id, authentication first (A1-A4), rate limits, headers, access log |
| `controllers/` | HTTP layer, thin: `deployments.py` (`/api/v1/deployments...`), `probes.py` (`/healthz`, `/readyz`), `auth.py` (mock identity, OIDC documents) |
| `service.py` | use cases and check order: input 400, allowlist 403, then the cluster |
| `ports.py` | `ClusterPort` interface and `KubeFailure`: what the use cases need from the cluster, no SDK types (D27) |
| `kube.py` | `KubernetesAdapter`, the `ClusterPort` implementation and the only module that imports the Kubernetes SDK; no caching; errors reduced to fixed kinds |
| `builder.py` | pure manifest builders (PSS restricted, tmpfs `/tmp`, probes, managed label) |
| `models/` | strict Pydantic models, one module per concern: `deployment.py`, `responses.py`, `auth.py`, `error.py` |
| `validation.py` | pure input checks used by the models |
| `health.py`, `domain.py` | pure health mapping over plain snapshots |
| `auth/` | token minting and validation, signing-key cache (the HTTP side is `controllers/auth.py`) |

## Run and test

```
make test-api-unit     # fake cluster, no network
make test-integration  # real local cluster, creates cp-test-* namespaces only
make openapi           # regenerate spec/openapi.yaml; a test fails if it is stale
ENVIRONMENT=dev BOOTSTRAP_SECRET_FILE=/path/secret ALLOWED_NAMESPACES=demo python -m api_service.main   # from src/
```

Settings are environment variables, see `config.py`. `ALLOWED_NAMESPACES` empty denies everything (R6).
RBAC for the pod's ServiceAccount: [`charts/api-service/templates/rbac.yaml`](../../../charts/api-service/templates/rbac.yaml).

## Behaviour worth knowing

- **Namespace cleanup (D9)** runs as a background task after `DELETE` returns 202. Foreground deletion keeps
  the Deployment and pods alive for a while, so it re-checks emptiness for up to 30 s, then deletes the
  namespace only if it is allowlisted, carries the managed label and the live check finds nothing (R11).
- **Default resources (D31):** `resources` is optional on create. Omitted means `DEFAULT_CPU_REQUEST`, `DEFAULT_MEMORY_REQUEST`, `DEFAULT_CPU_LIMIT`, `DEFAULT_MEMORY_LIMIT` (50m / 64Mi, 200m / 128Mi), checked at startup against the caps; the response says `resourcesDefaulted`. A partial `resources` is a 400, never merged.
- **Secret references** work only in namespaces that have the per-namespace Role in the chart's `rbac.yaml`; otherwise the
  check reads as "missing" and the request is refused with `secret_not_allowed` (R12, R14).
- **Deploy:** the chart is [`charts/api-service/`](../../../charts/api-service) (D26). `make lint CHART=api-service`,
  `make render-dev CHART=api-service`, `make deploy-dev CHART=api-service`. Overlays: `environments/<env>/api-service.yaml`.
  Dev needs `environments/dev/api-service.secrets.yaml` (copy the `.example`) and the `demo` namespace to exist.
- **Not built yet:** log streaming (R21), OpenTelemetry (F5), CLI, Dockerfile (the chart expects `api-service:1.0.0`).
