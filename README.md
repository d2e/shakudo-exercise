# Kubernetes Architecture: Chart & Config Management Framework

A production-grade Kubernetes architecture framework designed to maintain Helm charts, multi-environment configurations, security baselines, and deployment automation.

---

## 1. Directory Taxonomy & Architecture

```text
.
├── Makefile                        # Operational entrypoint (lint, render, deploy, test, clean)
├── README.md                       # Architectural documentation & operational runbook
├── .gitignore                      # Git exclusion rules
├── charts/                         # Reusable, version-controlled Helm charts
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
    └── test.sh                     # Post-deployment validation test runner
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
# Atomically deploy to dev namespace with health checks and timeout
make deploy-dev

# Run automated Helm post-deployment integration tests
make test-dev

# Inspect live cluster status (Pods, Services, Ingress, PDB)
make status-dev

# Teardown dev release and delete namespace
make clean-dev
```
