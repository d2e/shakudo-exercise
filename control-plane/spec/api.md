> **Superseded.** This describes an earlier design (namespaced `/api/v1` paths). The current requirements are in [req.md](./req.md) and the `req-*.md` files.

# Control Plane — API List

Derived from [`req.md`](./req.md) (create a deployment from one request body).
Base path `/api/v1`. All endpoints need a Bearer JWT (reuse `app/auth.py`): 401 invalid or missing, 403 role or namespace denied.

## APIs

| # | Method and path | Purpose | Success |
| :-- | :--- | :--- | :--- |
| 1 | `POST /deployments` | **Core use case**: create deployment, creating the namespace if missing | 201 |
| 2 | `GET /namespaces/{ns}/deployments` | List; `labelSelector` filter | 200 |
| 3 | `GET /namespaces/{ns}/deployments/{name}` | Detail and rollout status | 200 |
| 4 | `PUT /namespaces/{ns}/deployments/{name}` | Update (needs `resourceVersion`, else 409) | 200 |
| 5 | `PATCH /namespaces/{ns}/deployments/{name}/scale` | Change replicas | 200 |
| 6 | `POST /namespaces/{ns}/deployments/{name}/restart` | Rollout restart | 202 |
| 7 | `DELETE /namespaces/{ns}/deployments/{name}` | Delete | 202 |
| 8 | `GET /namespaces/{ns}/deployments/{name}/pods` | Pods of the deployment | 200 |
| 9 | `POST /deployments:validate` | Dry-run, returns rendered manifest | 200 |
| 10 | `GET /namespaces`, `POST /namespaces` | List accessible; idempotent ensure | 200 / 201 |
| 11 | `GET /namespaces/{ns}/pods/{name}/logs` | Pod logs; `follow=true` streams via SSE | 200 |

Supporting: `/healthz`, `/readyz`, `/me`. Services CRUD is out of scope here.

## Roles

| Role | APIs |
| :--- | :--- |
| viewer | 2, 3, 8, 9, 10 (GET), 11 |
| operator | viewer + 1, 4, 5, 6, 7 |
| admin | operator + `POST /namespaces` |

## More

- Request schema and example: [api-schema.md](./api-schema.md)
- Deviations needing your confirmation: [api-decisions.md](./api-decisions.md)
