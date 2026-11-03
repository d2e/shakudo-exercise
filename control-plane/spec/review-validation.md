# Consistency Review of the Requirements

Review of `req.md` and the `req-*.md` files (decisions D1–D19) for contradictions and gaps, before the security changes. Security findings are in [review-security.md](./review-security.md).

## Issues found

| # | Issue | Files | Resolution | Status |
| :-- | :--- | :--- | :--- | :--- |
| V-1 | One file says run a single replica (signing key in memory), another loads the key from a Secret, which is multi-replica safe | `req-auth.md`, `req-secrets.md` | The key comes from the Secret. The single-replica limit is removed. | Fixed |
| V-2 | `ALLOWED_NAMESPACES` was dropped, which removed the only namespace boundary | `req-config.md` assumption 3 | Restored (D21), added to the settings table | Fixed |
| V-3 | K4 says the API needs no `secrets` permission, but D22 needs to read Secret metadata | `req-secrets.md` | K4 and K9 superseded by R12–R14 | Fixed |
| V-4 | RBAC consequences did not say cluster-wide or per-namespace | `req-api.md` | Model stated: ClusterRole for deployments and namespaces; Roles in allowlisted namespaces for Secrets and the emptiness check | Fixed |
| V-5 | D9 deletes only managed namespaces, but nothing required the label on create | `req-api.md`, `req-security.md` | R10 requires it | Fixed |
| V-6 | Gaps: pagination, concurrent PATCH conflicts, request timeouts, idempotency of DELETE and restart | all | Listed as assumptions in `req-api.md` | Recorded |
| V-7 | The rule-letter list in the index did not include the new category | `req.md` | `R` added, plus `D20`–`D23` | Fixed |
| V-8 | Three superseded drafts remain untracked: `api.md`, `api-schema.md`, `api-decisions.md` | `control-plane/spec/` | Left alone, carrying a "Superseded" banner | Removed during repository cleanup |
| V-9 | A3 is default-deny, but P6 serves OpenAPI | `req-auth.md`, `req-api.md` | R23: dev only, or authenticated | Fixed |
| V-10 | "Order of checks" (A1) had no authorization step | `req-auth.md` | A1 now: authenticate, validate, allowlist (403), cluster | Fixed |
| V-11 | Refresh token life (T5, 8 hours) conflicted with the security fix | `req-auth.md` | 1 hour (R5) | Fixed |
| V-12 | "No NetworkPolicy change now" conflicts with default-deny | `req-observability.md` | R22 replaces it | Fixed |

## Consistency checks that passed

- Every category file is linked from the `req.md` index.
- Cross-references to rule IDs resolve (checked with a script, see below).
- Paths are under `/api/v1` everywhere except probes and OIDC documents (P3).
- Status codes agree across files: 400 invalid input, 401 unauthenticated, 403 namespace not allowed, 404 unknown id, 409 duplicate, 429 rate limited, 500 cluster failure.
- Cache rules agree: Kubernetes data never cached (N1–N7), signing keys cached (N8–N15).

## Still open (needs the owner)

- Numeric values chosen as assumptions (rate limits, caps, quota, 1-hour refresh).
- ~~Whether auto-delete of namespaces (D9) is worth the read access R11 needs.~~ Decided: option A. Keep D9 as designed, accept that it is inert under the chart's least-privilege RBAC, and document it.
