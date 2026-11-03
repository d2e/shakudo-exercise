# deploy

Command line client for the control-plane API. Requirements: [`../spec/req-cli.md`](../spec/req-cli.md) and
[`../spec/req-logs.md`](../spec/req-logs.md). It talks to the API over HTTP only and never imports the API code (U6).

How it fits with the API end to end: [`../docs/architecture.md`](../docs/architecture.md).

## Install and run

```
uv pip install -e control-plane/cli      # adds the `deploy` command to the project venv
deploy --help                             # or: python -m deploy_cli (from control-plane/cli/src)
```

## Use

```
deploy login --bootstrap-secret-file ./bootstrap      # dev server only; asks for the secret if no file is given
deploy create --name http-echo --image hashicorp/http-echo:1.0.0 --namespace demo --replicas 2 --port 5678 \
              --arg=-text=hello --arg=-listen=:5678 --wait
deploy list [-n demo]
deploy status 7f3a9c1e                                # a full UID, or a unique prefix of 8 or more characters
deploy scale 7f3a9c1e --replicas 3 [--wait]
deploy restart 7f3a9c1e [--wait]
deploy delete 7f3a9c1e-1b2c-4d5e-8f60-123456789abc    # asks first; --yes in scripts; the full UID is required
deploy logs 7f3a9c1e [--pod NAME] [--tail 100] [--since 10m]
```

Options work before or after the command: `--server` (or `DEPLOY_SERVER`, default `http://localhost:8000`),
`-o json` (the API's JSON, for scripts), `--timeout`, `--debug` (method, path and status only), `--ca-file`, `--insecure`.

`create` sends only what you give. Without the four `--cpu-*`/`--memory-*` flags the server applies its default
resources (give all four or none). `--wait` (create, scale, restart, delete) polls until the deployment is `HEALTHY`
(or gone, for delete) and exits 1 if it becomes `FAILING` or after `--wait-timeout` (default 120 s).

## Authentication

Token sources, first match wins: `--token-file`, `--token-stdin`, `DEPLOY_TOKEN`, then the login stored by `deploy login`.
Only the stored login is refreshed (when 60 s or less remain). There is no `--token` flag, because a command line is
visible to other processes. After `--token-stdin`, confirmations need `--yes`.

| File | Content |
| :--- | :--- |
| `~/.config/deploy/credentials` | tokens per server; directory `0700`, file `0600`; refused if others can read it |
| `~/.config/deploy/cookies` | the load-balancer affinity cookie, sent only to the server that set it |

(`XDG_CONFIG_HOME` is honoured.) Plain `http://` is refused except to localhost, unless `--insecure`. The CLI never
follows a redirect, so the token cannot be sent to another host.

## Exit codes

`0` success, `1` the API or the network failed the request (or a declined confirmation), `2` usage error or a refused
action (for example `delete` with no terminal and no `--yes`), `3` authentication needed or failed.

## Logs

`deploy logs` asks for a short-lived ticket with the Bearer token, then opens a WebSocket with only that ticket (D28).
Lines from all pods are merged and prefixed `[pod]`; `--pod` shows one pod. When the token expires the CLI refreshes the
stored login and reconnects without repeating lines; with `DEPLOY_TOKEN` or `--token-file` it exits 3 instead. Ctrl-C ends
the stream. Control characters in log text are shown as `\xNN`, so a workload cannot rewrite your terminal.

## Tests

```
make test-cli        # unit tests and end-to-end tests, no cluster needed (about 70 s)
```

- **Unit tests** use a fake API server that checks every request and every canned response against
  `spec/openapi.yaml` (CL37). `test/logs_server.py` is a fake log API.
- **End-to-end tests** (`test/test_e2e_cli.py`, marked `e2e`) run the real CLI, in process and as the installed `deploy`
  command, against the real API app on a real port. Only the cluster is faked. They cover the lifecycle, merged and
  single-pod logs, the 4th-stream refusal, a token expiring mid-stream with a real refresh and resume, live output through a
  pipe, and Ctrl-C.
