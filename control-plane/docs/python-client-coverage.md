# Python Client Coverage

Library: [`kubernetes-client/python`](https://github.com/kubernetes-client/python) `37.0.1` (package `kubernetes`).
Cluster: k3s v1.36.5. Result: **20/20 checks passed**.

Run: `.venv/bin/python control-plane/scripts/validate_kube_api.py`
Details: [kube-api-validation.md](./kube-api-validation.md).

## Use case → client call

| Requirement | Call | Verified |
| :--- | :--- | :--- |
| Namespace get / create if missing | `CoreV1Api.read_namespace`, `create_namespace` (409 = success) | yes |
| name, image, port | `V1Deployment`, `V1Container`, `V1ContainerPort` | yes |
| replicas | `V1DeploymentSpec`, `patch_namespaced_deployment_scale` | yes |
| requests / limits | `V1ResourceRequirements` | yes; invalid → 422 |
| env | `V1EnvVar` | yes |
| labels (Deployment + pod template) | `V1ObjectMeta` | yes |
| command / args | `V1Container(command, args)` | yes |
| Validate only | `dry_run="All"` | yes |
| Pod logs, streaming | `read_namespaced_pod_log(follow=True, _preload_content=False)` | yes |

## Conclusion

The library covers the whole use case. No other SDK is needed.

## Caveats

- The client is **synchronous**: use plain `def` FastAPI handlers, and cap concurrent log streams (each holds a thread).
- Run with an **admin kubeconfig**. The least-privilege ServiceAccount ([`charts/api-service/templates/rbac.yaml`](../../charts/api-service/templates/rbac.yaml)) is not yet applied or tested.
- Authentication, role mapping and the HTTP layer are not covered.
