# Kubernetes Call and Error Mapping

How the control-plane API operations ([req-api.md](../spec/req-api.md)) map to Kubernetes client calls. Written during discovery, before the endpoint set was final.

## Calls

| Control-plane API | Kubernetes call |
| :--- | :--- |
| ensure namespace | `read_namespace`, then `create_namespace` (409 = success) |
| list namespaces | `list_namespace` (filtered by allowlist) |
| create deployment | ensure namespace, then `create_namespaced_deployment` |
| validate | same call with `dry_run="All"` |
| get / list deployments | `read_namespaced_deployment`, `list_namespaced_deployment` |
| update | `replace_namespaced_deployment` (needs `resourceVersion`) |
| scale | `patch_namespaced_deployment_scale` |
| restart | `patch_namespaced_deployment` (`kubectl.kubernetes.io/restartedAt`) |
| delete | `delete_namespaced_deployment` |
| deployment pods | `list_namespaced_pod(label_selector=...)` |
| pod logs | `read_namespaced_pod_log` |

## Errors

| Kubernetes | Cause | Response |
| :--- | :--- | :--- |
| 404 | missing namespace or deployment | 404 |
| 409 | already exists, or stale `resourceVersion` | 409 |
| 422 | invalid spec | 422 |
| 403 | RBAC denied | 403 |

## Pod spec that passes PSS restricted

The API applies this server-side:

- `runAsNonRoot: true` with explicit `runAsUser: 65532` (the image declares no user)
- `allowPrivilegeEscalation: false`, `readOnlyRootFilesystem: true`
- `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`
- memory `emptyDir` on `/tmp`
- TCP readiness and liveness probes
- `terminationGracePeriodSeconds: 30`
- pinned image tag, never `:latest`
