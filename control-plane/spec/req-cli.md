# CLI Requirements

Part of the spec set indexed in [`req.md`](./req.md) (Part 3). Requirements only. This is the only CLI document: commands, behaviour, tests and open decisions are all here.

The CLI is a thin client over the API in [req-api.md](./req-api.md). It uses HTTP only and adds no behaviour the API lacks (U6).

**Status:** built in [`../cli/`](../cli/README.md); `make test-cli` runs its tests.

## Decision

| # | Decision |
| :-- | :--- |
| D7 | The first UI is a **CLI**, named `deploy`. A web UI comes later ([req-ui.md](./req-ui.md)). `deploy logs` is a stretch goal. |
| D28 | `deploy logs` streams over WebSocket, authenticated over HTTP first with a short-lived ticket. All log rules are in [req-logs.md](./req-logs.md). |

Items marked **(proposed)** are defaults that were not in the original spec and still need confirmation (OD5).

## Commands

| Command | API call | Behaviour |
| :--- | :--- | :--- |
| `deploy login [--user]` | `POST /api/v1/auth/token` | Gets a token for the given user from the mock identity provider (D10), stores access and refresh tokens (file mode `0600`). Dev only (D20). |
| `deploy create --name --image --namespace [--replicas]` | `POST /api/v1/deployments` | Creates a deployment; prints its UID. `--replicas` defaults to 1. |
| `deploy list [--namespace]` | `GET /api/v1/deployments` | Table of deployments with health. |
| `deploy status <id>` | `GET /api/v1/deployments/{id}` | Detail with pod-level info, formatted for readability. |
| `deploy scale <id> --replicas` | `PATCH /api/v1/deployments/{id}` | Scales the deployment. |
| `deploy restart <id>` | `POST /api/v1/deployments/{id}/restart` | Triggers a rollout restart. |
| `deploy delete <id>` | `DELETE /api/v1/deployments/{id}` | Deletes, after a confirmation prompt. |
| `deploy logs <id> [--pod]` | `POST /api/v1/deployments/{id}/logs/ticket`, then WebSocket `GET /api/v1/logs/stream` (not built) | Streams logs from the deployment's pods (stretch). Authenticated over HTTP first, then upgraded (D28, [req-logs.md](./req-logs.md)). |

`<id>` is the Deployment UID (D1).

## General rules

| # | Requirement |
| :-- | :--- |
| CL1 | **Server.** `--server` or `DEPLOY_SERVER`; default `http://localhost:8000`. |
| CL2 | **Exit codes.** `0` success, `1` the API or the network failed the request, `2` usage error or a refused action (bad flags, delete without confirmation), `3` authentication needed or failed. |
| CL3 | **Output.** Plain text tables and sections. Colour only on a terminal and never when `NO_COLOR` is set. `-o json` prints the API's JSON unchanged, for scripts. |
| CL4 | **Errors are readable, never raw.** No JSON dump, no stack trace. Show the API's `error.message`, each entry of `error.details[]` (one per line, 400 reports all field errors together), and the `request_id`. |
| CL5 | **Error mapping.** 401: "run `deploy login`", exit 3. 403: namespace not allowed. 404: not found (also for unmanaged deployments, D2). 409: name already in use. 429: show `Retry-After`, exit 1. 500: the API's sanitized message (R24). |
| CL6 | **No unsafe retries.** `POST`, `PATCH` and `DELETE` are never retried automatically, because a retry after a timeout would return a confusing 409 or 404. `GET` may retry once on a connection error. |
| CL7 | **Timeouts.** Each request times out after 10 s (`--timeout`). Log streams have no overall timeout. |
| CL8 | **`--debug`** prints method, path and status of each request. Never headers, tokens, secrets or bodies. |

## Authentication and transport

| # | Requirement |
| :-- | :--- |
| CL9 | **Token sources**, first match wins: `--token-file`, stdin (`--token-stdin`), `DEPLOY_TOKEN`, stored credentials. The first two are preferred because an environment variable is visible to the process (R28). |
| CL10 | **Refresh.** Before each command the CLI refreshes a stored token if 60 s or less remains. If refresh fails, it exits 3 with a message to run `deploy login`. `DEPLOY_TOKEN` and `--token-file` tokens are used as given and never refreshed (T3, T7). |
| CL11 | **Stored credentials** live in `~/.config/deploy/credentials` (directory `0700`, file `0600`; `XDG_CONFIG_HOME` is honoured). The CLI refuses to read a file others can read. |
| CL12 | **Tokens never appear** in command-line arguments, output, `--debug` output, or error messages. |
| CL13 | **Login** sends the bootstrap secret (R1) from `--bootstrap-secret-file` or a prompt, never as an argument. `--user <name>` is the token subject (`sub`); it defaults to the operating-system user. It must match `^[A-Za-z0-9_.@:-]+$` and be at most 128 characters; if the default does not, the CLI exits 2 and asks for `--user`. The subject is the identity the API logs and rate-limits per user (R18), and the mock provider does not verify it. Outside dev the API has no token endpoint (D20), so `deploy login` reports that and points to CL9. |
| CL14 | **Transport (R16).** The CLI refuses an `http://` server unless the host is `localhost`, `127.0.0.1` or `::1`, or `--insecure` is given (with a warning). It never forwards the token to another host after a redirect. TLS is verified by default; `--ca-file` adds a CA. |
| CL38 | **Affinity cookie (D29).** The CLI stores the cookie the server sets (the affinity cookie) in `~/.config/deploy/cookies` (file `0600`, same directory rules as CL11), per server host, and sends it on every request to that host, including the log WebSocket upgrade. One user's commands then reach one API replica. The cookie is never sent to another host. Deleting the file only loses affinity. |

## Command behaviour

### Identifying a deployment

| # | Requirement |
| :-- | :--- |
| CL15 | `list` always prints the **full UID**, so it can be copied into the next command. |
| CL16 | **Prefix lookup (proposed).** `status`, `scale` and `restart` accept a unique UID prefix of 8 or more characters, resolved with `GET /api/v1/deployments`. An ambiguous or unknown prefix is an error that lists the matches. The API still takes full UIDs only. |
| CL17 | `delete` requires the **full UID**, never a prefix. |

### create

| # | Requirement |
| :-- | :--- |
| CL18 | `--name`, `--image` and `--namespace` are required. The CLI does no validation beyond presence. The server's rules (V) are the single source, and its 400 details are shown (CL4). |
| CL19 | **Optional flags** for the rest of the API schema: `--port`, `--env NAME=VALUE` (repeatable), `--secret-env NAME=SECRET/KEY` (repeatable, a Secret reference, never a value, K7, K8), `--label KEY=VALUE` (repeatable), `--command` and `--arg` (repeatable). |
| CL20 | **Resources (D31).** `resources` is optional, so `deploy create` works with only the required flags and the API applies its defaults. The CLI offers `--cpu-request`, `--memory-request`, `--cpu-limit` and `--memory-limit`. They are all-or-nothing: giving some but not all is a usage error (exit 2), because the API accepts only complete `resources`. The CLI has no defaults of its own (U6). |
| CL21 | **Output:** UID, name, namespace, health, "namespace created" when the API reports it, and "default resources applied" when `resourcesDefaulted` is true. The 400 for a `latest` or missing tag is shown as is (D4). |

### list

| # | Requirement |
| :-- | :--- |
| CL22 | Columns: `NAMESPACE`, `NAME`, `UID`, `READY` (ready/desired), `HEALTH`, `IMAGE`, `AGE`. Sorted by namespace, then name. An empty result prints "No deployments found." and exits 0. |
| CL23 | `--help` says that only deployments created through this API, in allowed namespaces, are listed (D2, D21). |

### status

| # | Requirement |
| :-- | :--- |
| CL24 | Sections in order: **summary** (name, namespace, UID, image, health, age), **replicas** (desired, ready, available, unavailable), **conditions** (type, status, reason, message), **pods** (name, phase, restarts, node) with each container's state, reason and message on the lines below. |
| CL25 | When health is `FAILING` or `DEGRADED`, a **"Why"** line shows the first failing reason, for example `ImagePullBackOff` from a container or `ProgressDeadlineExceeded` from a condition (S4–S8). Events are not shown, because the role does not grant them (accepted). |

### scale, restart, delete

| # | Requirement |
| :-- | :--- |
| CL26 | `scale` needs `--replicas` (an integer, 0 or more). The server enforces the cap (R19). Scaling to **0** asks for confirmation like delete, since it stops the service. |
| CL27 | **Delete confirmation.** The CLI first reads the deployment (`GET`) and asks: `Delete <name> in <namespace> (<uid>)? Its pods are removed, and an empty namespace the API created is removed too. [y/N]`. The default is no. `--yes` skips the prompt. With no terminal and no `--yes`, it exits 2 and sends no request at all. Answering anything but `y` or `yes` prints "Aborted." and exits 1. |
| CL28 | Delete is asynchronous (202). The CLI prints "deleting", not "deleted". |
| CL29 | **`--wait` (proposed)** for `create`, `scale`, `restart` and `delete`: poll `GET` every 2 s, up to `--wait-timeout` (default 120 s). The first three end at `HEALTHY` (exit 0), or early at `FAILING` (exit 1). `delete` ends at 404. |

### logs (stretch)

| # | Requirement |
| :-- | :--- |
| CL30 | **Flow (D28).** Request a ticket with the Bearer token, then open the WebSocket with the scheme swapped (`https` to `wss`, `http` to `ws`) and offer `logs.v1` and `ticket.<value>` as subprotocols. The ticket is never put in the URL. Frames are printed as received. Streams to stdout until interrupted; Ctrl-C closes the stream and exits 0. |
| CL31 | `--pod` names one pod of the deployment, sent in the ticket request. The API checks ownership, so another deployment's pod is 404. Without `--pod`, all the deployment's pods are shown in one stream, each line prefixed `[pod-name]` (D30). |
| CL32 | Options: `--tail N` (default 100, sent as `tailLines`) and `--since DURATION` (sent as `sinceTime`). |
| CL33 | **Token expiry (T8).** The server closes the stream with code `4001` at the expiry of the token that requested the ticket. With a stored token the CLI refreshes, requests a new ticket with `sinceTime` set to the oldest per-pod position (tracked per pod, because lines from different pods arrive out of time order), and reconnects, so lines are not repeated. With `DEPLOY_TOKEN` or `--token-file` it cannot refresh, so it prints a message and exits 3. Close code `1001` (server restarting) is handled the same way. |
| CL34 | A fourth concurrent stream for one user is refused with 429 at the ticket request, or at the upgrade (R21, LG11). The CLI shows that message and exits 1. Other close codes (`1013` too slow, `1011` cluster failure) print the fixed message and exit 1. |

## Build rules

| # | Requirement |
| :-- | :--- |
| CL35 | **Location and entry point.** `control-plane/cli/`, Python package `deploy_cli`, console script `deploy` (F7). Tests in `control-plane/cli/test`, run by `make test-cli`. |
| CL36 | **Dependencies (proposed).** `argparse` from the standard library and `requests`, which is already pinned. `deploy logs` adds a WebSocket client library; `websocket-client` is proposed because the CLI is synchronous. Any addition is pinned exactly (F6) and has its own requirements file. |
| CL37 | **API only over HTTP (U6).** The CLI never imports `api_service`. Request bodies and canned responses in its tests are checked against the committed [`openapi.yaml`](./openapi.yaml), so a contract change breaks a CLI test. |

## Tests

Unit tests use a local fake HTTP server and need no cluster (Q1). The `http://` refusal (Q35) and the fourth stream (Q41) are also in [req-tests.md](./req-tests.md).

| # | Test | Expected |
| :-- | :--- | :--- |
| CT1 | Each command against the fake server | Right method, path and body (CL1, command table) |
| CT2 | `create` with only the required flags, with all four resource flags, with only some of them, and with every optional flag | Body has no `resources` for the first; complete `resources` for the second; usage error, exit 2, no request for the third; every body matches the API schema and the OpenAPI file (CL18–CL20, CL37) |
| CT3 | `list`: rows, empty result, `--namespace`, `-o json` | Columns and order (CL22); empty message; JSON equals the API's |
| CT4 | `status` for a `FAILING` deployment with an `ImagePullBackOff` pod | All sections and the "Why" line (CL24, CL25) |
| CT5 | `delete`: answer `n`, answer `y`, `--yes`, no terminal without `--yes` | No call; `DELETE`; `DELETE`; exit 2 and no call (CL27) |
| CT6 | Prefix: unique, ambiguous, unknown, `delete` with a prefix | Resolved; error lists matches; error; refused (CL16, CL17) |
| CT7 | Token sources in order; token search in argv, stdout, `--debug` and errors | Order holds; token never present (CL9, CL12) |
| CT8 | Stored token with 60 s left; refresh failure; `DEPLOY_TOKEN` near expiry | Refreshed; exit 3 with login hint; not refreshed (CL10) |
| CT9 | Credentials file modes; group-readable file | `0700` and `0600`; refused (CL11) |
| CT10 | `http://` non-local server; `--insecure`; redirect to another host | Refused; warning; token not sent to the other host (CL14, R16) |
| CT11 | 400 with several details, 401, 403, 404, 409, 429 with `Retry-After`, 500 | Readable text, `request_id` shown, no raw JSON, right exit code (CL2, CL4, CL5) |
| CT12 | `POST` that times out | Exactly one request sent (CL6) |
| CT13 | `--wait` for create (to `HEALTHY`, to `FAILING`), and for delete | Exit 0; exit 1 early; ends at 404 (CL29) |
| CT14 | `scale --replicas 0` | Confirmation asked (CL26) |
| CT15 | `logs`: ticket request then upgrade, prefixes, `--pod`, reconnect after close `4001` with `sinceTime`, `DEPLOY_TOKEN` at expiry, Ctrl-C, ticket never in the URL or output | Frames shown with pod prefix; one pod; no repeated lines; exit 3; exit 0; no ticket anywhere (CL30–CL34, LT6, LT10) |
| CT16 | Integration (marked `integration`, Q2): create, list, status, scale, restart, delete against the real API and local cluster | Reaches `HEALTHY`; delete ends at 404; test namespaces cleaned up |
| CT17 | `login` with and without `--user`; default user that is not a valid subject | Body `sub` is the given or default user; exit 2 with a hint (CL13) |
| CT18 | Server sets an affinity cookie; run two commands, then a ticket request and the upgrade | Cookie stored `0600`; sent on later runs and on the upgrade; not sent to another host (CL38) |

## Open decisions and gaps

These came from checking the commands against the API as built. OD1 (log transport), OD2 (merged log streams) and OD3 (create resources) are resolved by D28, D30 and D31. The two that remain do not block anything.

| # | Question | Proposed default | Needs |
| :-- | :--- | :--- | :--- |
| OD4 | **Login outside dev.** The mock identity is refused outside dev. A real provider is documented only (D10). | Out of scope. Outside dev a token comes from CL9. A device-code login is a later phase. | Nothing now |
| OD5 | **Defaults not in the original spec**: prefix lookup (CL16), `--wait` (CL29), the confirmation for scaling to 0 (CL26), the `--user` default (CL13), `-o json`, file path and timeouts. | As written above. | Confirmation |

Accepted gaps: no pagination (`list` returns everything), no events in `status`, `list` shows only API-created deployments, and `delete` of a namespace that the API did not create leaves the namespace in place (D9).
