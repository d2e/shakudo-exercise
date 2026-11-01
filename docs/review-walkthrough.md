# 30-Minute Screen-Recorded Review Walkthrough

This document serves as your scripted presentation guide for the 30-minute recorded review session (Google Meet / Fireflies). It is structured to demonstrate senior architectural competence, clear communication of trade-offs, and an interactive live cluster demo.

---

## Time Allocation Plan (30 Minutes)

| Segment | Time | Focus |
| :--- | :--- | :--- |
| **1. Introduction & Context** | 00:00 – 03:00 | Goals, scope, and technical constraints |
| **2. Architecture & Design Choices** | 03:00 – 13:00 | Configuration hierarchy, PSS security, zero-downtime rollouts |
| **3. Live Demonstration** | 13:00 – 23:00 | Linting, atomic deployment, test execution, rolling restart demo |
| **4. Production Hardening & Q&A** | 23:00 – 30:00 | Scalability, GitOps (ArgoCD), Secret management, observability |

---

## Segment 1: Introduction & Context (3 mins)

### Talking Points:
* *"In this exercise, my primary goal was establishing a scalable, maintainable Kubernetes architecture that solves two foundational platform challenges: eliminating multi-environment configuration drift, and enforcing zero-hallucination, production-ready security baselines."*
* *"The structure separates the version-controlled application chart from environment-specific configuration overlays, backed by automated validation and atomic rollouts."*

---

## Segment 2: Architecture & Design Choices (10 mins)

### Talking Points & Highlights:
1. **Directory Taxonomy (`charts/` vs `environments/`)**:
   * Show [`README.md`](file:///home/dt/project/shakudo_exercise/README.md) and [`docs/architecture.md`](file:///home/dt/project/shakudo_exercise/docs/architecture.md).
   * Explain how `charts/app/values.yaml` acts as the canonical schema contract (validated by `values.schema.json`), while `environments/base.yaml` enforces platform-wide invariants, and `environments/{dev,staging,prod}/values.yaml` supply environment deltas.

2. **Pod Security Standard (PSS) Restricted Profile**:
   * Point out the container security contexts:
     - Non-root execution (`runAsNonRoot: true`, `runAsUser: 101`).
     - Immutable filesystem (`readOnlyRootFilesystem: true`) preventing binary tampering.
     - Ephemeral writeable scratch space routed safely via `emptyDir` tmpfs mounts.
     - `capabilities: drop: ["ALL"]` to prevent kernel privilege escalation.

3. **Zero-Downtime Rollouts & Self-Healing**:
   * Highlight the automated rolling update mechanism via ConfigMap/Secret SHA256 checksums in `deployment.yaml`.
   * Explain the distinction between `startupProbe`, `readinessProbe` (traffic gating), and `livenessProbe` (deadlock recovery).
   * Discuss how `PodDisruptionBudget` protects against voluntary node evictions and cluster upgrades.

---

## Segment 3: Live Terminal Demonstration (10 mins)

Execute the live workflow step-by-step:

### Step 1: Automated Validation Across All Overlays
```bash
make lint
```
* **What to say**: *"First, `make lint` verifies our chart syntax and renders each environment overlay (`dev`, `staging`, `prod`) against the schema to catch regressions before any cluster API is touched."*

### Step 2: Atomic Deployment to the Live Cluster
```bash
make deploy-dev
```
* **What to say**: *"Next, we deploy the dev release. Notice the flags: `--wait`, `--timeout 300s`, and `--atomic`. If any pod fails its readiness probe during startup, Helm automatically rolls back the release to the previous known-good state, preventing partial or broken deployments."*

### Step 3: Run Post-Deployment Integration Tests
```bash
make test-dev
```
* **What to say**: *"We have automated test pods using the `helm.sh/hook: test` annotation. This launches an in-cluster probe container that validates service discovery, CoreDNS resolution, and HTTP health responses."*

### Step 4: Live Status & Ingress Verification
```bash
make status-dev
```
* **What to say**: *"Here we see our pod running healthy in namespace `app-dev`, backed by a ClusterIP service, active endpoints, and a Traefik ingress route."*

---

## Segment 4: Production Readiness & Next Steps (7 mins)

### Talking Points:
1. **GitOps Integration**:
   * *"In a multi-cluster enterprise setup, this repository connects directly to an ArgoCD or Flux controller. The environment folders (`environments/dev`, `environments/prod`) map to discrete ArgoCD Application manifests for continuous reconciliation."*
2. **Secret Management**:
   * *"We strictly forbid committing plain secrets. In production, we integrate with the External Secrets Operator (ESO) syncing from AWS Secrets Manager or HashiCorp Vault into native Kubernetes Secrets at runtime."*
3. **Telemetry & Observability**:
   * *"Our next milestone would be injecting Prometheus ServiceMonitors for automated metric scraping and OpenTelemetry distributed tracing headers."*
