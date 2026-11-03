> **Superseded.** This describes an earlier design (namespaced `/api/v1` paths). The current requirements are in [req.md](./req.md) and the `req-*.md` files.

# API Decisions Needing Confirmation

Where the design departs from the literal use case in [`req.md`](./req.md). Endpoints: [api.md](./api.md).

1. **`:latest` images.** The examples use `nginx:latest`, but `AGENTS.md` prohibits unpinned tags.
   Default: reject `latest` or a missing tag with 422. Alternative: allow it in dev only, behind a config flag.

2. **Namespace auto-create** needs a cluster-scoped permission.
   Default: one narrow ClusterRole, see [`charts/api-service/templates/rbac.yaml`](../../charts/api-service/templates/rbac.yaml).
   - The allowlist is checked first, and 409 AlreadyExists counts as success.
   - New namespaces get `pod-security.kubernetes.io/enforce: restricted`.
   - Trade-off: the binding is cluster-wide, so the API-level allowlist is the real boundary.

3. **Server-enforced hardening**, not caller-controlled: PSS restricted security context, `/tmp` emptyDir, `terminationGracePeriodSeconds: 30`, TCP probes when `port` is set.

4. **Secrets in `env`.** Plain key/value only. Secret values should use `secretKeyRef` in a future extension, never inline.

5. **RBAC is narrower than the API list.** The current role allows create, get and list only. APIs 4–7 (update, scale, restart, delete) need extra verbs before they work.
