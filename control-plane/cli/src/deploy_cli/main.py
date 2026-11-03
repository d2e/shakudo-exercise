"""Argument parsing, dispatch and the one place errors become exit codes (CL2, CL4)."""
import argparse
import sys
from contextlib import contextmanager

from . import __version__, commands
from .auth import prepare_auth
from .client import DEFAULT_SERVER, ApiClient, normalize_server
from .commands import App
from .context import Context
from .errors import EXIT_FAIL, EXIT_OK, EXIT_USAGE, CliError
from .store import Cookies, Credentials, config_dir

SUPPRESS = argparse.SUPPRESS


def _parser_class(ctx: Context):
    class Parser(argparse.ArgumentParser):
        """Writes to the injected streams and raises SystemExit, so tests need no real terminal."""

        def _print_message(self, message, file=None):
            if message:
                (ctx.stdout if file is sys.stdout else ctx.stderr).write(message)

        def exit(self, status=0, message=None):
            if message:
                self._print_message(message, sys.stderr)
            raise SystemExit(status)

    return Parser


def _common(p: argparse.ArgumentParser, top: bool) -> None:
    """Options accepted before or after the command. After the command they only override when given."""
    d = (lambda value: value) if top else (lambda value: SUPPRESS)
    p.add_argument("--server", default=d(None), help=f"API address (default: $DEPLOY_SERVER or {DEFAULT_SERVER})")
    p.add_argument("--token-file", default=d(None), help="read the token from this file (not refreshed)")
    p.add_argument("--token-stdin", action="store_true", default=d(False),
                   help="read the token from standard input (then confirmations need --yes)")
    p.add_argument("--insecure", action="store_true", default=d(False),
                   help="allow plain http to a non-local server (TLS certificates are still verified)")
    p.add_argument("--ca-file", default=d(None), help="CA certificate to trust, in addition to the system store")
    p.add_argument("--timeout", type=float, default=d(10.0), help="seconds per request (default 10)")
    p.add_argument("-o", "--output", choices=("table", "json"), default=d("table"),
                   help="table (default) or the API's JSON, for scripts")
    p.add_argument("--debug", action="store_true", default=d(False), help="print method, path and status of each request")


def _wait(p: argparse.ArgumentParser) -> None:
    p.add_argument("--wait", action="store_true", help="wait until the deployment is HEALTHY (exit 1 if FAILING or on timeout)")
    p.add_argument("--wait-timeout", type=float, default=120.0, help="seconds to wait (default 120)")


VALUE_FLAGS = ("--arg", "--command")  # container arguments often start with a dash, so the next token is always the value


def glue_values(argv: list[str]) -> list[str]:
    out, i = [], 0
    while i < len(argv):
        if argv[i] in VALUE_FLAGS and i + 1 < len(argv):
            out.append(f"{argv[i]}={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def build_parser(ctx: Context) -> argparse.ArgumentParser:
    P = _parser_class(ctx)
    root = P(prog="deploy", description="Manage Kubernetes deployments through the control-plane API.")
    root.add_argument("--version", action="version", version=f"deploy {__version__}")
    _common(root, top=True)
    sub = root.add_subparsers(dest="cmd", required=True, metavar="command", parser_class=P)

    def add(name, help_text, **kw):
        p = sub.add_parser(name, help=help_text, description=help_text, **kw)
        _common(p, top=False)
        return p

    p = add("login", "Get a token from the dev identity provider and store it")
    p.add_argument("--user", help="token subject; default: the operating-system user")
    p.add_argument("--bootstrap-secret-file", help="file holding the bootstrap secret (otherwise you are asked)")

    p = add("create", "Create a deployment")
    for flag in ("name", "image", "namespace"):
        p.add_argument(f"--{flag}", required=True)
    p.add_argument("--replicas", type=int, help="default: 1")
    p.add_argument("--port", type=int, help="container port")
    p.add_argument("--env", action="append", default=[], metavar="NAME=VALUE", help="repeatable")
    p.add_argument("--secret-env", action="append", default=[], metavar="NAME=SECRET/KEY",
                   help="environment variable from an existing Secret, repeatable")
    p.add_argument("--label", action="append", default=[], metavar="KEY=VALUE", help="repeatable")
    p.add_argument("--command", dest="container_command", action="append", default=[], help="container command, repeatable")
    p.add_argument("--arg", action="append", default=[], help="container argument, repeatable")
    for flag in ("cpu-request", "memory-request", "cpu-limit", "memory-limit"):
        p.add_argument(f"--{flag}", help="all four or none; none means the server's defaults")
    _wait(p)

    p = add("list", "List deployments with their health (only those created through this API, in allowed namespaces)")
    p.add_argument("-n", "--namespace", help="only this namespace")

    p = add("status", "Show a deployment in detail, including its pods")
    p.add_argument("id", help="deployment UID, or a unique prefix of at least 8 characters")

    p = add("scale", "Change the number of replicas")
    p.add_argument("id", help="deployment UID, or a unique prefix of at least 8 characters")
    p.add_argument("--replicas", type=int, required=True)
    p.add_argument("-y", "--yes", action="store_true", help="do not ask before scaling to 0")
    _wait(p)

    p = add("restart", "Trigger a rollout restart")
    p.add_argument("id", help="deployment UID, or a unique prefix of at least 8 characters")
    _wait(p)

    p = add("delete", "Delete a deployment (asks first)")
    p.add_argument("id", help="the full deployment UID")
    p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    p.add_argument("--wait", action="store_true", help="wait until the deployment is gone")
    p.add_argument("--wait-timeout", type=float, default=120.0, help="seconds to wait (default 120)")

    p = add("logs", "Stream logs from a deployment's pods")
    p.add_argument("id", help="deployment UID, or a unique prefix of at least 8 characters")
    p.add_argument("--pod", help="only this pod of the deployment")
    p.add_argument("--tail", type=int, default=100, help="lines of history to start with (default 100)")
    p.add_argument("--since", help="only logs newer than this, for example 30s, 10m, 2h")
    return root


COMMANDS = {
    "login": (commands.cmd_login, False), "create": (commands.cmd_create, True), "list": (commands.cmd_list, True),
    "status": (commands.cmd_status, True), "scale": (commands.cmd_scale, True), "restart": (commands.cmd_restart, True),
    "delete": (commands.cmd_delete, True), "logs": (commands.cmd_logs, True),
}


@contextmanager
def open_app(ctx: Context, args, authenticate: bool):
    raw = args.server if args.server is not None else (ctx.env.get("DEPLOY_SERVER") or DEFAULT_SERVER)  # an explicit empty --server is an error
    server = normalize_server(raw, args.insecure, ctx)
    directory = config_dir(ctx.env)
    cookies = Cookies(directory / "cookies")
    client = ApiClient(ctx, server, timeout=args.timeout, ca_file=args.ca_file, debug=args.debug, cookies=cookies)
    creds = Credentials(directory / "credentials")
    try:
        if authenticate:
            prepare_auth(ctx, args, client, creds)
        yield App(ctx, args, client, creds)
    finally:
        cookies.save()


def main(argv=None, ctx: Context | None = None) -> int:
    ctx = ctx or Context.system()
    try:
        args = build_parser(ctx).parse_args(glue_values(list(sys.argv[1:] if argv is None else argv)))
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else (EXIT_OK if e.code is None else EXIT_USAGE)
    handler, authenticate = COMMANDS[args.cmd]
    try:
        with open_app(ctx, args, authenticate) as app:
            return handler(app)
    except CliError as e:
        ctx.err(f"Error: {e.message}")
        if e.hint:
            ctx.err(e.hint)
        return e.code
    except KeyboardInterrupt:
        ctx.err("Interrupted.")
        return 130
    except Exception as e:  # noqa: BLE001 - never a traceback or raw text (CL4)
        ctx.err(f"Error: unexpected failure ({type(e).__name__}).")
        return EXIT_FAIL


def run() -> None:
    raise SystemExit(main())
