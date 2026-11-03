"""The commands (CL15-CL34). Each takes an App (context, parsed arguments, client) and returns an exit code."""
import re
from dataclasses import dataclass
from typing import Optional

from . import render
from .auth import entry_from_token_response
from .client import ApiClient
from .context import Context
from .errors import EXIT_AUTH, EXIT_FAIL, EXIT_OK, EXIT_USAGE, CliError
from .store import Credentials

BASE = "/api/v1/deployments"
FULL_UID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
UID_PREFIX = re.compile(r"^[0-9a-f][0-9a-f-]{7,34}$")
SUBJECT = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
POLL_SECONDS = 2


@dataclass
class App:
    ctx: Context
    args: object
    client: Optional[ApiClient] = None
    creds: Optional[Credentials] = None


def _show(app: App, data: dict, text_lines: list[str]) -> None:
    """CL3: -o json prints the API's JSON; otherwise the text."""
    if app.args.output == "json":
        app.ctx.out(render.as_json(data))
    else:
        for line in text_lines:
            app.ctx.out(line)


# ---- identifying a deployment (CL15-CL17) ----
def resolve_id(app: App, ident: str, allow_prefix: bool = True) -> str:
    s = ident.strip().lower()
    if FULL_UID.match(s):
        return s
    if not allow_prefix:
        raise CliError("delete needs the full UID (36 characters, as printed by `deploy list`)", EXIT_USAGE)
    if not UID_PREFIX.match(s) or len(s.replace("-", "")) < 8:
        raise CliError("<id> must be a deployment UID, or a unique prefix of at least 8 hex characters", EXIT_USAGE)
    items = app.client.json("GET", BASE)["items"]
    matches = [d for d in items if d["uid"].startswith(s)]
    if not matches:
        raise CliError(f"no deployment has a UID starting with {s}", EXIT_FAIL)
    if len(matches) > 1:
        lines = [f"  {d['uid']}  {d['namespace']}/{d['name']}" for d in matches]
        raise CliError(f"{s} matches {len(matches)} deployments; use more characters:\n" + "\n".join(lines), EXIT_USAGE)
    return matches[0]["uid"]


# ---- confirmation (CL26, CL27) ----
def require_terminal_or_yes(app: App) -> None:
    """Checked before any request: with no terminal and no --yes nobody can confirm, so refuse (exit 2)."""
    if not app.args.yes and not app.ctx.stdin_tty:
        raise CliError("refusing to continue without a terminal to confirm on", EXIT_USAGE, "Pass --yes to confirm in scripts.")


def confirm(app: App, question: str) -> None:
    """No terminal and no --yes: refuse before asking (exit 2). Anything but y or yes aborts (exit 1)."""
    if app.args.yes:
        return
    require_terminal_or_yes(app)
    app.ctx.stderr.write(question + " [y/N] ")
    app.ctx.stderr.flush()
    if app.ctx.stdin.readline().strip().lower() not in ("y", "yes"):
        raise CliError("Aborted.", EXIT_FAIL)


# ---- --wait (CL29) ----
def _progress(app: App, d: dict, last: Optional[str]) -> str:
    state = f"{d['health']} ({d['replicas']['ready']}/{d['replicas']['desired']} ready)"
    if state != last:
        app.ctx.err(f"  {state}")
    return state


def wait_healthy(app: App, uid: str) -> int:
    ctx, deadline, last = app.ctx, app.ctx.now() + app.args.wait_timeout, None
    ctx.err("Waiting for HEALTHY...")
    while True:
        d = app.client.json("GET", f"{BASE}/{uid}")
        last = _progress(app, d, last)
        if d["health"] == "HEALTHY":
            return EXIT_OK
        if d["health"] == "FAILING":
            reason = render.why(d)
            raise CliError(f"{d['name']} is FAILING" + (f": {reason}" if reason else ""), EXIT_FAIL)
        if ctx.now() >= deadline:
            raise CliError(f"timed out after {app.args.wait_timeout:g} s waiting for {d['name']} to become HEALTHY", EXIT_FAIL)
        ctx.sleep(POLL_SECONDS)


def wait_gone(app: App, uid: str) -> int:
    ctx, deadline = app.ctx, app.ctx.now() + app.args.wait_timeout
    ctx.err("Waiting for the deployment to disappear...")
    while True:
        resp = app.client.request("GET", f"{BASE}/{uid}")
        if resp.status_code == 404:
            ctx.err("  gone")
            return EXIT_OK
        if resp.status_code >= 400:
            raise CliError(f"cannot check the deployment (status {resp.status_code})", EXIT_FAIL)
        if ctx.now() >= deadline:
            raise CliError(f"timed out after {app.args.wait_timeout:g} s waiting for the deployment to be deleted", EXIT_FAIL)
        ctx.sleep(POLL_SECONDS)


# ---- login (CL13) ----
def cmd_login(app: App) -> int:
    ctx, args = app.ctx, app.args
    if args.user:
        user = args.user
    else:
        try:
            user = ctx.getuser()
        except (OSError, KeyError):
            user = ""
    if not SUBJECT.match(user):
        raise CliError(f"cannot use {user!r} as the user name (letters, digits and _ . @ : - only, at most 128)", EXIT_USAGE,
                       "Pass --user <name>.")
    if args.bootstrap_secret_file:
        try:
            secret = open(args.bootstrap_secret_file).read().strip()
        except OSError:
            raise CliError(f"cannot read --bootstrap-secret-file {args.bootstrap_secret_file}", EXIT_USAGE) from None
    elif ctx.stdin_tty:
        secret = ctx.prompt_secret("Bootstrap secret: ").strip()
    else:
        raise CliError("no --bootstrap-secret-file and no terminal to ask on", EXIT_USAGE)
    if not secret:
        raise CliError("the bootstrap secret is empty", EXIT_USAGE)
    resp = app.client.request("POST", "/api/v1/auth/token", body={"sub": user}, auth=False,
                              headers={"X-Bootstrap-Secret": secret})
    if resp.status_code == 404:
        raise CliError("this server has no token endpoint (it is not a dev server)", EXIT_USAGE,
                       "Supply a token with --token-file, --token-stdin or DEPLOY_TOKEN.")
    if resp.status_code == 401:
        raise CliError("the bootstrap secret was rejected", EXIT_AUTH)
    if resp.status_code >= 400:
        from .client import error_from_response
        raise error_from_response(resp)
    data = resp.json()
    app.creds.put(app.client.server, entry_from_token_response(data, ctx.now(), user))
    ctx.out(f"Logged in as {user} on {app.client.server}. The access token is valid for {data['expires_in']} s.")
    return EXIT_OK


# ---- create (CL18-CL21) ----
def _pair(text: str, what: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise CliError(f"{what} must look like NAME=VALUE, got {text!r}", EXIT_USAGE)
    return key, value


def create_body(args) -> dict:
    body: dict = {"name": args.name, "image": args.image, "namespace": args.namespace}
    for key in ("replicas", "port"):
        if getattr(args, key) is not None:
            body[key] = getattr(args, key)
    env = [{"name": k, "value": v} for k, v in (_pair(e, "--env") for e in args.env)]
    for text in args.secret_env:
        name, ref = _pair(text, "--secret-env")
        secret, sep, key = ref.partition("/")
        if not sep or not secret or not key:
            raise CliError(f"--secret-env must look like NAME=SECRET/KEY, got {text!r}", EXIT_USAGE)
        env.append({"name": name, "secretRef": {"name": secret, "key": key}})
    if env:
        body["env"] = env
    if args.label:
        body["labels"] = dict(_pair(x, "--label") for x in args.label)
    if args.container_command:
        body["command"] = list(args.container_command)
    if args.arg:
        body["args"] = list(args.arg)
    res = [args.cpu_request, args.memory_request, args.cpu_limit, args.memory_limit]
    if any(x is not None for x in res):
        if any(x is None for x in res):
            raise CliError("give all four of --cpu-request, --memory-request, --cpu-limit, --memory-limit, or none", EXIT_USAGE,
                           "Without them the server applies its default resources.")
        body["resources"] = {"requests": {"cpu": args.cpu_request, "memory": args.memory_request},
                             "limits": {"cpu": args.cpu_limit, "memory": args.memory_limit}}
    return body


def cmd_create(app: App) -> int:
    body = create_body(app.args)
    d = app.client.json("POST", BASE, body=body)
    lines = [f"Created deployment {d['name']} in {d['namespace']}", f"  UID:      {d['uid']}",
             f"  Health:   {render.paint(d['health'], d['health'], app.ctx.color)}",
             f"  Replicas: {d['replicas']['ready']}/{d['replicas']['desired']} ready"]
    if d.get("namespaceCreated"):
        lines.append(f"  Note:     namespace {d['namespace']} was created")
    if d.get("resourcesDefaulted"):
        lines.append("  Note:     default resources applied")
    _show(app, d, lines)
    return wait_healthy(app, d["uid"]) if app.args.wait else EXIT_OK


# ---- list, status (CL22-CL25) ----
def cmd_list(app: App) -> int:
    params = {"namespace": app.args.namespace} if app.args.namespace else None
    data = app.client.json("GET", BASE, params=params)
    _show(app, data, render.list_table(data.get("items", []), app.ctx.now(), app.ctx.color))
    return EXIT_OK


def cmd_status(app: App) -> int:
    uid = resolve_id(app, app.args.id)
    d = app.client.json("GET", f"{BASE}/{uid}")
    _show(app, d, render.status_sections(d, app.ctx.now(), app.ctx.color))
    return EXIT_OK


# ---- scale, restart, delete (CL26-CL29) ----
def cmd_scale(app: App) -> int:
    uid = resolve_id(app, app.args.id)
    if app.args.replicas == 0 and not app.args.yes:
        require_terminal_or_yes(app)
        d = app.client.json("GET", f"{BASE}/{uid}")
        confirm(app, f"Scale {d['name']} in {d['namespace']} ({uid}) to 0? This stops the service.")
    d = app.client.json("PATCH", f"{BASE}/{uid}", body={"replicas": app.args.replicas})
    _show(app, d, [f"Scaled {d['name']} in {d['namespace']} to {d['replicas']['desired']} replicas "
                   f"({d['replicas']['ready']}/{d['replicas']['desired']} ready)"])
    return wait_healthy(app, uid) if app.args.wait else EXIT_OK


def cmd_restart(app: App) -> int:
    uid = resolve_id(app, app.args.id)
    d = app.client.json("POST", f"{BASE}/{uid}/restart")
    _show(app, d, [f"Restart triggered for {d['name']} in {d['namespace']}"])
    return wait_healthy(app, uid) if app.args.wait else EXIT_OK


def cmd_delete(app: App) -> int:
    uid = resolve_id(app, app.args.id, allow_prefix=False)
    require_terminal_or_yes(app)  # before any request (CL27)
    d = app.client.json("GET", f"{BASE}/{uid}")
    confirm(app, f"Delete {d['name']} in {d['namespace']} ({uid})? Its pods are removed, and an empty namespace "
                 "the API created is removed too.")
    out = app.client.json("DELETE", f"{BASE}/{uid}")
    _show(app, out, [f"Deleting {out['name']} in {out['namespace']} ({out['uid']}). Deletion finishes in the background."])
    return wait_gone(app, uid) if app.args.wait else EXIT_OK


def cmd_logs(app: App) -> int:
    from .logs import parse_since, stream_logs  # imported here so the other commands do not load the WebSocket library
    since = parse_since(app.args.since, app.ctx.now()) if app.args.since is not None else None  # before any request
    return stream_logs(app, resolve_id(app, app.args.id), since)
