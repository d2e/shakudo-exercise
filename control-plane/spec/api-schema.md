> **Superseded.** This describes an earlier design (namespaced `/api/v1` paths). The current requirements are in [req.md](./req.md) and the `req-*.md` files.

# Request Schema — `POST /deployments`

Same body for `PUT` (update) and `:validate`. Source: [`req.md`](./req.md). Endpoint list: [api.md](./api.md).

| Field | Type | Required | Default | Validation |
| :--- | :--- | :--- | :--- | :--- |
| `name` | string | **yes** | none | DNS-1123 label, ≤63 chars |
| `namespace` | string | **yes** | none | DNS-1123; on allowlist; created if absent |
| `image` | string | **yes** | none | explicit tag or digest, not `latest` |
| `replicas` | int | no | 1 | 0 to `MAX_REPLICAS` (20) |
| `port` | int | no | none | 1–65535, becomes `containerPort` |
| `resources.requests.cpu/memory` | quantity | **yes** | none | `100m`, `128Mi` |
| `resources.limits.cpu/memory` | quantity | **yes** | none | at least the requests |
| `env` | `[{name,value}]` | no | `[]` | valid names, max 50, no duplicates |
| `labels` | map | no | `{}` | on Deployment and pod template; reserved keys protected |
| `command` | `[string]` | no | image ENTRYPOINT | override |
| `args` | `[string]` | no | image CMD | override |

Resources are required because `AGENTS.md` forbids pods without requests and limits.

## Example

```json
{
  "name": "http-echo", "namespace": "demo",
  "image": "hashicorp/http-echo:1.0.0", "replicas": 2, "port": 5678,
  "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                "limits":   {"cpu": "200m", "memory": "128Mi"}},
  "env": [{"name": "LOG_LEVEL", "value": "info"}],
  "labels": {"team": "platform"},
  "command": ["/http-echo"], "args": ["-text=hello", "-listen=:5678"]
}
```

## Response

`201`: `{name, namespace, namespace_created, replicas, image, resourceVersion}`.
Errors: `409` exists (use PUT), `422` validation, `403` denied, `502/504` upstream.
