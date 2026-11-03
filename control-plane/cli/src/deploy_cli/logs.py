"""`deploy logs` (D28, req-logs.md): ask for a ticket over HTTP with the Bearer token, then open the WebSocket
with only the ticket. Lines are not repeated when the stream resumes, and log text is made safe for a terminal."""
import json
import re
import ssl
from datetime import datetime, timedelta, timezone
from typing import Optional

import websocket
from websocket import ABNF

from .auth import prepare_auth
from .errors import EXIT_AUTH, EXIT_FAIL, EXIT_OK, EXIT_USAGE, CliError

SUBPROTOCOL = "logs.v1"
IDLE_SECONDS = 90          # the server pings every 20 s (LG15), so silence this long means the link is dead
MAX_RECONNECT_FAILURES = 3
RESUME_TAIL = 1000         # the largest tail the API accepts (LG2), so a resume loses nothing

CLOSE_NORMAL, CLOSE_GOING_AWAY, CLOSE_TOO_SLOW, CLOSE_CLUSTER, CLOSE_TOKEN_EXPIRED = 1000, 1001, 1013, 1011, 4001
_UNSAFE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def parse_since(text: str, now: float) -> str:
    m = re.fullmatch(r"(\d+)([smhd])", text.strip())
    if not m or int(m.group(1)) == 0:
        raise CliError(f"--since must look like 30s, 10m, 2h or 1d, got {text!r}", EXIT_USAGE)
    seconds = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    return datetime.fromtimestamp(now - seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ts_key(stamp: str) -> tuple:
    """Sortable form of an RFC 3339 time; Kubernetes trims trailing zeros of the fraction, so plain strings do not sort."""
    head, _, rest = stamp.rstrip("Z").partition(".")
    return head, rest.ljust(9, "0")[:9]


def clean(text: str) -> str:
    """A workload controls its log text. Escape sequences could rewrite the operator's terminal, so show them as \\xNN."""
    return _UNSAFE.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def ws_url(server: str, path: str) -> str:
    base = server + path
    return "wss" + base[len("https"):] if base.startswith("https") else "ws" + base[len("http"):]


class Resume:
    """Remembers where each pod's stream got to, so a reconnect does not print a line twice.

    Tracking is per pod: in a merged stream, lines from different pods arrive out of timestamp order, so one shared
    "last time" would wrongly drop a slower pod's older lines. A resume asks for logs since the oldest pod position."""

    def __init__(self, since: Optional[str]):
        self.since = since
        self.last: dict[str, str] = {}          # pod -> time of its newest printed line
        self.at_last: dict[str, set] = {}       # pod -> lines printed at exactly that time

    def is_new(self, stamp: str, pod: str, line: str) -> bool:
        last = self.last.get(pod)
        if last is None or ts_key(stamp) > ts_key(last):
            return True
        return ts_key(stamp) == ts_key(last) and line not in self.at_last[pod]

    def record(self, stamp: str, pod: str, line: str) -> None:
        last = self.last.get(pod)
        if last is None or ts_key(stamp) > ts_key(last):
            self.last[pod], self.at_last[pod] = stamp, set()
        self.at_last[pod].add(line)
        self.since = min(self.last.values(), key=ts_key)


def request_ticket(app, uid: str, resume: Resume, resuming: bool) -> dict:
    body: dict = {"tailLines": RESUME_TAIL if resuming else app.args.tail}
    if app.args.pod:
        body["pod"] = app.args.pod
    if resume.since:
        body["sinceTime"] = resume.since
    return app.client.json("POST", f"/api/v1/deployments/{uid}/logs/ticket", body=body)


def open_stream(app, ticket: dict) -> websocket.WebSocket:
    client = app.client
    cookie = client.cookies.header_for(client.server)
    try:
        ws = websocket.create_connection(
            ws_url(client.server, ticket["streamPath"]), timeout=app.args.timeout,
            subprotocols=[SUBPROTOCOL, f"ticket.{ticket['ticket']}"],  # never in the URL (LG6)
            header=[f"Cookie: {cookie}"] if cookie else [], suppress_origin=True,  # no Origin from the CLI (LG8)
            sslopt={"ca_certs": app.args.ca_file} if app.args.ca_file else {})
    except websocket.WebSocketBadStatusException as e:
        raise CliError(f"the server refused the log stream (HTTP {e.status_code})", EXIT_FAIL) from None
    except ssl.SSLError:
        raise CliError("TLS verification of the server failed", EXIT_FAIL, "Pass --ca-file with the CA certificate.") from None
    except (websocket.WebSocketException, OSError):
        return None
    if ws.getsubprotocol() != SUBPROTOCOL:
        ws.close()
        raise CliError("the server did not accept the log protocol", EXIT_FAIL)
    ws.settimeout(IDLE_SECONDS)
    return ws


def show(app, resume: Resume, text: str) -> bool:
    """Print one frame. Returns True when it was a new line."""
    try:
        frame = json.loads(text)
        stamp, pod, line = str(frame["time"]), str(frame["pod"]), str(frame["line"])
    except (ValueError, KeyError, TypeError):
        return False  # a malformed frame is skipped, never printed raw
    if not resume.is_new(stamp, pod, line):
        return False
    resume.record(stamp, pod, line)
    if app.args.output == "json":
        app.ctx.out(text)
    else:
        app.ctx.out(("" if app.args.pod else f"[{clean(pod)}] ") + clean(line))
    app.ctx.stdout.flush()
    return True


def receive(app, ws, resume: Resume) -> tuple[str, int, bool]:
    """Read until the stream ends. Returns (how, close code, got_lines)."""
    got = False
    while True:
        try:
            opcode, frame = ws.recv_data_frame(True)
        except (websocket.WebSocketException, OSError):
            return "dropped", 0, got
        if opcode == ABNF.OPCODE_TEXT:
            got = show(app, resume, frame.data.decode("utf-8", "replace")) or got
        elif opcode == ABNF.OPCODE_CLOSE:
            return "closed", int.from_bytes(frame.data[:2], "big") if len(frame.data) >= 2 else 1005, got


def stream_logs(app, uid: str, since: Optional[str]) -> int:
    ctx, resume = app.ctx, Resume(since)
    failures, resuming = 0, False
    while True:
        ticket = request_ticket(app, uid, resume, resuming)
        ws = open_stream(app, ticket)
        if ws is None:
            how, code, got = "dropped", 0, False
        else:
            try:
                how, code, got = receive(app, ws, resume)
            except KeyboardInterrupt:  # CL30: Ctrl-C ends the stream cleanly
                ws.close()
                return EXIT_OK
            finally:
                ws.close()
        failures = 0 if got else failures + (how == "dropped")
        if how == "closed":
            if code == CLOSE_NORMAL:
                ctx.err("Stream ended.")
                return EXIT_OK
            if code == CLOSE_TOKEN_EXPIRED:
                if app.client.token_origin != "stored":  # CL33: only a stored login can be refreshed
                    raise CliError("the token expired and cannot be refreshed here", EXIT_AUTH,
                                   "Get a fresh token and run the command again, or use `deploy login`.")
                ctx.err("Token expired; reconnecting with a fresh one...")
                prepare_auth(ctx, app.args, app.client, app.creds)
            elif code == CLOSE_GOING_AWAY:
                ctx.err("Server restarting; reconnecting...")
            elif code == CLOSE_TOO_SLOW:
                raise CliError("the server closed the stream because this client could not keep up", EXIT_FAIL)
            elif code == CLOSE_CLUSTER:
                raise CliError("the cluster failed while streaming logs", EXIT_FAIL)
            else:
                raise CliError(f"the server closed the stream (code {code})", EXIT_FAIL)
        elif failures > MAX_RECONNECT_FAILURES:
            raise CliError("lost the connection to the log stream", EXIT_FAIL)
        else:
            ctx.err("Connection lost; reconnecting...")
        resuming = resume.since is not None
        ctx.sleep(1)
