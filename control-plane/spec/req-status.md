# Status and Monitoring Requirements

Detail for Part 2 of [`req.md`](./req.md). Follows decisions D1, D2, D5 and the no-caching rule (N1–N8). Requirements only.

## Response fields

| # | Level | Fields |
| :-- | :--- | :--- |
| S1 | Deployment (list and detail) | `uid`, `name`, `namespace`, `image`, `replicas.desired`, `.ready`, `.available`, `.unavailable`, `conditions[]` (`type`, `status`, `reason`, `message`, `lastTransitionTime`), `createdAt`, `updatedAt`, `health` |
| S2 | Pod (detail only), one per pod owned by the deployment | `name`, `phase` (`Pending`, `Running`, `Succeeded`, `Failed`, `Unknown`), `restartCount` (sum over containers), `node`, `containerStatuses[]` |
| S3 | Container status | `name`, `ready`, `restartCount`, `state` (`waiting`, `running` or `terminated`) and its `reason` (and `message` when present) |

- `updatedAt` is the latest `lastUpdateTime` among the conditions (assumption: the Deployment object has no single update timestamp).
- A pod is "owned" when its owner chain leads to a ReplicaSet owned by the deployment. Pods are found with the deployment's selector, then checked by ownership.
- Every GET reads the cluster at request time (N1–N3). `health` is computed on each call and never stored.

## Health (`health`)

Values: `HEALTHY`, `DEGRADED`, `FAILING`, `PROGRESSING`, `UNKNOWN`. Rules are evaluated in this order and the first match wins (D5).

| # | Order | Value | When |
| :-- | :--- | :--- | :--- |
| S4 | 1 | `FAILING` | `Progressing` condition has reason `ProgressDeadlineExceeded`, **or** any pod container is waiting with a failure reason (list below), **or** `desired > 0` and `ready == 0` with no rollout in progress |
| S5 | 2 | `PROGRESSING` | rollout in progress: `observedGeneration < generation`, or `updated < desired`, or `replicas > desired` (old pods still draining) |
| S6 | 3 | `DEGRADED` | `0 < ready < desired` |
| S7 | 4 | `HEALTHY` | `ready == desired`, `unavailable == 0`, no rollout in progress (including `desired == 0`) |
| S8 | 5 | `UNKNOWN` | status missing or cannot be read |

Failure reasons (assumption): `ImagePullBackOff`, `ErrImagePull`, `InvalidImageName`, `CrashLoopBackOff`, `CreateContainerConfigError`.

## Refinement of D5 that needs your confirmation

D5 says "no replicas ready" is `FAILING` and `FAILING` wins. Taken literally, every new deployment would report `FAILING` for the first seconds, while its pods are still starting. S4 therefore counts "no replicas ready" as `FAILING` **only when no rollout is in progress**. During startup it reports `PROGRESSING`. A pod stuck on a failure reason, or a missed deadline, is still `FAILING` immediately.

## Test scenarios

| # | Scenario | Expected |
| :-- | :--- | :--- |
| S9 | Fresh deployment with a pinned image, pods starting | `PROGRESSING`, then `HEALTHY` |
| S10 | 3 replicas healthy, then PATCH to a nonexistent tag | `FAILING` at deployment level; the new pod shows `waiting` / `ImagePullBackOff`; old pods still `Running` |
| S11 | 3 replicas, 1 pod deleted directly in the cluster | `DEGRADED`, then `HEALTHY` (also proves no caching, N7) |
| S12 | Container that exits repeatedly | `FAILING`, container reason `CrashLoopBackOff` |
| S13 | Unit tests of the mapping (S4–S8) | one case per rule, plus precedence cases (for example `FAILING` beats `PROGRESSING`) |

The assessment's `nginx:latest` scenario uses a pinned tag (D4) and a non-root image (D8).

## Assumptions to confirm

1. `updatedAt` as defined above.
2. The failure-reason list above.
3. The D5 refinement above.
4. Pods in `Succeeded` or `Failed` phase count as not ready, and are still shown in the pod table.
