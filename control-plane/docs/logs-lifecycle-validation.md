# Log Streaming and Deployment Lifecycle Validation

Script: [`../scripts/validate_logs_lifecycle.py`](../scripts/validate_logs_lifecycle.py). Client: `kubernetes` 37.0.1, k3s v1.36.5.
Result: **17/17 passed**. Runs in a throwaway namespace (`cp-life-<random>`), always cleaned up.

```bash
.venv/bin/python control-plane/scripts/validate_logs_lifecycle.py
```

The test pod prints `tick N` every second, so streaming can be told apart from one buffered read.

## Log streaming

| Check | Call | Result |
| :--- | :--- | :--- |
| One-shot read | `read_namespaced_pod_log(tail_lines=)` | works |
| Timestamps, window | `timestamps=True`, `since_seconds=` | works |
| Follow, incremental | `watch.Watch().stream(read_namespaced_pod_log, follow=True)` | 4 lines over 3s, not a burst |
| Raw stream | `_preload_content=False`, `resp.stream()`, `release_conn()` | works |
| Capped stream in a thread | stop flag, then `release_conn()` | thread exits cleanly |
| Deleted pod | `read_namespaced_pod_log` | 404 |

For the API: run the stream in a worker with a hard time cap, and always call `release_conn()`.

## Deployment lifecycle

| Stage | How it is observed |
| :--- | :--- |
| Status | `Available` and `Progressing` conditions, replica counts, `observed_generation == generation` |
| Rollout | `Watch` on deployments shows each transition (new ReplicaSet scales up, old scales down) |
| History | ReplicaSets carry `deployment.kubernetes.io/revision`; one per revision |
| Events | `list_namespaced_event(field_selector=involvedObject.name=...)` shows `ScalingReplicaSet` |
| Rollback | apps/v1 has **no rollback call**: re-apply the earlier revision's pod template |
| Failed rollout | bad image gives `Progressing=False / ProgressDeadlineExceeded`; pod shows `ImagePullBackOff` |
| Delete | foreground propagation removes pods, then get returns 404 |

During the failed rollout the previous pod kept serving, so a bad deploy does not cause downtime.

## Caveats

- Set `progress_deadline_seconds` explicitly; the default is 600s before a failure is reported.
- Ran with admin credentials. The least-privilege role ([`charts/api-service/templates/rbac.yaml`](../../charts/api-service/templates/rbac.yaml)) does not yet allow `watch`, `patch`, replicasets or events; add those if these APIs are enabled.
- Related: [kube-api-validation.md](./kube-api-validation.md).
