# CLI Requirements

Part of the spec set indexed in [`req.md`](./req.md) (Part 3). Requirements only.

## Decision

| # | Decision |
| :-- | :--- |
| D7 | The first UI is a **CLI**. A web UI comes later ([req-ui.md](./req-ui.md)). `deploy logs` is a stretch goal. |

## Commands

| Command | Behaviour |
| :--- | :--- |
| `deploy login` | Gets a token from the mock identity provider (D10) and stores the access and refresh tokens (file mode `0600`) |
| `deploy create --name --image --namespace --replicas` | Creates a deployment |
| `deploy list [--namespace]` | Table of deployments with health |
| `deploy status <id>` | Detail including pod-level info, formatted for readability |
| `deploy scale <id> --replicas` | Scales the deployment |
| `deploy restart <id>` | Triggers a rollout restart |
| `deploy delete <id>` | Deletes, after a confirmation prompt |
| `deploy logs <id> [--pod]` | Streams logs (stretch) |

## Behaviour

- `<id>` is the Deployment UID (D1).
- Before each command the CLI refreshes the token if 60 s or less remains. If refresh fails, it exits with a message to run `deploy login`. `DEPLOY_TOKEN` is used as given and never refreshed. See [req-auth.md](./req-auth.md) (T3, T7).
- `deploy logs` reconnects with a fresh token when the stream ends at token expiry (T8). At most 3 streams per user run at once (R21).
- **Transport (R16).** The CLI refuses an `http://` server unless the host is `localhost`, `127.0.0.1` or `::1`, or `--insecure` is given (with a warning). It never forwards the token to another host after a redirect.
- **Token input (R28).** Besides `DEPLOY_TOKEN`, the CLI accepts `--token-file` and a token on stdin. These are preferred, because an environment variable is visible to the process.
- `deploy login` sends the bootstrap secret (R1) from `--bootstrap-secret-file` or a prompt, never as a command-line argument.
- Server errors are shown as readable messages, not raw JSON or stack traces.

## Assumptions to confirm

1. The server URL comes from `--server` or `DEPLOY_SERVER`, with a local default.
2. Exit code is non-zero on any error.
3. A `--yes` flag skips the delete confirmation, for scripts and tests.
