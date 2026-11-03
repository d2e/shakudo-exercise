# Auth Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only.

## Decisions

| # | Decision |
| :-- | :--- |
| D3 | **JWT required** on every endpoint, modelled on the OIDC validation of the earlier `app/auth.py` (deprecated; the API has its own `auth/`). 401 for a missing or invalid token. |
| D10 | **Mock identity provider** (the built-in one). `POST /api/v1/auth/token` mints a JWT, and requires a bootstrap secret (D20, [req-security.md](./req-security.md)). A real provider (Dex, Keycloak) is the documented production path, not built. |

## Authentication happens before the call (D17)

| # | Requirement |
| :-- | :--- |
| D17 | **Authentication runs first**, before any handler logic: before body parsing, input validation, and any Kubernetes call. |

| # | Requirement |
| :-- | :--- |
| A1 | **Order of checks:** (1) authenticate, 401; (2) validate input, 400; (3) check the namespace allowlist, 403 (R7); (4) look up and act on the cluster, 404, 409, 500. An unauthenticated request never reaches steps 2 to 4. |
| A2 | **Enforced in one place**, as a global dependency or middleware, not by each handler. A new route is protected by default. |
| A3 | **Default deny.** Only an explicit public list skips authentication: `/healthz`, `/readyz`, `/api/v1/auth/token`, `/api/v1/auth/refresh`, and the OIDC discovery and JWKS documents. The token endpoint is public only in the sense that it needs no Bearer token: it still requires the bootstrap secret (R1). OpenAPI and docs are **not** on this list (R23). One more route is not Bearer-authenticated: the log WebSocket `/api/v1/logs/stream`, which accepts only a short-lived ticket obtained with a Bearer token (D28, [req-logs.md](./req-logs.md)). |
| A4 | **No information leak before login.** Without a valid token, a request to a protected path always gets 401, whether the id exists or the body is valid. 400, 404 and 409 are never seen by unauthenticated callers. |
| A5 | **401 format:** `WWW-Authenticate: Bearer`, with the expired-token hint when relevant (T2). Nothing about the token is echoed. |
| A6 | **Streams** (`deploy logs`) are authenticated over HTTP first: a Bearer-authenticated ticket request, then a WebSocket opened with the ticket (D28, [req-logs.md](./req-logs.md)). |
| A7 | **Clients log in first.** A client must hold a valid token before calling the API (`deploy login`, see [req-cli.md](./req-cli.md)). |
| A8 | **Tests:** every protected route returns 401 without a token (checked from the route table, so new routes are covered); an unauthenticated request with an invalid body returns 401, not 400; the same request with a valid token returns 400; no Kubernetes call is made on 401. |

## Mock identity (D10)

- The mint endpoint is protected by a **bootstrap secret** (R1, R2) and refused outside dev (R3). Anyone holding the secret can still request any identity and role, so the README must label it **demo-only**.
- The signing key is loaded from a Kubernetes Secret (K1), not generated in memory. Tokens survive a restart, and more than one replica can validate them. There is no single-replica limit.
- Validation stays strict (RS256, `iss`, `aud`, `exp`), so switching to a real provider needs configuration only.

## Token lifetime and refresh

| # | Requirement |
| :-- | :--- |
| T1 | **Access token lifetime is 5 minutes (300 s).** It is both the default and the maximum. A request for a longer `expires_in_seconds` is capped at 300. The `exp` claim is always enforced. |
| T2 | **Expiry is distinguishable.** An expired token returns 401 with `WWW-Authenticate: Bearer error="invalid_token", error_description="token expired"`, so a client knows to refresh rather than re-login. No token content appears in the response. |
| T3 | **Refresh before expiry.** Clients refresh proactively when 60 s or less remains, and do not wait for a 401. A refresh returns a new access token (another 300 s). |
| T4 | **Refresh token.** Login returns an access token and a refresh token. Refresh is `POST /api/v1/auth/refresh` with the refresh token, returning a new access token and a new refresh token. |
| T5 | **Refresh token lifetime** is longer than the access token: **1 hour** (R5; was 8 hours). After that, the user must log in again. |
| T6 | **Refresh tokens are signed JWTs, not stored server-side** (a `typ` claim marks them refresh-only). A refresh token must be rejected when presented as an access token, and the reverse. This keeps the service stateless. |
| T7 | **CLI behaviour** is in [req-cli.md](./req-cli.md). `DEPLOY_TOKEN` is used as-is and is never refreshed. |
| T8 | **Long operations.** A log stream ends at the expiry of the access token that requested its ticket (close code `4001`). The client requests a new ticket with a fresh token and reconnects. |
| T9 | **Tests.** Expired token gives 401 with the expired hint. Lifetime above 300 s is capped. Refresh returns a working token. An expired or wrong-type refresh token is rejected. |

## Signing key cache (D19)

| # | Requirement |
| :-- | :--- |
| D19 | The **token signing certificate (public key) is cached**, and each token is validated against the cached key. This **replaces** the earlier rule that identity data is never cached (old N8). |

| # | Requirement |
| :-- | :--- |
| N8 | The signing keys (JWKS) and the OIDC discovery document are cached in memory, per pod. Most requests make no call to the identity provider. Only public keys are cached, never tokens. In mock mode the keys are local. |
| N9 | **Validation against the cached key:** pick the key by `kid`; accept `RS256` only; verify the signature, then `iss`, `aud`, `exp` and `nbf`. A token with no `kid`, or with no matching key, gets 401. There is no fallback to the first key or to a local key. |
| N10 | **Expiry of the cache.** Cached keys are refetched after a TTL (`JWKS_CACHE_TTL_SECONDS`, default 600, assumption). |
| N11 | **Key rotation.** An unknown `kid` triggers one immediate refetch and a retry. Refetches are rate-limited (at least 30 s apart, assumption) so random `kid` values cannot flood the provider. |
| N12 | **Provider outage.** Cached keys keep working for a maximum stale age (1 hour, assumption), then validation fails closed with 401. Fetch failures are logged without secrets. |
| N13 | **Single fetch at a time.** Concurrent requests that need a refetch share one fetch. |
| N14 | **Scope.** This applies only to identity keys. Kubernetes data is still never cached ([req-api.md](./req-api.md), N1–N7). |
| N15 | **Tests:** many requests cause one fetch; an unknown `kid` causes one refetch; repeated random `kid` values are rate-limited; a TTL expiry refetches; a provider outage works within the stale window and fails closed after it; a token signed by another key, `alg: none` and HS256 are rejected. |

## Consequences

- A refresh token cannot be revoked before it expires without server-side state (T6). Accepted for the demo; a real provider handles revocation.
- With a real provider, T4 to T6 describe the mock only. Refresh uses the provider's standard `refresh_token` grant.
- A key removed at the provider stays trusted until the cache TTL passes (up to 10 minutes), or until a restart. This is the cost of caching, and it is accepted.
- The existing code already caches keys forever with no TTL or rotation handling, and falls back to the first key when `kid` is missing. N9 to N12 fix both.

## Assumptions to confirm

1. Refresh token lifetime is 1 hour.
2. Clock-skew leeway of 30 s when checking `exp`.
3. Refresh tokens are not single-use, because rotation needs stored state.
4. A log stream ends at token expiry rather than being extended.
5. No role-based authorization yet: any valid token may call every endpoint.
6. Cache values: TTL 10 minutes, refetch cooldown 30 s, maximum stale age 1 hour.
7. The discovery document is cached together with the keys.
