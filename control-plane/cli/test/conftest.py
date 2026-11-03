"""A fake API server that checks every request body and every canned response against spec/openapi.yaml (CL37),
and a `run` fixture that drives the CLI with an injected clock, streams and environment."""
import json
import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml
from jsonschema import Draft202012Validator

import canned
from deploy_cli.context import Context
from deploy_cli.main import main

SPEC = Path(__file__).resolve().parents[2] / "spec" / "openapi.yaml"


@pytest.fixture(scope="session")
def spec() -> dict:
    return yaml.safe_load(SPEC.read_text())


def _operation(spec: dict, method: str, path: str):
    for template, item in spec["paths"].items():
        if re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", template), path) and method.lower() in item:
            return item[method.lower()]
    return None


def _problems(spec: dict, schema: dict, instance) -> list[str]:
    validator = Draft202012Validator({**schema, "components": spec["components"]})
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(instance)]


def _json_schema(content: dict):
    return (content or {}).get("application/json", {}).get("schema")


@dataclass
class Seen:
    method: str
    path: str
    query: dict
    headers: dict
    body: object


class FakeServer:
    def __init__(self, spec: dict, host: str = "127.0.0.1"):
        self.spec, self.seen, self.violations, self._routes = spec, [], [], {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _handle(self):
                url = urlparse(self.path)
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                body = json.loads(raw) if raw else None
                server.seen.append(Seen(self.command, url.path, {k: v[0] for k, v in parse_qs(url.query).items()},
                                        {k.lower(): v for k, v in self.headers.items()}, body))
                op = _operation(server.spec, self.command, url.path)
                schema = _json_schema(((op or {}).get("requestBody") or {}).get("content"))
                if body is not None and schema:
                    for problem in _problems(server.spec, schema, body):
                        server.violations.append(f"{self.command} {url.path} request {problem}")
                status, reply, headers, delay = server._next(self.command, url.path)
                if delay:
                    time.sleep(delay)
                payload = b"" if reply is None else (reply if isinstance(reply, bytes) else json.dumps(reply).encode())
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

            do_GET = do_POST = do_PATCH = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer((host, 0), Handler)
        self.httpd.daemon_threads = True
        self.url = f"http://{host}:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def reply(self, method: str, path: str, status: int = 200, body=None, headers=None, delay: float = 0.0):
        """Queue a response for a route. The last one repeats. Canned JSON bodies are validated against the spec now."""
        if isinstance(body, dict):
            op = _operation(self.spec, method, path)
            responses = (op or {}).get("responses", {})
            schema = _json_schema((responses.get(str(status)) or {}).get("content"))
            if schema is None and status >= 400:
                schema = {"$ref": "#/components/schemas/Error"}
            if schema is not None:
                problems = _problems(self.spec, schema, body)
                assert not problems, f"canned {status} for {method} {path} breaks the OpenAPI contract: {problems}"
        self._routes.setdefault((method, path), []).append((status, body, headers, delay))

    def _next(self, method: str, path: str):
        queue = self._routes.get((method, path))
        if not queue:
            return 404, canned.error("not_found", "no such route in the fake server"), None, 0
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def requests(self, method=None, path=None) -> list[Seen]:
        return [s for s in self.seen if (method is None or s.method == method) and (path is None or s.path == path)]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@dataclass
class Result:
    code: int
    out: str
    err: str


class Clock:
    def __init__(self):
        self.t, self.sleeps = canned.NOW, []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,127.0.0.2,localhost,::1")


@pytest.fixture
def server(spec):
    srv = FakeServer(spec)
    yield srv
    srv.stop()
    assert srv.violations == [], f"the CLI sent requests that break the OpenAPI contract: {srv.violations}"


@pytest.fixture
def other_server(spec):
    srv = FakeServer(spec)
    yield srv
    srv.stop()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "home"
    path.mkdir()
    return path


@pytest.fixture
def token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("tok-file-123\n")
    return path


@pytest.fixture
def run(server, clock, home, token_file):
    def _run(*argv, env=None, stdin="", tty=False, secret="s3cret", user="alice", auth=True, target=None) -> Result:
        out, err = StringIO(), StringIO()
        args = list(argv)
        if "--server" not in args:
            args = ["--server", (target or server).url] + args
        if auth and "--token-file" not in args and "--token-stdin" not in args:
            args = ["--token-file", str(token_file)] + args
        ctx = Context(env={"XDG_CONFIG_HOME": str(home), "HOME": str(home), **(env or {})}, stdin=StringIO(stdin),
                      stdout=out, stderr=err, stdin_tty=tty, stdout_tty=False, now=clock.now, sleep=clock.sleep,
                      prompt_secret=lambda prompt: secret, getuser=lambda: user)
        return Result(main(args, ctx), out.getvalue(), err.getvalue())
    return _run
