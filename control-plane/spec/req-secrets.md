# Secrets Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only.

## Decision

| # | Decision |
| :-- | :--- |
| D11 | **Secrets live in Kubernetes Secrets, never in environment variable values.** This applies to the control plane's own secrets and to secrets for the workloads it deploys. |

## Control plane's own secrets

| # | Requirement |
| :-- | :--- |
| K1 | Secret material (the mock identity signing key and any client or credential secret) is stored in a **Kubernetes Secret** and mounted into the pod as a **read-only file**. The environment holds only the file path, never the value. |
| K2 | Nothing secret is baked into the image, the chart values, `environments/*`, or the repository. |
| K3 | Only placeholders are committed (`secrets.example.yaml`, per `AGENTS.md`). Real values are supplied at deploy time or by an external secret manager (External Secrets Operator, Vault, SOPS). |
| K4 | The pod's ServiceAccount may read only the Secrets it mounts; the mount is handled by the kubelet, so the API needs **no `secrets` permission** in RBAC. |
| K5 | The chart adds the Secret reference and mount through values and schema (per `AGENTS.md`). A Secret checksum annotation restarts pods when it changes. |
| K6 | Secret values are never logged, returned in responses, or used as metric or log attributes (see O3 in [req-observability.md](./req-observability.md)). |

## Secrets for deployed workloads

| # | Requirement |
| :-- | :--- |
| K7 | The `env` field of `POST /api/v1/deployments` carries **plain values only**. It must not be used for secrets. |
| K8 | A workload receives a secret by **referencing an existing Kubernetes Secret** (Secret name and key), not by sending the value to the API. The API never receives, stores, creates, reads or returns Secret values. |
| K9 | The reference is passed through to the pod spec (`secretKeyRef`). The API does not verify that the Secret exists, because that would need `get` on secrets (K4). A missing Secret shows as `CreateContainerConfigError` on the pod, which the health rules treat as `FAILING` ([req-status.md](./req-status.md)). |
| K10 | Invalid references (missing name or key, bad names) are rejected with 400. |

## Consequences

- The mock identity signing key currently exists only in memory (D10), so a restart invalidates all tokens. Loading it from the Secret (K1) makes tokens survive restarts and lets more than one replica validate them. This is an improvement, but it changes the "single replica" constraint in [req-auth.md](./req-auth.md) and is listed below for confirmation.
- Workload Secrets must already exist in the target namespace. The API does not create them. Because the API may create namespaces (D9), a caller can reference a Secret in a namespace that does not exist yet and will see `CreateContainerConfigError` until it is created.
- Tests (K7–K10): a request with a secret reference creates the pod spec with `secretKeyRef`; a request with an invalid reference returns 400; no response or log line contains a secret value.

## Assumptions to confirm

1. Own secrets are mounted as files, not injected through `secretKeyRef` env, to follow "not in env" literally.
2. The signing key is loaded from the Secret, replacing the in-memory key (see Consequences).
3. The `env` schema gains an optional secret reference form (name and key), while plain `value` stays as is. This extends the assessment's key-value `env` field.
4. If a future collector needs an auth header, the OTel SDK reads it from an environment variable, which is an exception to the rule. Not needed now, since no collector is deployed.
5. Creating Secrets through the API is out of scope.
