# AI tooling: agent definition, skill, and how they were used

This repository was built with AI coding agents. This page records which agent instructions and skills were in play,
where they live (or lived), and how the work was checked, so a reviewer can judge the result knowing how it was made.

## 1. Agent definition: [`AGENTS.md`](../../AGENTS.md)

`AGENTS.md` (repository root, 63 lines) is the standing instruction file for coding agents. Agents that follow the
`AGENTS.md` convention load it automatically at the start of a session. It defines:

| Section | Content |
| :--- | :--- |
| Role | "Senior Kubernetes Platform Architect and Pair Programmer" for this repository |
| Core operating principles | No unpinned tags (`:latest` prohibited); CPU and memory requests and limits always; Pod Security Standard **restricted** (`runAsNonRoot`, no privilege escalation, read-only root filesystem, drop all capabilities, `RuntimeDefault` seccomp); `emptyDir` for scratch paths; readiness and liveness probes; `terminationGracePeriodSeconds: 30`; checksum annotations for config rollouts; PodDisruptionBudgets; no plaintext secrets |
| Configuration hierarchy | chart defaults, `environments/base.yaml`, then per-environment overlays, with no duplication |
| Standard tooling | the Makefile is the single entry point (`make lint`, `render-*`, `deploy-*`, `test-*`, `status-*`, `clean-*`) |
| Pre-flight checklist | `make lint` before reporting done; mirror new values in `values.schema.json`; verify pods `Running` and `1/1`; run `make test-<env>`; keep every doc at or under 300 lines |

These rules are visible in the code: the API's manifest builder enforces the restricted profile server-side
([`builder.py`](../api-service/src/api_service/builder.py)), images must be pinned (validation rule D4), and every doc
except the generated `openapi.yaml` stays under the length limit.

## 2. Skill: KubeShark (`kubernetes-skill`)

| | |
| :--- | :--- |
| Name | KubeShark, `kubernetes-skill` v1.0.0 |
| Author, license | Lukas Niessen, MIT |
| Source | <https://github.com/LukasNiessen/kubernetes-skill> (install: `npx skills add https://github.com/lukasniessen/kubernetes-skill --skill kubernetes-skill`) |
| Purpose | Reduce Kubernetes hallucinations: diagnose failure modes before writing manifests |
| Based on | the official Kubernetes documentation, the NSA/CISA Kubernetes Hardening Guide, OWASP Kubernetes Top 10, Pod Security Standards, the CIS Kubernetes Benchmark |

Its workflow, run top to bottom by the agent: (1) capture the execution context (cluster version and distribution,
namespace and criticality, workload type, deployment method, policy enforcement, CNI and add-ons; state assumptions
when unknown), (2) diagnose the likely failure modes, then generate and validate against them. The six failure modes
it covers: **insecure workload defaults**, **resource starvation**, **network exposure**, **privilege sprawl**,
**fragile rollouts**, and **API drift**. It ships reference notes for each, Helm and Kustomize patterns, good and bad
examples, and conditional notes for EKS, GKE, AKS, OpenShift, GitOps controllers and observability stacks.

It was vendored at `.agents/skills/kubernetes-skill/` (75 files) and removed in `43d8d1f` to keep the repository focused
for review. To read it as it was used: `git show 8dab75f:.agents/skills/kubernetes-skill/SKILL.md`, or reinstall it
with the command above.

## 3. How the agents were used

- **Spec first.** Requirements were written and reviewed before code: [`req.md`](../spec/req.md) indexes the assignment
  text, decisions D1–D31 and one `req-*.md` per category. Two review passes ([security](../spec/review-security.md),
  [consistency](../spec/review-validation.md)) found gaps that were fixed in the specs.
- **Coding agent.** Claude Code wrote most of the code, tests and docs. Commits it created carry a trailer
  `Co-Authored-By: Claude Sonnet 5.5` or `Claude Opus 5.5`: 19 of the 24 commits on `main` at the time of writing
  (`git log --format='%h %(trailers:key=Co-Authored-By,valueonly)'`). Several agent sessions worked in parallel on the
  API, the CLI and the docs.
- **Checked against the real cluster, not trusted.** Agent output was verified by running it. Unit tests use a
  hand-written fake cluster. The integration gate ([`test/integration/`](../api-service/test/integration/)) runs the
  real app against k3s and confirms every API action with an independent Kubernetes client. The hand-testing guides
  were executed as written. This caught real defects that unit tests missed, for example a settings loader that crashed
  on any list value, a namespace cleanup that gave up before slow pods exited, and a log read timeout the Kubernetes
  client rejects.
- **Human decisions.** The developer made the product and risk calls the agents surfaced as options, for example
  namespace auto-delete under least-privilege RBAC (decision A, [`req-api.md`](../spec/req-api.md)) and what to remove
  from the repository. Actions that change the cluster or publish to GitHub were confirmed before they ran, and some
  (applying RBAC from a test, deleting a remote branch) were left to the developer.
