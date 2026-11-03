# Web UI Requirements (Later Phase)

Part of the spec set indexed in [`req.md`](./req.md) (Part 3). **Not in scope now.** The CLI ([req-cli.md](./req-cli.md)) is built first. This file records what the UI will need so the API is not designed in a way that blocks it. Requirements only.

## Decision

| # | Decision |
| :-- | :--- |
| D15 | A web UI is **in scope for a later phase**, after the API and CLI. Nothing UI-specific is built now. |

## Planned UI (from the assessment)

| # | Requirement |
| :-- | :--- |
| U1 | Dashboard listing deployments across namespaces with aggregated health, color coded (green, yellow, red). |
| U2 | Detail view: replica counts, pod table with per-pod status, and deployment conditions. |
| U3 | Actions: scale (with a replica input), restart, delete (with confirmation). |
| U4 | Auto-refresh by polling every 5 to 10 seconds. |
| U5 | Namespace filter or selector. |

## What the API does now so the UI is not blocked

| # | Requirement |
| :-- | :--- |
| U6 | **The API is the only contract.** The UI uses the same endpoints as the CLI. No behaviour exists only in the CLI. |
| U7 | **Health values map cleanly to colors:** `HEALTHY` green, `PROGRESSING` and `DEGRADED` yellow, `FAILING` red, `UNKNOWN` grey (assumption). The API returns the enum, never a color. |
| U8 | **Polling-safe.** Every GET is fresh and `no-store` (N1, N6). Reads are cheap enough for 5 to 10 s polling by several users. Rate limiting must allow that rate. |
| U9 | **CORS is a setting** (allowed origins), **off by default**. The wildcard `*` with credentials in the earlier `app/main.py` (deprecated) must not be carried over (R17, tested in Q36). |
| U10 | **Token handling for browsers** (5-minute tokens, refresh) follows [req-auth.md](./req-auth.md). The browser flow itself is decided when the UI starts. |
| U11 | **Machine-readable errors** (V3) so the UI can show field-level messages. |

## Assumptions to confirm

1. UI technology is not chosen.
2. The color mapping in U7.
3. The UI is served separately from the API, with no secrets in its assets.
4. A full list endpoint across all namespaces is enough for the dashboard (no pagination needed for the exercise).
