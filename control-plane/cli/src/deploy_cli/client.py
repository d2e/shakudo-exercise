"""The HTTP client: transport rules (CL14), timeouts and retries (CL6, CL7), the affinity cookie (CL38), and
readable errors (CL4, CL5). The CLI never follows a redirect, so the token cannot reach another host."""
from typing import Optional
from urllib.parse import urlparse

import requests

from . import __version__
from .context import Context
from .errors import EXIT_AUTH, EXIT_FAIL, EXIT_USAGE, CliError
from .store import Cookies

DEFAULT_SERVER = "http://localhost:8000"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def normalize_server(raw: str, insecure: bool, ctx: Context) -> str:
    """R16: plain http only to localhost, unless --insecure (with a warning)."""
    url = urlparse(raw.strip())
    if url.scheme not in ("http", "https") or not url.hostname:
        raise CliError(f"server must be a URL such as https://host or http://localhost:8000, got {raw!r}", EXIT_USAGE)
    if url.scheme == "http" and url.hostname not in LOCAL_HOSTS:
        if not insecure:
            raise CliError(f"refusing to send credentials over plain http to {url.hostname}", EXIT_USAGE,
                           "Use an https:// server, or pass --insecure to accept the risk.")
        ctx.err(f"WARNING: sending credentials over plain http to {url.hostname}; anyone on the network can read them.")
    return raw.strip().rstrip("/")


def error_from_response(resp: requests.Response, token_origin: str = "none") -> CliError:
    """Turn an API error into a readable message: message, one line per detail, the request id (CL4, CL5)."""
    status = resp.status_code
    try:
        body = resp.json()
        err = body["error"] if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    except ValueError:
        err = {}
    defaults = {400: "the request was invalid", 401: "authentication failed", 403: "not allowed",
                404: "not found", 409: "name already in use", 413: "request is too large",
                429: "too many requests"}
    message = str(err.get("message") or defaults.get(status) or f"the server answered {status}")
    lines = [message]
    for d in err.get("details") or []:
        if isinstance(d, dict):
            lines.append(f"  - {d.get('field', '?')}: {d.get('message', d.get('rule', ''))}")
    request_id = err.get("request_id") or resp.headers.get("X-Request-ID")
    if request_id:
        lines.append(f"  request id: {request_id}")
    hint: Optional[str] = None
    code = EXIT_FAIL
    if status == 401:
        code = EXIT_AUTH
        hint = ("Run `deploy login`." if token_origin == "stored"
                else "The supplied token was rejected; get a fresh one.")
    elif status == 429:
        wait = resp.headers.get("Retry-After")
        hint = f"Rate limited. Retry in {wait} s." if wait else "Rate limited. Try again shortly."
    return CliError("\n".join(lines), code, hint)


class ApiClient:
    def __init__(self, ctx: Context, server: str, *, timeout: float, ca_file: Optional[str], debug: bool, cookies: Cookies):
        self.ctx, self.server, self.timeout, self.debug, self.cookies = ctx, server, timeout, debug, cookies
        self.verify = ca_file or True
        self.session = requests.Session()
        self.token: Optional[str] = None
        self.token_origin = "none"

    def set_token(self, token: str, origin: str) -> None:
        self.token, self.token_origin = token, origin

    def request(self, method: str, path: str, *, params: Optional[dict] = None, body: Optional[dict] = None,
                auth: bool = True, headers: Optional[dict] = None) -> requests.Response:
        h = {"Accept": "application/json", "User-Agent": f"deploy-cli/{__version__}", **(headers or {})}
        if auth and self.token:
            h["Authorization"] = f"Bearer {self.token}"
        cookie = self.cookies.header_for(self.server)
        if cookie:
            h["Cookie"] = cookie
        attempts = 2 if method == "GET" else 1  # CL6: only reads are retried
        for attempt in range(attempts):
            try:
                resp = self.session.request(method, self.server + path, params=params, json=body, headers=h,
                                            timeout=self.timeout, allow_redirects=False, verify=self.verify)
                break
            except requests.exceptions.SSLError:
                raise CliError("TLS verification of the server failed", EXIT_FAIL,
                               "Check the server address, or pass --ca-file with the CA certificate.") from None
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt + 1 < attempts:
                    continue
                reason = "timed out" if isinstance(e, requests.exceptions.Timeout) else "connection failed"
                raise CliError(f"cannot reach {self.server}: {reason}", EXIT_FAIL) from None
        if self.debug:
            self.ctx.err(f"debug: {method} {path} -> {resp.status_code}")  # never headers, tokens or bodies (CL8)
        self.cookies.update(self.server, resp.cookies.get_dict())
        self.session.cookies.clear()  # we send our own Cookie header; keep the session jar empty
        if 300 <= resp.status_code < 400:
            raise CliError(f"the server redirected the request to {resp.headers.get('Location', 'another address')}", EXIT_FAIL,
                           "The CLI does not follow redirects, so the token is never sent elsewhere. Check --server.")
        return resp

    def json(self, method: str, path: str, **kw) -> dict:
        resp = self.request(method, path, **kw)
        if resp.status_code >= 400:
            raise error_from_response(resp, self.token_origin)
        try:
            data = resp.json()
        except ValueError:
            raise CliError(f"unexpected response from the server (status {resp.status_code})", EXIT_FAIL) from None
        return data if isinstance(data, dict) else {}
