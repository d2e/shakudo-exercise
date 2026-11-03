# Security Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only. Findings behind these rules: [review-security.md](./review-security.md).

## Decisions

| # | Decision |
| :-- | :--- |
| D20 | Token minting needs a **bootstrap secret** and is refused outside dev. |
| D21 | A **namespace allowlist** limits where the API may act. Everything else is refused before any Kubernetes call. |
| D22 | A Secret can be referenced only if it carries an **allow label**. |
| D23 | **HTTPS is required** in staging and prod. The CLI refuses plain `http://` except for localhost. |

## Token minting (D20)

| # | Requirement |
| :-- | :--- |
| R1 | `POST /api/v1/auth/token` requires a **bootstrap secret** sent in the `X-Bootstrap-Secret` header (header name is an assumption). Without it: 401. |
| R2 | The bootstrap secret lives in a Kubernetes Secret, mounted as a file (K1). It is compared in constant time and never logged or echoed. |
| R3 | **Dev only.** Startup fails (C6) when `MOCK_AUTH_ENABLED=true` and the environment is not dev. The chart schema rejects the same combination. |
| R4 | Mint and refresh endpoints are rate limited (R18) and answer 429 with `Retry-After`. Failed attempts are logged without the supplied secret. |
| R5 | **Refresh token lifetime is 1 hour** (changes T5 from 8 hours). Refresh tokens remain non-revocable and not single-use (T6); the shorter life limits the damage. |

## Namespace scope (D21)

| # | Requirement |
| :-- | :--- |
| R6 | `ALLOWED_NAMESPACES` (comma-separated names) lists the namespaces the API may touch. **Empty means deny all.** |
| R7 | **Order of checks:** authenticate (401), validate input (400), check the allowlist (**403**, code `namespace_not_allowed`), then call the cluster. A refused request makes no Kubernetes call. |
| R8 | The three reserved namespaces (V10) are refused even if listed. |
| R9 | `GET /api/v1/deployments` without a filter returns only deployments in allowlisted namespaces. A `?namespace=` outside the list gives 403. |
| R10 | Namespaces the API creates get the managed label, `pod-security.kubernetes.io/enforce: restricted`, and a default `ResourceQuota` and `LimitRange` (values are an assumption). |
| R11 | **Namespace deletion (D9)** happens only if the namespace is allowlisted, carries the managed label, **and** a final live check finds no objects of these kinds: Deployments, StatefulSets, DaemonSets, Jobs, CronJobs, Pods, Services, PersistentVolumeClaims, Secrets, and ConfigMaps other than `kube-root-ca.crt`. If the check cannot be completed, the namespace is **not** deleted. |

## Secret references (D22)

| # | Requirement |
| :-- | :--- |
| R12 | A Secret reference is accepted only if the Secret exists in the target namespace and carries the label `control-plane.io/allow-reference=true`. Otherwise 400, code `secret_not_allowed`. Missing and unlabeled Secrets give the **same** error, so existence is not revealed. |
| R13 | The check is a live read of Secret **metadata only** (no caching, N1). The API never returns, logs or stores Secret values (K6). |
| R14 | This replaces K4 and K9. Kubernetes cannot grant "metadata only", so `get` on `secrets` is granted through **Roles in allowlisted namespaces**, never a ClusterRole. |

## Transport and browser (D23)

| # | Requirement |
| :-- | :--- |
| R15 | Staging and prod expose the API only over **HTTPS**: the chart schema rejects an enabled ingress with empty `tls` outside dev. Responses over HTTPS send `Strict-Transport-Security`. |
| R16 | The CLI refuses an `http://` server unless the host is `localhost`, `127.0.0.1` or `::1`, or `--insecure` is given (which prints a warning). It never forwards the token to another host after a redirect. |
| R17 | **CORS** is a setting (allowed origins), **empty by default**. A wildcard `*` together with credentials is never allowed. |

## Abuse limits

| # | Requirement |
| :-- | :--- |
| R18 | **Rate limits**, per client and configurable: auth endpoints 10 per minute, mutating endpoints 30 per minute, reads 300 per minute (values are assumptions). Over the limit: 429 with `Retry-After`. The UID lookup (D1) lists all managed deployments, so this limit protects the cluster API. |
| R19 | **Resource caps** (configurable): CPU limit up to 2 per container, memory limit up to 2 GiB, replicas up to 20 (replaces the open maximum in V12 and V14). |
| R20 | `ALLOWED_REGISTRIES` lists permitted image registries. In staging and prod it must be set (schema enforced); in dev, empty allows any registry. |
| R21 | Log streams: at most **3 concurrent per user**, each ending at token expiry (T8). |

## Network and exposure

| # | Requirement |
| :-- | :--- |
| R22 | The chart enables a **default-deny NetworkPolicy**: ingress only from the ingress controller, egress only to the Kubernetes API, DNS, and the identity provider (when a real one is used). This replaces the "no NetworkPolicy change now" assumption in [req-observability.md](./req-observability.md). |
| R23 | OpenAPI and interactive docs are served **only in dev**, or require authentication. They are not on the public list in A3. |

## Information disclosure

| # | Requirement |
| :-- | :--- |
| R24 | **Sanitized errors.** Kubernetes errors map to fixed messages: connection failure gives 500 "cluster unreachable"; denied by the cluster gives 500 "operation not permitted"; other failures give 500 "cluster operation failed". Raw Kubernetes text, hostnames and RBAC details are never returned. Every error carries a `request_id` for log lookup. |
| R25 | A duplicate name gives 409 with a generic message ("name already in use"). It can reveal that an unmanaged deployment with that name exists. This is **accepted** and documented. |
| R26 | Audit events record the token `sub` and `iss`. With the mock provider the `sub` is self-asserted, so the audit trail is trustworthy only with a real provider. Documented, not fixed. |
| R27 | A signing key removed at the provider stays trusted until the cache TTL passes (N10), or until restart. Documented. |
| R28 | `DEPLOY_TOKEN` in the environment is visible to the process. The CLI also accepts `--token-file` and stdin, which are preferred. |

## Assumptions to confirm

1. All numeric values (rate limits, caps, quota, 1-hour refresh life).
2. The bootstrap header name.
3. Secret reads are scoped by Roles that the chart creates for namespaces that already exist. A namespace the API creates on demand has no such Role, so a Secret reference there is refused (400, `secret_not_allowed`) until the chart provisions the Role. Plain deployments work in both cases.
4. Fallback if R14 is too heavy: a configured allowlist of `namespace/name` Secret references that needs no Secret permission.
