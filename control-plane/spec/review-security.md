# Security Review of the Requirements

Review of `req.md` and the `req-*.md` files (decisions D1–D19) before this change. Scope: the **requirements**, not code. Fixes are in [req-security.md](./req-security.md) (rules `R1`–`R28`). The only existing code consulted: `app/main.py` and the `charts/app` templates.

## Method

Read every spec file, traced each endpoint through the order of checks (authenticate, validate, authorize, call the cluster), and asked what a holder of a valid token, and what an unauthenticated caller, could do. Four decisions were made with the owner (D20–D23).

## Findings

| Sev | Finding | Spec reference | Resolution | Status |
| :-- | :--- | :--- | :--- | :--- |
| Critical | The open mint endpoint makes JWT auth meaningless: anyone reaching the API mints any identity | `req-auth.md` D10, A3 | D20: R1–R5 | Specified |
| Critical | `secretKeyRef` plus custom `command` plus logs can print another team's Secret in the same namespace | `req-secrets.md` K8 | D22 and D21: R6–R14 | Specified |
| High | Cluster-wide create, patch and delete on deployments, and namespace delete, in any non-system namespace | `req-api.md` D9 | D21: R6–R9, R11 | Specified |
| High | "Namespace is empty" was undefined; deleting a namespace with others' Secrets or volumes is data loss | `req-api.md` D9 | R11 defines the kinds checked, and fails safe | Specified |
| High | Refresh tokens are stateless, not revocable, not single-use, 8 hours, on a public endpoint | `req-auth.md` T4–T6 | R4, R5: 1 hour, rate limited | Reduced, residual risk |
| High | No rate limiting anywhere, and the UID lookup lists all deployments on every call | all | R18 | Specified |
| Medium | No TLS requirement | all | D23: R15, R16 | Specified |
| Medium | CORS `*` with credentials exists in `app/main.py` lines 31–32 | `req-ui.md` U9 | R17, tested in Q36 | Fixed in the API (CORS off by default, no wildcard); remains only in the deprecated `app/`, marked for removal |
| Medium | No NetworkPolicy requirement | `req-observability.md` | R22 | Specified |
| Medium | No resource caps and no registry allowlist | `req-validation.md` V12–V14 | R19, R20 | Specified |
| Medium | A removed or compromised signing key stays trusted up to 10 minutes (and up to 1 hour stale during an outage) | `req-auth.md` N10–N12 | R27 | Accepted, documented |
| Medium | Audit actor is self-asserted with the mock provider | `req-observability.md` O9 | R26 | Accepted, documented |
| Medium | 409 on duplicate name reveals an unmanaged deployment exists | `req-api.md` D2 | R25 | Accepted, documented |
| Low | No mapping of Kubernetes errors to safe messages | `req-api.md` | R24 | Specified |
| Low | OpenAPI and docs are public by default | `req-api.md` P6, `req-auth.md` A3 | R23 | Specified |
| Low | `DEPLOY_TOKEN` is visible in the process environment | `req-cli.md` | R28 | Specified |
| Low | No cap on concurrent log streams | `req-cli.md` | R21 | Specified |

## New risks introduced by the chosen fixes

| Risk | Note |
| :--- | :--- |
| D22 needs `get` on Secrets, and Kubernetes cannot grant metadata-only access | R14 scopes it to Roles in allowlisted namespaces. A compromised API pod could still read Secrets in those namespaces. |
| Per-namespace Roles do not exist in namespaces created on demand | Secret references are refused there until the chart provisions the Role (R-assumption 3). |
| R11 needs `list` on ten resource kinds in allowlisted namespaces | Wider read access than before. If too heavy, drop auto-delete of namespaces (D9). |
| Bootstrap secret (R1) is a single shared secret | Anyone holding it can mint any identity. Acceptable for dev only (R3). |

## Residual risk

With the mock provider, the API is not a real authentication boundary: whoever holds the bootstrap secret can act as anyone. The design is safe for staging and prod only with a real identity provider (Dex or Keycloak), which is documented but not built.

## Not covered

Implementation review, container image scanning, supply chain of the API's own dependencies, and cluster hardening outside this API (for example etcd encryption and node security).
