"""Where the token comes from (CL9) and how a stored one is refreshed (CL10). Tokens are never printed (CL12)."""
import re
from pathlib import Path

from .client import ApiClient, error_from_response
from .context import Context
from .errors import EXIT_AUTH, EXIT_USAGE, CliError
from .store import Credentials

REFRESH_MARGIN_SECONDS = 60  # T3


def entry_from_token_response(data: dict, now: float, user: str) -> dict:
    return {"user": user, "access_token": data["access_token"], "refresh_token": data["refresh_token"],
            "expires_at": now + float(data["expires_in"]), "refresh_expires_at": now + float(data["refresh_expires_in"])}


def _clean(token: str, where: str) -> str:
    token = token.strip()
    if not token or re.search(r"\s", token):
        raise CliError(f"the token from {where} is empty or contains whitespace", EXIT_USAGE)
    return token


def prepare_auth(ctx: Context, args, client: ApiClient, creds: Credentials) -> None:
    """Pick the token, first match wins: --token-file, --token-stdin, DEPLOY_TOKEN, stored credentials."""
    if args.token_file:
        try:
            token = Path(args.token_file).read_text()
        except OSError:
            raise CliError(f"cannot read --token-file {args.token_file}", EXIT_USAGE) from None
        return client.set_token(_clean(token, "--token-file"), "file")  # used as given, never refreshed (T7)
    if args.token_stdin:
        return client.set_token(_clean(ctx.stdin.read(), "stdin"), "stdin")
    if ctx.env.get("DEPLOY_TOKEN"):
        return client.set_token(_clean(ctx.env["DEPLOY_TOKEN"], "DEPLOY_TOKEN"), "env")

    entry = creds.get(client.server)
    if not entry:
        raise CliError("not logged in", EXIT_AUTH, "Run `deploy login`, or supply a token with --token-file or --token-stdin.")
    now = ctx.now()
    if entry["expires_at"] - now <= REFRESH_MARGIN_SECONDS:
        if entry.get("refresh_expires_at", 0) <= now:
            raise CliError("your session has expired", EXIT_AUTH, "Run `deploy login`.")
        resp = client.request("POST", "/api/v1/auth/refresh", body={"refresh_token": entry["refresh_token"]}, auth=False)
        if resp.status_code in (400, 401, 403, 404):
            raise CliError("your session could not be refreshed", EXIT_AUTH, "Run `deploy login`.")
        if resp.status_code >= 400:
            raise error_from_response(resp)
        entry = entry_from_token_response(resp.json(), now, entry.get("user", ""))
        creds.put(client.server, entry)
    client.set_token(entry["access_token"], "stored")
