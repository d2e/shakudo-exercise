# Test Requirements

Part of the spec set indexed in [`req.md`](./req.md) (Part 4). Requirements only. Input validation tests are in [req-validation.md](./req-validation.md) (V21–V24).

## Decision

| # | Decision |
| :-- | :--- |
| D14 | Tests have two levels: **unit** (no cluster) and **integration** (real local cluster). Both are required. |

## Test levels

| # | Requirement |
| :-- | :--- |
| Q1 | **Unit tests** need no cluster. They use a fake Kubernetes client and run on every change. |
| Q2 | **Integration tests** run against the local k3s cluster, marked `integration`. If the cluster is unreachable they are skipped with a clear message, not failed. |
| Q3 | **Isolation.** Each run uses a unique namespace (`cp-test-<random>`), created through the API itself (D9), and removed at the end **even when a test fails**. Tests never touch other namespaces. |
| Q4 | **Images** are pinned and run non-root (D4, D8), for example `hashicorp/http-echo:1.0.0`. |
| Q5 | **No fixed sleeps.** Tests poll the API with a timeout (90 s, assumption) and report the last status on timeout. |
| Q6 | **Authentication in tests.** Integration tests obtain a token from the mock provider (D10). |

## Deployment lifecycle (integration)

One test, in this order, each step checked against the live cluster:

| # | Step | Check |
| :-- | :--- | :--- |
| Q7 | Create | 201, response has `uid` and initial status |
| Q8 | List | the deployment appears in `GET /api/v1/deployments?namespace=` |
| Q9 | Detail | replica counts, pod table and conditions are present |
| Q10 | Scale | PATCH `replicas`; the replica count changes in GET, and pods reach ready |
| Q11 | Update image | PATCH `image`; a rolling update completes and the new image is shown |
| Q12 | Restart | POST restart; new pods replace the old ones |
| Q13 | Delete | 202; GET gives 404; the list no longer shows it; no ReplicaSets or Pods remain (cascade); an empty namespace is removed (D9) |

## Status mapping (unit)

| # | Requirement |
| :-- | :--- |
| Q14 | The function that turns Deployment and Pod status into `health` is **pure** and tested with fixtures, no cluster. |
| Q15 | One case per health rule (S4–S8), plus precedence cases such as `FAILING` over `PROGRESSING`. |
| Q16 | Edge cases: desired 0 is `HEALTHY`; missing status is `UNKNOWN`; partial readiness is `DEGRADED`; startup with no ready pods is `PROGRESSING`, not `FAILING`. |
| Q17 | Pod level: container `waiting` and `terminated` reasons (`ImagePullBackOff`, `CrashLoopBackOff`, `CreateContainerConfigError`), restart count sum, and node are mapped correctly. |

## Live status scenarios (integration)

| # | Scenario | Check |
| :-- | :--- | :--- |
| Q18 | 3 replicas healthy, then PATCH to a nonexistent tag | `FAILING` at deployment level, new pod `ImagePullBackOff`, old pods still running (S10) |
| Q19 | Delete one pod directly in the cluster | next GET shows `DEGRADED` then `HEALTHY`, proving no caching (S11, N7) |

## Error scenarios

| # | Case | Expected |
| :-- | :--- | :--- |
| Q20 | Create with an existing name in the same namespace | 409 |
| Q21 | GET, PATCH or DELETE with a well-formed unknown id | 404 |
| Q22 | Malformed id | 400 |
| Q23 | No token, expired token, tampered token | 401 (expired carries the hint, T2) |
| Q24 | Cluster unreachable (bad endpoint through configuration, C2) | 500 with a meaningful message and no internal details |
| Q25 | Invalid input | 400, and the Kubernetes client is not called (V22) |
| Q26 | Deployment not created by the API (no managed label) | 404, and it is never modified (D2) |

## Security tests

Rules are in [req-security.md](./req-security.md). Tests that need a cluster are integration tests; the rest are unit tests.

| # | Case | Expected |
| :-- | :--- | :--- |
| Q29 | Mint a token without the bootstrap secret, with a wrong one, and with the right one | 401, 401, 200; the secret never appears in logs (R1, R2) |
| Q30 | Start with `MOCK_AUTH_ENABLED=true` in a non-dev environment | startup fails (R3) |
| Q31 | Empty allowlist; namespace outside the list; reserved namespace that is listed | all 403, and no Kubernetes call (R6–R8) |
| Q32 | Namespace delete when it is not managed, not allowlisted, not empty, or the check fails | the namespace is not deleted (R11) |
| Q33 | Secret reference to a missing Secret, and to an unlabeled one | the same 400 `secret_not_allowed` for both (R12) |
| Q34 | Exceed a rate limit | 429 with `Retry-After` (R18) |
| Q35 | CLI with an `http://` non-local server | refused unless `--insecure` (R16) |
| Q36 | CORS: default config, and a request with `Origin` | no CORS headers by default; wildcard with credentials never produced (R17) |
| Q37 | Force a Kubernetes failure | the response has only the fixed message and a `request_id`, no hostnames or raw text (R24) |
| Q38 | Render the chart | NetworkPolicy is default-deny with only the allowed egress (R22) |
| Q39 | Request `/docs` and `/openapi.json` outside dev | 404, or 401 without a token (R23) |
| Q40 | Render the chart with an enabled ingress and no TLS outside dev | schema rejects it (R15) |
| Q41 | Open a fourth log stream for one user | ticket request and upgrade refused (R21, LG11) |

## Running tests

| # | Requirement |
| :-- | :--- |
| Q27 | The Makefile stays the single entry point (`AGENTS.md`): `make test-unit` for unit tests and a new `make test-integration` for the cluster tests. |
| Q28 | CI runs unit tests always. Integration tests run only where a cluster is available. |

## Assumptions to confirm

1. pytest, as the repo already uses (`pytest.ini`).
2. A 90 s polling timeout per wait.
3. The unit-test fake client is hand-written fixtures, not a mocking library for Kubernetes.
4. Integration tests may take a few minutes (image pulls), and that is acceptable.
5. No coverage percentage is required.
