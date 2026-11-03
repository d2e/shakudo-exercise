# Kubernetes API Validation

Validates the Kubernetes calls the control plane relies on, against a live cluster.
Script: [`../scripts/validate_kube_api.py`](../scripts/validate_kube_api.py). Library coverage: [python-client-coverage.md](./python-client-coverage.md).

## Run

```bash
.venv/bin/python control-plane/scripts/validate_kube_api.py
```

- Uses in-cluster config, else the current kubeconfig.
- Creates `cp-validate-<random>` and always deletes it.
- Exit `0` if all pass, `1` otherwise.
- Needs admin-level access and the ability to pull `hashicorp/http-echo:1.0.0`.

## Checks (20/20 passed)

| Area | Check | Expected |
| :--- | :--- | :--- |
| Namespace | get missing | 404 |
| | ensure, then ensure again | created, then idempotent |
| | get | PSS `restricted` label |
| Validation | `dry_run="All"` | accepted, nothing stored |
| | requests > limits | 422 |
| Deployment | create, then duplicate | 201, then 409 |
| | get | all request fields match |
| Runtime | pods Ready, list by selector | 2 pods |
| | logs, follow stream | works |
| Change | scale, env patch, restart | converge |
| | stale `resourceVersion` | 409 |
| Delete | delete, then get | 404 |

## Related

- Call and error mapping: [kube-api-mapping.md](./kube-api-mapping.md)
- Caveat: ran with admin credentials, not the least-privilege ServiceAccount ([`charts/api-service/templates/rbac.yaml`](../../charts/api-service/templates/rbac.yaml), not yet applied).
