# Kubernetes Architecture: Chart & Config Management Framework

A production-grade Kubernetes architecture framework designed to maintain Helm charts, multi-environment configurations, security baselines, and deployment automation.

---

## 1. Directory Taxonomy & Architecture

```text
.
├── Makefile                        # Operational entrypoint (lint, render, deploy, test, clean)
├── README.md                       # Architectural documentation & operational runbook
├── Dockerfile                      # Multi-stage PSS-restricted non-root container build
├── pytest.ini                      # Pytest discovery configuration
├── .gitignore                      # Git exclusion rules
├── app/                            # Hardened Python microservice
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
