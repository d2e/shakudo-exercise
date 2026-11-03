# Auth Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only.

## Decisions

| # | Decision |
| :-- | :--- |
| D3 | **JWT required** on every endpoint, reusing the OIDC validation in `app/auth.py`. 401 for a missing or invalid token. |
| D10 | **Mock identity provider** (the built-in one). `POST /api/v1/auth/token` mints a JWT. A real provider (Dex, Keycloak) is the documented production path, not built. |

## Authentication happens before the call (D17)

| # | Requirement |
| :-- | :--- |
| D17 | **Authentication runs first**, before any handler logic: before body parsing, input validation, and any Kubernetes call. |

| # | Requirement |
| :-- | :--- |
| A1 | **Order of checks:** (1) authenticate, 401; (2) validate input, 400; (3) look up and act on the cluster, 404, 409, 500. An unauthenticated request never reaches steps 2 or 3. |
| A2 | **Enforced in one place**, as a global dependency or middleware, not by each handler. A new route is protected by default. |
| A3 | **Default deny.** Only an explicit public list skips authentication: `/healthz`, `/readyz`, `/api/v1/auth/token`, `/api/v1/auth/refresh`, and the OIDC discovery and JWKS documents. |
| A4 | **No information leak before login.** Without a valid token, a request to a protected path always gets 401, whether the id exists or the body is valid. 400, 404 and 409 are never seen by unauthenticated callers. |
| A5 | **401 format:** `WWW-Authenticate: Bearer`, with the expired-token hint when relevant (T2). Nothing about the token is echoed. |
| A6 | **Streams** (`deploy logs`) are authenticated when opened (T8). |
| A7 | **Clients log in first.** A client must hold a valid token before calling the API (`deploy login`, see [req-cli.md](./req-cli.md)). |
| A8 | **Tests:** every protected route returns 401 without a token (checked from the route table, so new routes are covered); an unauthenticated request with an invalid body returns 401, not 400; the same request with a valid token returns 400; no Kubernetes call is made on 401. |

## Mock identity (D10)

- The mint endpoint is open by design: anyone who can reach it can request any identity and role. The README must label it **demo-only**, and the service should be reachable only locally or on a private network.
- The signing key lives in memory, so a restart invalidates all tokens. Run a single replica, or users log in again after each restart.
- Validation stays strict (RS256, `iss`, `aud`, `exp`), so switching to a real provider needs configuration only.

## Token lifetime and refresh

| # | Requirement |
| :-- | :--- |
| T1 | **Access token lifetime is 5 minutes (300 s).** It is both the default and the maximum. A request for a longer `expires_in_seconds` is capped at 300. The `exp` claim is always enforced. |
| T2 | **Expiry is distinguishable.** An expired token returns 401 with `WWW-Authenticate: Bearer error="invalid_token", error_description="token expired"`, so a client knows to refresh rather than re-login. No token content appears in the response. |
| T3 | **Refresh before expiry.** Clients refresh proactively when 60 s or less remains, and do not wait for a 401. A refresh returns a new access token (another 300 s). |
| T4 | **Refresh token.** Login returns an access token and a refresh token. Refresh is `POST /api/v1/auth/refresh` with the refresh token, returning a new access token and a new refresh token. |
| T5 | **Refresh token lifetime** is longer than the access token (8 hours, assumption). After that, the user must log in again. |
| T6 | **Refresh tokens are signed JWTs, not stored server-side** (a `typ` claim marks them refresh-only). A refresh token must be rejected when presented as an access token, and the reverse. This keeps the service stateless. |
| T7 | **CLI behaviour** is in [req-cli.md](./req-cli.md). `DEPLOY_TOKEN` is used as-is and is never refreshed. |
| T8 | **Long operations.** A log stream is authenticated when opened and ends at the token's expiry. The client reconnects with a fresh token. |
| T9 | **Tests.** Expired token gives 401 with the expired hint. Lifetime above 300 s is capped. Refresh returns a working token. An expired or wrong-type refresh token is rejected. |

## No caching of identity data

| # | Requirement |
| :-- | :--- |
| N8 | The OIDC discovery document and signing keys (JWKS) are fetched when a token is validated, not stored. This replaces `_DISCOVERY_CACHE` and `_JWKS_CACHE` in `app/auth.py`. In mock mode the keys are local, so this costs no network call. |

## Consequences

- A refresh token cannot be revoked before it expires without server-side state (T6). Accepted for the demo; a real provider handles revocation.
- With a real provider, T4 to T6 describe the mock only. Refresh uses the provider's standard `refresh_token` grant, and each request adds one or two network calls (N8). If the provider is unreachable, requests fail rather than use stored keys.

## Assumptions to confirm

1. Refresh token lifetime is 8 hours.
2. Clock-skew leeway of 30 s when checking `exp`.
3. Refresh tokens are not single-use, because rotation needs stored state.
4. A log stream ends at token expiry rather than being extended.
5. No role-based authorization yet: any valid token may call every endpoint.
