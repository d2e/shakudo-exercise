Schema
The request body must accept the following fields. You decide which are required vs.
optional and what defaults to apply:
name — deployment name (must be DNS-compatible)
namespace — target namespace. Create the namespace if it doesn’t exist.
image — container image (e.g., nginx:latest, hashicorp/http-echo:latest)
replicas — desired replica count
port — container port to expose
resources.requests.cpu and resources.requests.memory — resource requests
resources.limits.cpu and resources.limits.memory — resource limits
env — array of environment variables as key-value pairs
labels — additional labels to apply to the deployment and pod template
command and args — optional container command and arguments override

Required Endpoints

| Method | Path | Description |
| :--- | :--- | :--- |
| POST | /api/deployments | Create a new Deployment in the cluster. Accepts JSON body with workload spec (see schema above). Returns the created deployment with its UID and initial status. |
| GET | /api/deployments | List all managed deployments. Support filtering by namespace via query parameter. Return current status from the cluster, not cached data. |
| GET | /api/deployments/:id | Get detailed status for a single deployment including: replica counts (desired, ready, available, unavailable), pod-level status for each pod (name, phase, restart count, container statuses), conditions array from the Deployment status. |
| PATCH | /api/deployments/:id | Update a deployment. Must support at minimum: scaling replica count, updating the container image (triggering a rolling update). |
| DELETE | /api/deployments/:id | Delete the deployment and its associated resources from the cluster. Should handle cascade deletion of owned ReplicaSets and Pods. |
| POST | /api/deployments/:id/restart | Trigger a rollout restart (equivalent to kubectl rollout restart). This should patch the pod template annotation to force new pods. |


Part 1: Deployment API
Build a REST API service that creates and manages Kubernetes Deployments. The API
must interact with the real cluster via the Kubernetes API (using a client library such as
client-go, kubernetes Python client, or fabric8).

Required Endpoints:
Method Path Description
POST /api/deployments Create a new Deployment in the cluster. Accepts JSON
body with workload spec (see schema below). Returns
the created deployment with its UID and initial status.
GET /api/deployments List all managed deployments. Support filtering by
namespace via query parameter. Return current status
from the cluster, not cached data.

GET /api/deployments/:id Get detailed status for a single deployment including:
replica counts (desired, ready, available, unavailable),
pod-level status for each pod (name, phase, restart

count, container statuses), conditions array from the
Deployment status.

PATCH /api/deployments/:id Update a deployment. Must support at minimum: scaling
replica count, updating the container image (triggering a
rolling update).

DELETE /api/deployments/:id Delete the deployment and its associated resources
from the cluster. Should handle cascade deletion of
owned ReplicaSets and Pods.

POST /api/deployments/:id/restart Trigger a rollout restart (equivalent to kubectl rollout
restart). This should patch the pod template annotation
to force new pods.

Request Schema for POST /api/deployments:
The request body must accept the following fields. You decide which are required vs.
optional and what defaults to apply:
name — deployment name (must be DNS-compatible)
namespace — target namespace. Create the namespace if it doesn’t exist.
image — container image (e.g., nginx:latest, hashicorp/http-echo:latest)
replicas — desired replica count
port — container port to expose
resources.requests.cpu and resources.requests.memory — resource requests
resources.limits.cpu and resources.limits.memory — resource limits
env — array of environment variables as key-value pairs
labels — additional labels to apply to the deployment and pod template
command and args — optional container command and arguments override
Error Handling:
Your API should return appropriate HTTP status codes and structured error responses. At
minimum handle:
• 400 for invalid input (bad image format, negative replicas, invalid resource values, DNS-
incompatible names)
• 404 when a deployment ID doesn’t exist
• 409 when creating a deployment with a name that already exists in the same namespace
• 500 with a meaningful message when the Kubernetes API call fails (e.g., insufficient
resources, API server unreachable)

Part 2: Status &amp; Monitoring
The GET endpoints above must return real data from the Kubernetes API, not
stored/cached state. Specifically:
• Deployment-level: desired replicas, ready replicas, available replicas, unavailable
replicas, the deployment’s conditions array (Available, Progressing, etc.), creation
timestamp, last update timestamp.
• Pod-level (in detail endpoint): for each pod owned by the deployment, return: pod
name, pod phase (Pending/Running/Succeeded/Failed/Unknown), restart count, each
container’s state (waiting/running/terminated with reason), the node the pod is scheduled
on.
• Aggregated health: compute an overall status enum for each deployment: HEALTHY
(all replicas ready), DEGRADED (some but not all ready), FAILING (no replicas ready or
CrashLoopBackOff detected), PROGRESSING (rollout in progress), UNKNOWN.
Suggested test scenario: deploy nginx:latest with 3 replicas, then deploy a deliberately bad
image (e.g., nginx:nonexistent) and verify your API correctly reports the failure state
(ImagePullBackOff) at both the deployment and pod level.

Part 3: User Interface
Build either a web UI or a CLI tool. Pick whichever lets you move faster.

If web UI:
• Dashboard listing all deployments across namespaces with their aggregated health
status, displayed with color coding (green/yellow/red)
• Click into a deployment to see the detail view: replica counts, pod table with per-pod
status, and deployment conditions
• Action buttons for: scale (with input for replica count), restart, delete (with confirmation)
• Auto-refresh or polling (every 5–10 seconds) so the dashboard updates without manual
reload
• Namespace filter/selector
If CLI:
deploy create --name &lt;n&gt; --image &lt;img&gt; --namespace &lt;ns&gt; --replicas &lt;r&gt; —
creates a deployment
deploy list [--namespace &lt;ns&gt;] — table output of all deployments with status
deploy status &lt;id&gt; — detailed status including pod-level info, formatted for readability
deploy scale &lt;id&gt; --replicas &lt;r&gt; — scales the deployment
deploy restart &lt;id&gt; — triggers rollout restart
deploy delete &lt;id&gt; — deletes with confirmation prompt

deploy logs &lt;id&gt; [--pod &lt;pod-name&gt;] — stream logs from the deployment’s pods
(stretch)

Part 4: Tests
Write tests covering at minimum:
• Input validation: verify that invalid requests (bad image strings, negative replicas,
missing required fields, names with invalid characters) are rejected with correct error
codes
• Deployment lifecycle: create a deployment, verify it appears in the list, scale it, verify
the replica count changes, delete it, verify it’s gone. These can be integration tests
against your local cluster.
• Status mapping: unit test the logic that maps Kubernetes Deployment/Pod status into
your aggregated health enum (HEALTHY, DEGRADED, FAILING, etc.)
• Error scenarios: test at least one failure case (e.g., creating a deployment with a
duplicate name returns 409)


---

# Spec Index

The assessment text above is the source requirement. Details are split by category, and each file is kept under 300 lines. Where a category file differs from the text above, **the category file wins**. In particular, all `/api/...` paths above are served under `/api/v1/...` (D16).

| Category | File | Covers |
| :--- | :--- | :--- |
| API | [req-api.md](./req-api.md) | behaviour, errors, no-caching rule, RBAC impact |
| Status | [req-status.md](./req-status.md) | response fields, health rules, test scenarios |
| Auth | [req-auth.md](./req-auth.md) | JWT, mock identity, 5-minute tokens and refresh |
| CLI | [req-cli.md](./req-cli.md) | commands, login, token handling |
| UI (later) | [req-ui.md](./req-ui.md) | planned web UI, and what the API does now to keep it unblocked |
| Observability | [req-observability.md](./req-observability.md) | OTel logs and metrics, collector-ready |
| Secrets | [req-secrets.md](./req-secrets.md) | Kubernetes Secrets, never env values |
| Config | [req-config.md](./req-config.md) | cluster endpoint, connection modes, settings |
| Validation | [req-validation.md](./req-validation.md) | static input checks, field rules, 400 behaviour |
| Tests | [req-tests.md](./req-tests.md) | unit and integration tests, lifecycle, health mapping, errors |
| Pod health | [req-health.md](./req-health.md) | liveness and readiness of the API pod itself |

## Decisions

| # | Topic | Decision | File |
| :-- | :--- | :--- | :--- |
| D1 | `:id` | The Deployment Kubernetes UID | API |
| D2 | Managed scope | Only deployments the API created (managed-by label) | API |
| D3 | Auth | JWT required on every endpoint | Auth |
| D4 | Image tags | Missing tag or `latest` rejected with 400 | API |
| D5 | Health | Order: `FAILING`, `PROGRESSING`, `DEGRADED`, `HEALTHY`, `UNKNOWN` (refined in req-status.md) | Status |
| D6 | `port` | `containerPort` only, no Service | API |
| D7 | UI | CLI first, `deploy logs` is a stretch goal; web UI comes later (D15) | CLI |
| D8 | Pod security | Always PSS restricted, callers cannot relax it | API |
| D9 | Namespaces | Created if missing, deleted after DELETE if now empty | API |
| D10 | Identity | Mock identity provider, real provider documented only | Auth |
| D11 | Secrets | Stored in Kubernetes Secrets, never in env values | Secrets |
| D12 | Configuration | Cluster endpoint and connection mode are settings | Config |
| D13 | Validation | Static validation before any Kubernetes call, invalid input gives 400 | Validation |
| D14 | Tests | Unit tests (no cluster) and integration tests (local cluster), both required | Tests |
| D15 | Web UI | In scope for a later phase, nothing UI-specific built now | UI |
| D16 | Versioning | API is v1: every endpoint is under `/api/v1` (overrides the `/api/...` paths in the assessment text) | API |
| D17 | Auth order | Authentication runs before any handler: before validation and any Kubernetes call | Auth |
| D18 | Pod health | `/healthz` (liveness) and `/readyz` (readiness, checks the cluster live) for the API pod | Pod health |
| D19 | Signing key cache | Token signing keys are cached and tokens are validated against the cached key. Kubernetes data is still never cached | Auth |

Rule IDs in the category files: `N` no caching, `T` tokens, `A` auth order, `O` observability, `S` deployment status, `K` secrets, `C` config, `V` validation, `Q` tests, `P` versioning, `H` pod health, `U` UI.

## Out of scope

Web UI (a later phase, see req-ui.md), Service or Ingress creation, multi-container pods, multi-cluster, role-based authorization beyond a valid token, secret values in `env` (use Secret references, see req-secrets.md), creating Secrets through the API, tracing export.
