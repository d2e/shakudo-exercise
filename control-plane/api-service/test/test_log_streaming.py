"""D28 log streaming (req-logs.md LT1-LT11): the ticket request, the WebSocket that only a ticket opens, limits."""
import asyncio
import contextlib
import json
import logging
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api_service.app import RuntimeState, create_app
from api_service.config import ConfigError, load_settings
from api_service.log_service import LogSession, StreamRegistry
from api_service.security import TICKET_AUTH_PATHS
from api_service.streaming import run_stream
from support import BOOTSTRAP, FakeGateway, log_lines, make_settings, pod

STREAM = "/api/v1/logs/stream"
UID_UNKNOWN = "00000000-0000-4000-8000-0000000000ff"


# ---- helpers ----
def login(client) -> tuple[dict, str]:
    r = client.post("/api/v1/auth/token", json={"sub": "tester"}, headers={"X-Bootstrap-Secret": BOOTSTRAP})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["access_token"]


def prepare(client, gateway, valid_body, lines=("a1", "a2")):
    """A deployment with two pods and scripted logs. Returns (auth headers, uid)."""
    headers, _ = login(client)
    r = client.post("/api/v1/deployments", json=valid_body, headers=headers)
    assert r.status_code == 201, r.text
    uid = r.json()["uid"]
    gateway.pods[uid] = [pod("p1"), pod("p2")]
    gateway.logs[("demo", "p1")] = log_lines(*lines)
    gateway.logs[("demo", "p2")] = log_lines("b1")
    return headers, uid


def ticket(client, headers, uid, **body) -> str:
    r = client.post(f"/api/v1/deployments/{uid}/logs/ticket", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def open_ws(client, value, headers=None, protocols=None):
    protos = protocols if protocols is not None else ["logs.v1", f"ticket.{value}"]
    return client.websocket_connect(STREAM, subprotocols=protos, headers=headers or {})


def drain(ws):
    """Frames until the server closes. Returns (frames, close code)."""
    frames = []
    while True:
        try:
            frames.append(json.loads(ws.receive_text()))
        except WebSocketDisconnect as e:
            return frames, e.code


def refused(client, protocols=None, headers=None) -> bool:
    try:
        with open_ws(client, "x", headers=headers, protocols=protocols):
            return False
    except WebSocketDisconnect:
        return True


def walk(routes):
    for r in routes:
        inner = getattr(getattr(r, "original_router", None), "routes", None)
        yield from walk(inner) if inner is not None else [r]


# ---- LT1: the ticket request ----
@pytest.mark.parametrize("body", [{"tailLines": 0}, {"tailLines": 1001}, {"pod": "Bad_Pod"}, {"sinceTime": "yesterday"},
                                  {"extra": 1}])
def test_invalid_ticket_request_is_400_without_a_cluster_call(client, gateway, valid_body, body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.calls.clear()
    r = client.post(f"/api/v1/deployments/{uid}/logs/ticket", json=body, headers=headers)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    assert gateway.calls == []  # V22


def test_ticket_request_without_a_token_is_401_even_with_a_bad_body(client, gateway, valid_body):
    _, uid = prepare(client, gateway, valid_body)
    gateway.calls.clear()
    r = client.post(f"/api/v1/deployments/{uid}/logs/ticket", json={"tailLines": 0})
    assert r.status_code == 401 and gateway.calls == []  # A4


def test_ticket_for_unknown_unmanaged_or_malformed_id(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    url = "/api/v1/deployments/{}/logs/ticket"
    assert client.post(url.format(UID_UNKNOWN), json={}, headers=headers).status_code == 404
    assert client.post(url.format("not-a-uid"), json={}, headers=headers).status_code == 400
    # outside the allowlist the deployment does not exist for this API (D21), same as get and patch
    moved = gateway.deployments[("demo", "http-echo")]
    gateway.deployments[("other", "x")] = type(moved)(uid="00000000-0000-4000-8000-0000000000aa", name="x", namespace="other")
    assert client.post(url.format("00000000-0000-4000-8000-0000000000aa"), json={}, headers=headers).status_code == 404


def test_ticket_for_a_pod_of_another_deployment_is_404(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    other = client.post("/api/v1/deployments", json={**valid_body, "name": "other-dep"}, headers=headers).json()["uid"]
    gateway.pods[other] = [pod("z1")]
    r = client.post(f"/api/v1/deployments/{uid}/logs/ticket", json={"pod": "z1"}, headers=headers)
    assert r.status_code == 404 and r.json()["error"]["message"] == "pod not found"


def test_fourth_stream_is_refused_at_the_ticket_and_at_the_upgrade(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.keep_open = True
    tickets = [ticket(client, headers, uid) for _ in range(4)]  # four tickets while none are open
    with contextlib.ExitStack() as stack:
        for t in tickets[:3]:
            stack.enter_context(open_ws(client, t))
        assert client.app.state.log_registry.open_count("tester") == 3
        assert refused(client, protocols=["logs.v1", f"ticket.{tickets[3]}"])  # authoritative at the upgrade (R21)
        r = client.post(f"/api/v1/deployments/{uid}/logs/ticket", json={}, headers=headers)
        assert r.status_code == 429 and r.json()["error"]["code"] == "too_many_streams"
    gateway.end_log_streams()
    deadline = time.time() + 3
    while client.app.state.log_registry.open_count("tester") and time.time() < deadline:
        time.sleep(0.02)
    assert client.app.state.log_registry.open_count("tester") == 0  # slots are given back
    assert client.post(f"/api/v1/deployments/{uid}/logs/ticket", json={}, headers=headers).status_code == 200


# ---- LT2, LT3: what a ticket is ----
def test_ticket_contents_are_bound_and_short_lived(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    access = headers["Authorization"].split()[1]
    r = client.post(f"/api/v1/deployments/{uid}/logs/ticket", json={"pod": "p1", "tailLines": 5,
                                                                    "sinceTime": "2026-01-01T00:00:00Z"}, headers=headers)
    body = r.json()
    assert body["streamPath"] == STREAM and body["expiresIn"] == 30 and set(body) == {"ticket", "streamPath", "expiresIn"}
    claims = jwt.decode(body["ticket"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 30
    assert (claims["sub"], claims["uid"], claims["ns"], claims["pod"], claims["tail"], claims["since"]) == \
        ("tester", uid, "demo", "p1", 5, "2026-01-01T00:00:00Z")
    assert claims["token_use"] == "log-ticket" and claims["jti"]
    assert claims["stream_until"] == jwt.decode(access, options={"verify_signature": False})["exp"]
    assert ticket(client, headers, uid) != body["ticket"]  # every ticket has its own id


def test_tokens_and_tickets_are_not_interchangeable(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    pair = client.post("/api/v1/auth/token", json={"sub": "tester"}, headers={"X-Bootstrap-Secret": BOOTSTRAP}).json()
    assert refused(client, protocols=["logs.v1", f"ticket.{pair['access_token']}"])
    assert refused(client, protocols=["logs.v1", f"ticket.{pair['refresh_token']}"])
    as_bearer = {"Authorization": f"Bearer {ticket(client, headers, uid)}"}
    assert client.get("/api/v1/deployments", headers=as_bearer).status_code == 401


# ---- LT4: a valid ticket opens the stream ----
def test_valid_ticket_streams_every_pod_merged(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    with open_ws(client, ticket(client, headers, uid)) as ws:
        assert ws.accepted_subprotocol == "logs.v1"  # the ticket subprotocol is never echoed (LG6)
        frames, code = drain(ws)
    assert code == 1000
    assert all(set(f) == {"pod", "time", "line"} for f in frames)
    assert sorted((f["pod"], f["line"]) for f in frames) == [("p1", "a1"), ("p1", "a2"), ("p2", "b1")]


def test_pod_filter_and_tail_lines(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    with open_ws(client, ticket(client, headers, uid, pod="p1", tailLines=1)) as ws:
        frames, code = drain(ws)
    assert [(f["pod"], f["line"]) for f in frames] == [("p1", "a2")] and code == 1000


def test_pending_pods_are_not_read_when_merging(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.pods[uid] = [pod("p1"), pod("p2", phase="Pending")]
    with open_ws(client, ticket(client, headers, uid)) as ws:
        frames, code = drain(ws)
    assert {f["pod"] for f in frames} == {"p1"} and code == 1000


def test_a_deployment_without_pods_ends_at_once(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.pods[uid] = []
    with open_ws(client, ticket(client, headers, uid)) as ws:
        assert drain(ws) == ([], 1000)


def test_cluster_failure_mid_stream_closes_1011(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.log_failure = "unreachable"
    with open_ws(client, ticket(client, headers, uid)) as ws:
        _, code = drain(ws)
    assert code == 1011


# ---- LT5: refused handshakes ----
def test_handshakes_without_a_valid_ticket_are_refused(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    good = ticket(client, headers, uid)
    gateway.calls.clear()
    assert refused(client, protocols=[])
    assert refused(client, protocols=["logs.v1"])  # no ticket
    assert refused(client, protocols=[f"ticket.{good}"])  # no logs.v1
    assert refused(client, protocols=["logs.v1", f"ticket.{good}", f"ticket.{good}"])  # two tickets
    assert refused(client, protocols=["logs.v1", "ticket.garbage"])
    assert refused(client, protocols=["logs.v1", f"ticket.{good[:-4]}AAAA"])  # tampered signature
    assert "stream_pod_logs" not in gateway.calls and client.app.state.log_registry.total_open() == 0


def test_expired_and_repeated_tickets_are_refused(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    tokens = client.app.state.tokens
    mint = lambda **kw: tokens.mint_log_ticket(sub="tester", uid=uid, namespace="demo", pod=None, tail_lines=10,
                                               since_time=None, stream_until=int(time.time()) + 300, **kw)[0]
    assert refused(client, protocols=["logs.v1", f"ticket.{mint(ttl=-10)}"])  # expired
    once = ticket(client, headers, uid)
    with open_ws(client, once) as ws:
        drain(ws)
    assert refused(client, protocols=["logs.v1", f"ticket.{once}"])  # repeated (LG5)
    past = tokens.mint_log_ticket(sub="tester", uid=uid, namespace="demo", pod=None, tail_lines=10, since_time=None,
                                  stream_until=int(time.time()) - 1)[0]
    assert refused(client, protocols=["logs.v1", f"ticket.{past}"])  # the token it came from has expired


# ---- LT6: the ticket never leaks ----
def test_ticket_and_log_text_never_reach_logs(client, gateway, valid_body, caplog):
    caplog.set_level(logging.DEBUG)
    headers, uid = prepare(client, gateway, valid_body)
    gateway.logs[("demo", "p1")] = log_lines("SECRET-LOG-TEXT")
    value = ticket(client, headers, uid)
    with open_ws(client, value) as ws:
        assert ws.accepted_subprotocol == "logs.v1"
        drain(ws)
    refused(client, protocols=["logs.v1", f"ticket.{value}"])  # a repeat is logged as refused, without the value
    assert value not in caplog.text and "SECRET-LOG-TEXT" not in caplog.text
    events = [json.loads(r.getMessage()) for r in caplog.records if r.name == "api_service.audit"]
    actions = [e["action"] for e in events]
    assert actions.count("logs.ticket") >= 1 and "logs.stream.open" in actions and "logs.stream.close" in actions
    assert any(e["action"] == "logs.stream.refused" and e["reason"] == "ticket_reused" for e in events)
    close = next(e for e in events if e["action"] == "logs.stream.close")
    assert close["sub"] == "tester" and close["code"] == 1000 and close["lines"] == 2 and "duration_ms" in close


# ---- LT7: Origin ----
def test_origin_must_be_allowed_when_present(make_client, gateway, valid_body):
    client = make_client(cors_allowed_origins=["https://ui.example.com"])
    headers, uid = prepare(client, gateway, valid_body)
    ok = lambda origin: open_ws(client, ticket(client, headers, uid), headers={"origin": origin} if origin else {})
    with ok(None) as ws:  # the CLI sends none
        drain(ws)
    with ok("https://ui.example.com") as ws:
        drain(ws)
    with pytest.raises(WebSocketDisconnect):
        with ok("https://evil.example.com"):
            pass


def test_any_origin_is_refused_when_none_are_configured(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    with pytest.raises(WebSocketDisconnect):
        with open_ws(client, ticket(client, headers, uid), headers={"origin": "https://ui.example.com"}):
            pass


# ---- LT8: the deployment goes away ----
def test_deployment_deleted_between_ticket_and_open_is_refused(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    value = ticket(client, headers, uid)
    gateway.deployments.pop(("demo", "http-echo"))
    assert refused(client, protocols=["logs.v1", f"ticket.{value}"])  # LG9: looked at again live


def test_pod_gone_between_ticket_and_open_is_refused(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    value = ticket(client, headers, uid, pod="p1")
    gateway.pods[uid] = [pod("p2")]
    assert refused(client, protocols=["logs.v1", f"ticket.{value}"])


def test_pods_ending_mid_stream_close_1000(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.keep_open = True
    with open_ws(client, ticket(client, headers, uid)) as ws:
        first = json.loads(ws.receive_text())
        assert first["line"] in {"a1", "b1"}
        gateway.pods[uid] = []
        gateway.end_log_streams()
        _, code = drain(ws)
    assert code == 1000


# ---- LT9: no websocket route without a ticket ----
def test_every_websocket_route_validates_a_ticket_first(client, gateway):
    paths = {r.path for r in walk(client.app.routes) if "WebSocket" in type(r).__name__}
    assert paths and paths == TICKET_AUTH_PATHS  # a new websocket route must be listed, so it is tested here
    for path in paths:
        for protocols in ([], ["logs.v1"], ["logs.v1", "ticket.garbage"]):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(path, subprotocols=protocols):
                    pass
    assert "stream_pod_logs" not in gateway.calls
    assert client.get(STREAM).status_code == 401  # a plain GET is still default-deny (A1)


# ---- LT10: expiry and resuming ----
def test_stream_ends_with_4001_at_the_token_expiry_and_resumes_without_repeats(client, gateway, valid_body):
    headers, uid = prepare(client, gateway, valid_body)
    gateway.keep_open = True
    short = client.app.state.tokens.mint_log_ticket(sub="tester", uid=uid, namespace="demo", pod="p1", tail_lines=100,
                                                    since_time=None, stream_until=int(time.time()) + 1)[0]
    started = time.time()
    with open_ws(client, short) as ws:
        frames, code = drain(ws)
    assert code == 4001 and time.time() - started < 5
    assert [f["line"] for f in frames] == ["a1", "a2"]
    gateway.keep_open = False
    with open_ws(client, ticket(client, headers, uid, pod="p1", sinceTime=frames[0]["time"])) as ws:
        again, code = drain(ws)
    assert [f["line"] for f in again] == ["a2"] and code == 1000  # strictly after the last time seen


def test_shutdown_closes_streams_1001_and_refuses_new_ones(bootstrap_file, gateway, valid_body):
    state = RuntimeState()
    client = TestClient(create_app(make_settings(bootstrap_secret_file=bootstrap_file), gateway=gateway, state=state),
                        raise_server_exceptions=False)
    headers, uid = prepare(client, gateway, valid_body)
    gateway.keep_open = True
    with open_ws(client, ticket(client, headers, uid)) as ws:
        ws.receive_text()
        state.shutting_down = True  # what SIGTERM does (H7)
        _, code = drain(ws)
        assert code == 1001
        assert refused(client, protocols=["logs.v1", f"ticket.{ticket(client, headers, uid)}"])


# ---- LT11: a client that cannot keep up ----
class StuckSocket:
    """A client that never reads: every send waits forever."""

    def __init__(self):
        self.closed = None

    async def send_text(self, _):
        await asyncio.Event().wait()

    async def receive(self):
        await asyncio.sleep(3600)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class QuietSocket(StuckSocket):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def send_text(self, text):
        self.sent.append(json.loads(text))


def session(*pods, until=None) -> LogSession:
    return LogSession(sub="u", uid="uid", namespace="demo", name="d", pods=list(pods), tail_lines=100, since_time=None,
                      stream_until=until or time.time() + 60)


def test_slow_client_is_closed_1013_and_does_not_block_others():
    gateway = FakeGateway()
    gateway.logs[("demo", "p1")] = log_lines(*[f"l{i}" for i in range(50)])
    gateway.logs[("demo", "p2")] = log_lines("x", "y")
    settings = make_settings(log_send_timeout_seconds=1, log_queue_max_lines=2)  # tiny: bursts must still be safe

    async def both():
        stuck, quiet = StuckSocket(), QuietSocket()
        results = await asyncio.gather(
            run_stream(stuck, session("p1"), gateway, settings, RuntimeState()),
            run_stream(quiet, session("p2"), gateway, settings, RuntimeState()))
        return stuck, quiet, results

    stuck, quiet, (slow, fine) = asyncio.run(both())
    assert slow["code"] == 1013 and stuck.closed[0] == 1013
    assert fine["code"] == 1000 and [f["line"] for f in quiet.sent] == ["x", "y"]


def test_closing_a_stream_releases_every_reader():
    gateway = FakeGateway()
    gateway.keep_open = True
    gateway.logs[("demo", "p1")] = log_lines("a")
    state = RuntimeState()

    async def go():
        task = asyncio.create_task(run_stream(QuietSocket(), session("p1"), gateway, make_settings(), state))
        await asyncio.sleep(0.3)
        state.shutting_down = True
        return await asyncio.wait_for(task, 3)

    assert asyncio.run(go())["code"] == 1001
    assert all(s.closed for s in gateway.log_streams)  # a reader waiting for a line was unblocked


# ---- limits and settings ----
def test_registry_limit_and_ticket_ids():
    now = [100.0]
    reg = StreamRegistry(2, clock=lambda: now[0])
    assert reg.acquire("a") and reg.acquire("a") and not reg.acquire("a") and reg.acquire("b")
    reg.release("a")
    assert reg.acquire("a") and reg.open_count("a") == 2 and reg.total_open() == 3
    assert reg.consume("j1", 130) and not reg.consume("j1", 130)
    now[0] = 200.0
    assert reg.consume("j1", 230)  # expired ids are forgotten


@pytest.mark.parametrize("var", ["LOG_TICKET_TTL_SECONDS", "MAX_LOG_STREAMS_PER_USER", "LOG_QUEUE_MAX_LINES",
                                 "LOG_SEND_TIMEOUT_SECONDS"])
def test_log_settings_must_be_positive(tmp_path, var):
    secret = tmp_path / "bootstrap"
    secret.write_text("x")
    with pytest.raises(ConfigError, match=var):
        load_settings({"BOOTSTRAP_SECRET_FILE": str(secret), var: "0"})
    assert load_settings({"BOOTSTRAP_SECRET_FILE": str(secret), var: "7"})
