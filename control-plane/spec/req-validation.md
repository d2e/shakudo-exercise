# Input Validation Requirements

Part of the spec set indexed in [`req.md`](./req.md) (Part 1 error handling, Part 4 tests). Requirements only.

## Decision

| # | Decision |
| :-- | :--- |
| D13 | Every request is **validated statically before any Kubernetes call**. Invalid input is rejected with **400** and never reaches the cluster. |

## General rules

| # | Requirement |
| :-- | :--- |
| V1 | **Validate first, then call Kubernetes.** An invalid request makes zero Kubernetes calls. Checks that need the cluster (duplicate name 409, unknown id 404, namespace state) come after validation and are not "static". |
| V2 | **Status 400 for all input errors.** The framework default for body errors is 422, so it must be mapped to 400. |
| V3 | **All violations are reported at once.** The structured error body lists every problem: `error.code` (`invalid_request`), and `error.details[]` with `field`, `rule` and `message`. |
| V4 | **Unknown fields are rejected** with 400 (a typo such as `replica` must not be silently ignored). |
| V5 | **Strict types.** `"3"` is not an integer, `true` is not a number, `1.5` is not a replica count. |
| V6 | **Body must be a JSON object** with `Content-Type: application/json`. Malformed JSON or an empty body gives 400. Body larger than 1 MiB gives 413 (assumption). |
| V7 | **Errors never echo secret values**, including `env` values (see O3, K6). |
| V8 | **One validation layer**, shared by create and patch, made of pure functions that can be unit-tested without a cluster. The CLI may pre-check, but the server is authoritative. |

## Field rules (`POST /api/v1/deployments`)

| # | Field | Required | Rule |
| :-- | :--- | :--- | :--- |
| V9 | `name` | yes | DNS-1123 label: `^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`, 1 to 63 chars. Rejects uppercase, `_`, `.`, spaces, leading or trailing `-`, empty. |
| V10 | `namespace` | yes | Same DNS-1123 label rule. Reserved system namespaces are rejected (assumption below). The namespace must also be on the allowlist, checked after validation and answered with 403 (R6, R7). |
| V11 | `image` | yes | Valid container image reference: `[registry[:port]/]path[:tag][@sha256:<64 hex>]`, path lowercase, no spaces, max 255 chars. A tag or digest is **required**, and `latest` is rejected (D4). Rejects `""`, `nginx:`, `nginx::1`, `NGINX:1.0`, `nginx:bad tag`. The registry must be on `ALLOWED_REGISTRIES` when that is set (R20). |
| V12 | `replicas` | no (1) | Integer from 0 to `MAX_REPLICAS` (20, R19). Negative gives 400. |
| V13 | `port` | no | Integer 1 to 65535. |
| V14 | `resources` | yes | Both `requests` and `limits`, each with `cpu` and `memory`. Quantities must parse (`100m`, `0.5`, `128Mi`, `1Gi`) and be greater than 0. `limits` must be at least `requests`, for cpu and for memory. The caps in R19 apply (CPU limit up to 2, memory limit up to 2 GiB). |
| V15 | `env` | no | Array, max 50. Each entry has `name` matching `^[A-Za-z_][A-Za-z0-9_]*$` and exactly one of `value` (string, max 4096) or a Secret reference (`name` as a DNS-1123 subdomain, `key` matching `^[-._a-zA-Z0-9]{1,253}$`, see K8–K10). No duplicate names. |
| V16 | `labels` | no | Max 20. Keys and values follow Kubernetes label syntax (optional DNS-subdomain prefix, name up to 63 chars, value up to 63 chars). Reserved keys (`app.kubernetes.io/managed-by` and the selector label) are rejected with 400, not overridden. |
| V17 | `command`, `args` | no | Arrays of strings, max 50 items, each up to 4096 chars. `command` entries are not empty. |

## Other inputs

| # | Input | Rule |
| :-- | :--- | :--- |
| V18 | `PATCH` body | At least one of `replicas`, `image`. Each uses the same rule as on create (V11, V12). Any other field gives 400. |
| V19 | `:id` | Must be a well-formed UID (UUID). Malformed gives **400**; well-formed but unknown gives **404**. |
| V20 | `?namespace=` on list | Same DNS-1123 label rule as V10. |

## Tests

| # | Requirement |
| :-- | :--- |
| V21 | Table-driven unit tests, one row per rule above: valid input accepted, each invalid input rejected with 400 and the offending `field` named. |
| V22 | Every invalid case asserts that the Kubernetes client was **not called** (V1). |
| V23 | A test with several errors in one body asserts that all are reported (V3). |
| V24 | Required examples: bad image strings, negative replicas, missing required fields, names with invalid characters (the assessment's list). |

## Assumptions to confirm

1. Limits: `MAX_REPLICAS` 20, 1 MiB body, 50 env entries, and the CPU and memory caps from R19.
2. Reserved namespaces rejected on create: `kube-system`, `kube-public`, `kube-node-lease`.
3. Reserved labels are rejected (400) rather than silently overridden.
4. A malformed `:id` is 400 rather than 404.
5. Registry allowlisting is a setting (R20), required outside dev.
