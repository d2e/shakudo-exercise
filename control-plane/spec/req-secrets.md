# Secrets Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only.

## Decision

| # | Decision |
| :-- | :--- |
| D11 | **Secrets live in Kubernetes Secrets, never in environment variable values.** This applies to the control plane's own secrets and to secrets for the workloads it deploys. |

## Control plane's own secrets

| # | Requirement |
| :-- | :--- |
| K1 | Secret material (the mock identity signing key, the token-minting bootstrap secret (R1), and any client or credential secret) is stored in a **Kubernetes Secret** and mounted into the pod as a **read-only file**. The environment holds only the file path, never the value. |
| K2 | Nothing secret is baked into the image, the chart values, `environments/*`, or the repository. |
| K3 | Only placeholders are committed (`secrets.example.yaml`, per `AGENTS.md`). Real values are supplied at deploy time or by an external secret manager (External Secrets Operator, Vault, SOPS). |
| K4 | **Superseded by R14.** The kubelet mounts the API's own Secrets, so those need no permission. But checking an allow label on workload Secrets (R12) needs `get` on `secrets`, granted only by Roles in allowlisted namespaces. |
| K5 | The chart adds the Secret reference and mount through values and schema (per `AGENTS.md`). A Secret checksum annotation restarts pods when it changes. |
| K6 | Secret values are never logged, returned in responses, or used as metric or log attributes (see O3 in [req-observability.md](./req-observability.md)). |

## Secrets for deployed workloads

| # | Requirement |
| :-- | :--- |
| K7 | The `env` field of `POST /api/v1/deployments` carries **plain values only**. It must not be used for secrets. |
| K8 | A workload receives a secret by **referencing an existing Kubernetes Secret** (Secret name and key), not by sending the value to the API. The API never receives, stores, creates, reads or returns Secret values. |
| K9 | **Superseded by R12.** The API checks that the Secret exists and carries the allow label before accepting the reference. If a referenced Secret is deleted later, the pod shows `CreateContainerConfigError`, which the health rules treat as `FAILING` ([req-status.md](./req-status.md)). |
| K10 | Invalid references (missing name or key, bad names) are rejected with 400. |

## Consequences

- The mock identity signing key is loaded from the Secret (K1), so tokens survive restarts and more than one replica can validate them. The single-replica limit in [req-auth.md](./req-auth.md) is removed.
- Workload Secrets must already exist in the target namespace, and carry the allow label (R12). The API does not create them. In a namespace the API creates on demand, a Secret reference is refused until the chart provisions the Role that allows the check (see [req-security.md](./req-security.md)).
- Tests (K7–K10): a request with a secret reference creates the pod spec with `secretKeyRef`; a request with an invalid reference returns 400; no response or log line contains a secret value.

## Assumptions to confirm

1. Own secrets are mounted as files, not injected through `secretKeyRef` env, to follow "not in env" literally.
2. The signing key is loaded from the Secret, replacing the in-memory key (decided; see Consequences).
3. The `env` schema gains an optional secret reference form (name and key), while plain `value` stays as is. This extends the assessment's key-value `env` field.
4. If a future collector needs an auth header, the OTel SDK reads it from an environment variable, which is an exception to the rule. Not needed now, since no collector is deployed.
5. Creating Secrets through the API is out of scope.
