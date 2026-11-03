# Configuration Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only. Variable names are proposals.

## Decision

| # | Decision |
| :-- | :--- |
| D12 | The API is configured entirely from outside the code. The **Kubernetes cluster endpoint** and how to connect to it are settings, not constants. |

## Rules

| # | Requirement |
| :-- | :--- |
| C1 | **One typed settings module** reads configuration from environment variables, with documented defaults. Secrets are the exception and come from mounted files (D11, K1). |
| C2 | **Kubernetes connection settings:** `KUBE_API_SERVER` (cluster endpoint URL), `KUBE_CA_FILE` (CA certificate path), `KUBE_TOKEN_FILE` (ServiceAccount or bearer token path), `KUBE_VERIFY_TLS` (default `true`), `KUBE_REQUEST_TIMEOUT_SECONDS` (default 10), `KUBECONFIG` and `KUBE_CONTEXT` for local development. |
| C3 | **Connection mode precedence:** (1) explicit `KUBE_API_SERVER` with its CA and token files; (2) in-cluster ServiceAccount; (3) kubeconfig. The mode in use is logged at startup. |
| C4 | **Credentials are files, not values.** `KUBE_TOKEN_FILE` and `KUBE_CA_FILE` are paths. The token is read from the file on use, so a rotated token is picked up without a restart. |
| C5 | **TLS verification is on by default.** Turning it off needs `KUBE_VERIFY_TLS=false` explicitly and logs a warning at startup. |
| C6 | **Fail fast.** Settings are validated at startup (types, URL format, files exist and are readable, TTLs within limits). An invalid setting stops the process with a non-zero exit and a message that names the setting, never its secret value. |
| C7 | **Readiness reflects connectivity.** `/readyz` makes a live, cheap call to the configured endpoint; `/healthz` never depends on the cluster. |
| C8 | **Startup log of effective config** (secrets omitted), so an operator can see which endpoint and mode are active. |
| C9 | **Read once at startup.** Changing configuration needs a restart. The chart restarts pods on change with a config checksum annotation. |
| C10 | **Chart integration** per `AGENTS.md`: new settings go in `charts/app/values.yaml`, mirrored in `values.schema.json`, with per-environment overrides only in `environments/*`. Non-secret settings go in a ConfigMap, credentials in a Secret. |
| C11 | **Tests:** defaults apply; overrides apply; invalid values are rejected at startup; connection mode precedence is respected; no secret value appears in logs or errors. |

## Other settings

| Setting | Default | Notes |
| :--- | :--- | :--- |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | listen address |
| `LOG_LEVEL` | `info` | |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_DISCOVERY_URL` | existing defaults | see [req-auth.md](./req-auth.md) |
| `MOCK_AUTH_ENABLED` | `true` | demo only; refuse `true` in staging and prod (assumption) |
| `ACCESS_TOKEN_TTL_SECONDS` | `300` | maximum 300 (T1) |
| `REFRESH_TOKEN_TTL_SECONDS` | `28800` | 8 hours (T5) |
| `PROGRESS_DEADLINE_SECONDS` | `60` | applied on create |
| `OTEL_*` | unset | see [req-observability.md](./req-observability.md) |
| signing key path | none | file from a Secret (K1) |

## Assumptions to confirm

1. The variable names above.
2. One cluster only. A cluster list or multi-cluster support is out of scope.
3. The old `ALLOWED_NAMESPACES` allowlist is not carried over: scope is set by the managed label (D2). Say if namespace restriction is still wanted.
4. `MOCK_AUTH_ENABLED=true` is refused outside dev by the chart schema or startup check.
5. Configuration comes from environment variables only. No config file format is defined.
