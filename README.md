# Kubernetes Architecture: Chart & Config Management Framework

A production-grade Kubernetes architecture framework designed to maintain Helm charts, multi-environment configurations, security baselines, and deployment automation.

## Start here (reviewers)

The assignment deliverable is the **control-plane deployment API**: a FastAPI service that creates and manages Kubernetes
Deployments, a `deploy` CLI for it, a Helm chart, and tests that check every API action against the real cluster.

| What | Where |
| :--- | :--- |
| Assignment text and requirements index | [`control-plane/spec/req.md`](./control-plane/spec/req.md) (decisions D1–D31, links to each `req-*.md`) |
| API contract | [`control-plane/spec/openapi.yaml`](./control-plane/spec/openapi.yaml) (generated from code; a test fails if stale) |
| API service | [`control-plane/api-service/`](./control-plane/api-service/doc/README.md) |
| CLI | [`control-plane/cli/`](./control-plane/cli/README.md) |
| Helm chart and overlays | [`charts/api-service/`](./charts/api-service/), `environments/*/api-service.yaml` |
| Try it by hand | [curl guide](./control-plane/api-service/doc/testing-the-api.md), [CLI guide](./control-plane/cli/doc/testing-the-cli.md) |

```bash
make test-api-unit                                        # API unit tests (fake cluster, no network)
cd control-plane/cli && ../../.venv/bin/pytest -q; cd -   # CLI unit tests
make test-integration                                     # live gate against a k3s cluster
make deploy-dev CHART=api-service && ./cli.sh             # deploy, then `deploy list`, `deploy logs <id>`, ...
```

The rest of this README describes the surrounding chart and configuration framework. `app/` and `charts/app` are an
earlier service, **deprecated and marked for removal** (not part of the deliverable; see [`app/README.md`](./app/README.md)).

---

## High-level architecture

The control plane is a thin path from an operator's CLI to the Kubernetes API, with every safety rule enforced on the server:

```text
   OPERATOR                      CLUSTER
 +------------+  HTTPS / WSS   +---------+     +-------------------------------------------+
 | deploy CLI |--------------->| Traefik |---->| control-plane API  (FastAPI, N replicas)  |
 |            |  Bearer token  | Ingress |     |                                           |
 |  login     |  (logs: a      | sticky  |     |  1 pipeline    authenticate, limit, audit |
 |  create    |   short-lived  | cookie  |     |  2 controllers -> services                |
 |  list      |   ticket)      +---------+     |  3 validate input, namespace allowlist    |
 |  status    |                                |  4 ClusterPort (interface)                |
 |  scale     |                                |        |                                  |
 |  restart   |                                |  KubernetesAdapter (the only SDK user)    |
 |  delete    |                                +--------+----------------------------------+
 |  logs      |                                         | ServiceAccount, least-privilege RBAC
 +------------+                                +--------v---------+    +-------------------+
                                               | Kubernetes API   |--->| Deployments,      |
                                               +------------------+    | ReplicaSets, Pods |
                                                                       | (managed by the   |
                                                                       |  API only)        |
                                                                       +-------------------+
```

- **Auth:** `deploy login` gets a short-lived token (dev identity provider); other environments validate tokens from a real
  provider. Authentication runs before any validation or cluster call.
- **Logs:** an authenticated `POST` returns a 30-second ticket, then the CLI opens a WebSocket with only that ticket
  (the long-lived token never goes in a URL), with a limit of 3 open streams per user.
- **Safety:** no cached cluster data, errors reduced to fixed messages, workloads always pod-security `restricted`,
  and only deployments created through the API are visible or changeable.
- **Testability:** the API reaches the cluster through one interface (`ClusterPort`), so the unit and end-to-end tests
  replace only the cluster and exercise everything else for real.

Configuration is layered, with the later layer winning, and secrets never enter git:

```text
 charts/<chart>/values.yaml --> environments/base.yaml --> environments/<env>/<chart>.yaml --> secrets
 (defaults + JSON schema)       (organisation baseline)    (deltas only)                       (ESO / Vault / SOPS)
         \______________  helm template / upgrade  (make lint | render-<env> | deploy-<env>)  ______________/
```

Full diagrams (components, one command, log streaming), where each rule is enforced, and a code map:
[`control-plane/docs/architecture.md`](./control-plane/docs/architecture.md).

### Sequence diagrams

[`control-plane/docs/sequences.md`](./control-plane/docs/sequences.md) (Mermaid, rendered by GitHub): login and token
refresh, an ordinary command through the pipeline, create with the order of its checks, delete with the background
namespace cleanup, log streaming (ticket, then WebSocket, with reconnect at token expiry), and how errors are shaped.

### Specs

Start with the index, [`control-plane/spec/req.md`](./control-plane/spec/req.md): the assignment, all decisions (D1 to D31)
and a link to every file below. Each file states its rules with IDs that the code, tests and diagrams refer to.

| Spec | Covers |
| :--- | :--- |
| [`req-api.md`](./control-plane/spec/req-api.md) | endpoints, errors, the no-caching rule, RBAC impact |
| [`req-validation.md`](./control-plane/spec/req-validation.md) | field rules, 400 behaviour, default resources |
| [`req-status.md`](./control-plane/spec/req-status.md) | response fields and the health rules |
| [`req-auth.md`](./control-plane/spec/req-auth.md) | JWT, mock identity, 5-minute tokens and refresh |
| [`req-security.md`](./control-plane/spec/req-security.md) | token minting, namespace scope, Secret references, TLS, limits |
| [`req-secrets.md`](./control-plane/spec/req-secrets.md) | Kubernetes Secrets, never environment values |
| [`req-config.md`](./control-plane/spec/req-config.md) | settings, cluster connection modes |
| [`req-logs.md`](./control-plane/spec/req-logs.md) | log streaming: ticket, WebSocket, limits, sticky sessions |
| [`req-cli.md`](./control-plane/spec/req-cli.md) | the `deploy` CLI: commands, auth, behaviour, tests |
| [`req-health.md`](./control-plane/spec/req-health.md) | liveness and readiness of the API pod |
| [`req-observability.md`](./control-plane/spec/req-observability.md) | logs and metrics |
| [`req-stack.md`](./control-plane/spec/req-stack.md) | FastAPI, OpenAPI contract, the cluster port and adapter, Helm packaging |
| [`req-tests.md`](./control-plane/spec/req-tests.md) | unit and integration tests |
| [`req-ui.md`](./control-plane/spec/req-ui.md) | the later web UI |
| [`review-security.md`](./control-plane/spec/review-security.md), [`review-validation.md`](./control-plane/spec/review-validation.md) | reviews behind the security and consistency fixes |
| [`openapi.yaml`](./control-plane/spec/openapi.yaml) | the generated API contract |

---

## 1. Directory Taxonomy & Architecture

```text
.
├── Makefile                        # Operational entrypoint (lint, render, deploy, test, clean)
├── README.md                       # Architectural documentation & operational runbook
├── Dockerfile                      # Multi-stage PSS-restricted non-root container build
├── pytest.ini                      # Pytest discovery configuration
├── .gitignore                      # Git exclusion rules
├── cli.sh                          # One-time setup for the `deploy` CLI (launcher, port-forward, login)
├── control-plane/                  # The assignment: deployment API, CLI, specs (see "Start here")
│   ├── spec/                       # Requirements (req.md index), reviews, openapi.yaml
│   ├── api-service/                # FastAPI service, Dockerfile, tests, docs
│   ├── cli/                        # `deploy` CLI, tests, docs
│   ├── docs/                       # Kubernetes API discovery and validation notes
│   └── scripts/                    # OpenAPI export, discovery validation scripts
├── app/                            # DEPRECATED, marked for removal: earlier OIDC + pod-listing service
│   ├── main.py                     # FastAPI routes (/healthz, OIDC discovery, pod listing)
│   ├── auth.py                     # OIDC discovery, JWKS management, JWT RS256 validation
│   ├── k8s_client.py               # Kubernetes API client (scoped namespace listing)
│   ├── models.py                   # Pydantic schemas (TokenClaims, PodSummary, etc.)
│   └── requirements.txt            # Microservice dependencies
├── tests/                          # Automated unit test suite
│   ├── test_auth.py                # OIDC & cryptographic JWT validation test cases
│   └── test_k8s.py                 # Kubernetes mock integration test cases
├── charts/                         # Reusable, version-controlled Helm charts
│   ├── api-service/                # Control-plane API chart (same layout as app/; RBAC, config, secrets mounts)
│   └── app/
│       ├── Chart.yaml              # Chart metadata (SemVer v2, appVersion)
│       ├── values.yaml             # Canonical baseline defaults (PSS restricted)
│       ├── values.schema.json      # JSONSchema validation for input variables
│       ├── .helmignore             # Build/packaging ignore rules
│       └── templates/
│           ├── _helpers.tpl        # Standardized naming & label templates
│           ├── deployment.yaml     # Hardened workload with rolling updates & probes
│           ├── service.yaml        # Standard ClusterIP service
│           ├── serviceaccount.yaml # Least-privilege ServiceAccount
│           ├── rbac.yaml           # Least-privilege Role & RoleBinding for Pod listing
│           ├── configmap.yaml      # Config injection with SHA256 checksum reload
│           ├── secret.yaml         # Secret template
│           ├── ingress.yaml        # Ingress routing (Traefik / NGINX)
│           ├── hpa.yaml            # HorizontalPodAutoscaler (CPU/Memory)
│           ├── pdb.yaml            # PodDisruptionBudget (HA safety during node maintenance)
│           ├── networkpolicy.yaml  # Default-deny / least-privilege traffic filtering
│           └── tests/
│               └── test-connection.yaml  # Post-rollout integration test hook
├── environments/                   # Hierarchical multi-environment configuration overlays
│   ├── base.yaml                   # Common cluster baselines
│   ├── dev/
│   │   ├── values.yaml             # Development overrides (1 replica, debug logs, low resources)
│   │   ├── api-service.yaml        # Same, for the api-service chart (<chart>.yaml per chart)
│   │   └── secrets.example.yaml    # Secret template for dev
│   ├── staging/
│   │   ├── values.yaml             # Staging overrides (mirrors prod scaling/testing)
│   │   └── secrets.example.yaml
│   └── prod/
│       ├── values.yaml             # Production overrides (3+ replicas, HPA, PDB, anti-affinity)
│       └── secrets.example.yaml
└── scripts/                        # Automated CI/CD & validation tooling
    ├── lint.sh                     # Chart & environment schema linting
    ├── render.sh                   # Manifest dry-run rendering per environment
    ├── deploy.sh                   # Atomic, zero-downtime rolling deployment with rollback
    ├── test.sh                     # Post-deployment validation test runner
    └── test_e2e.py                 # Live end-to-end OIDC, JWT, and RBAC verification
```

---

## 2. Core Architectural Principles

### A. Pod Security Standard (PSS) Restricted Profile
Every workload strictly abides by the Kubernetes PSS **restricted** profile:
- `runAsNonRoot: true` and non-root UID/GID (`runAsUser: 101`).
- `allowPrivilegeEscalation: false`.
- `readOnlyRootFilesystem: true` with dedicated `emptyDir` tmpfs mounts for `/tmp` and cache directories.
- `capabilities: drop: ["ALL"]`.
- `seccompProfile: type: RuntimeDefault`.

### B. Configuration Hierarchy & Value Overlays
To eliminate duplicate YAML and avoid configuration drift:
1. **`charts/app/values.yaml`**: Houses canonical structural defaults and schema definitions.
2. **`environments/base.yaml`**: Enforces organization-wide security, logging, and metadata policies.
3. **`environments/<env>/values.yaml`**: Environment-specific overrides (replicas, CPU/RAM sizing, ingress hostnames, HPA).
4. **`environments/<env>/secrets.yaml`**: Decrypted secrets at runtime (or injected via External Secrets Operator / Vault).

### C. Zero-Downtime Rollouts & Self-Healing
- **Probes**: Coordinated `readinessProbe` (prevents premature traffic), `livenessProbe` (detects deadlocks), and optional `startupProbe` (for slow-booting apps).
- **Graceful Shutdown**: `terminationGracePeriodSeconds: 30` with `SIGTERM` interception.
- **Automated Config Rollouts**: Deployment pod templates include SHA256 checksums of ConfigMaps and Secrets:
  ```yaml
  checksum/config: {{ include (print $.Template.BasePath "/configmap.yaml") . | sha256sum }}
  ```
  Modifying a ConfigMap automatically triggers an atomic rolling upgrade across pods.

### D. Availability & Blast-Radius Protection
- **PodDisruptionBudget (PDB)**: Configured in Staging and Production (`minAvailable: 2`) to ensure cluster upgrades and node drains never drop service availability.
- **Pod Anti-Affinity**: Spreads production replicas across physical host nodes (`topologyKey: kubernetes.io/hostname`).
- **Least-Privilege ServiceAccounts**: `automountServiceAccountToken: false` disables unnecessary Kubernetes API token injection into application pods.

---

## 3. Operational Commands (Developer Runbook)

All workflows are abstracted via the root [`Makefile`](./Makefile):

### Prerequisites
- **Kubernetes**: 1.28+ cluster (`k3s`, `minikube`, or cloud-managed EKS/GKE/AKS)
- **Helm**: v3.10+
- **kubectl**: Matching cluster minor version
- **Tooling**: GNU `make`, `bash` 4+

### Quick Start
```bash
# 1. Validate chart syntax and all environment overlays
make lint

# 2. Deploy to local dev environment
make deploy-dev

# 3. Verify health via integration tests
make test-dev

# 4. Check running pods and services
make status-dev
```

### Control-plane API (`charts/api-service`)
```bash
make load-api-image && make deploy-dev CHART=api-service   # build, import into k3s, install the dev release
make test-api-unit                                         # unit tests, fake cluster
make test-integration                                      # live gate: every API call confirmed against the cluster
```
CLI: run `./cli.sh` once, then `deploy --help` ([testing-the-cli.md](./control-plane/cli/doc/testing-the-cli.md)).
Hand-testing with curl and example payloads: [`control-plane/api-service/doc/testing-the-api.md`](./control-plane/api-service/doc/testing-the-api.md).

### Validation & Dry-Run
```bash
# Lint the chart and validate all environment overlays (dev, staging, prod)
make lint

# Preview rendered Kubernetes YAML for an environment without deploying
make render-dev
make render-staging
make render-prod
```

### Deployment & Testing
```bash
# Run unit tests for OIDC, JWT crypto, and Kubernetes integration
make test-unit

# Atomically deploy to dev namespace with health checks and timeout
make deploy-dev

# Run automated Helm post-deployment integration tests
make test-dev

# Run end-to-end OIDC, JWT, and RBAC pod listing tests against live cluster
make test-e2e

# Run all tests (unit + helm integration + live e2e)
make test-all

# Inspect live cluster status (Pods, Services, Ingress, PDB)
make status-dev

# Teardown dev release and delete namespace
make clean-dev
```
