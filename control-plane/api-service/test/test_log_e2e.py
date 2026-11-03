"""D28 end to end over real sockets: uvicorn on a free port, the real `websockets` client, the real handshake.

TestClient proves the logic; this proves the wire: `Sec-WebSocket-Protocol: logs.v1, ticket.<t>` is accepted,
the server selects only logs.v1, and refused handshakes fail with an HTTP status before any upgrade (LG6, LG7).
"""
import json

import pytest
import requests

websockets_sync = pytest.importorskip("websockets.sync.client")
from websockets.exceptions import ConnectionClosed, InvalidStatus  # noqa: E402

from e2e_server import run_api  # noqa: E402
from support import log_lines, pod  # noqa: E402


@pytest.fixture
def api():
    base, gateway, stop = run_api()
    try:
        yield base, gateway, stop
    finally:
        stop()


def deployment(api):
    base, gateway, stop = api
    token = requests.post(f"{base}/api/v1/auth/token", json={"sub": "e2e"}, headers={"X-Bootstrap-Secret": stop.bootstrap_secret})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    created = requests.post(f"{base}/api/v1/deployments", headers=headers,
                            json={"name": "http-echo", "namespace": "demo", "image": "hashicorp/http-echo:1.0.0"})
    assert created.status_code == 201, created.text
    uid = created.json()["uid"]
    gateway.pods[uid] = [pod("p1"), pod("p2")]
    gateway.logs[("demo", "p1")] = log_lines("a1", "a2")
    gateway.logs[("demo", "p2")] = log_lines("b1")
    return headers, uid


def get_ticket(api, headers, uid, **body):
    r = requests.post(f"{api[0]}/api/v1/deployments/{uid}/logs/ticket", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_real_handshake_streams_merged_lines_and_closes_1000(api):
    headers, uid = deployment(api)
    t = get_ticket(api, headers, uid)
    url = api[0].replace("http://", "ws://") + t["streamPath"]
    assert "ticket" not in url  # never in the URL (LG6)
    with websockets_sync.connect(url, subprotocols=["logs.v1", "ticket." + t["ticket"]]) as ws:
        assert ws.subprotocol == "logs.v1"
        frames = []
        with pytest.raises(ConnectionClosed) as closed:
            while True:
                frames.append(json.loads(ws.recv(timeout=5)))
    assert closed.value.rcvd.code == 1000
    assert sorted((f["pod"], f["line"]) for f in frames) == [("p1", "a1"), ("p1", "a2"), ("p2", "b1")]


def test_real_handshake_is_refused_with_an_http_status_and_no_upgrade(api):
    headers, uid = deployment(api)
    t = get_ticket(api, headers, uid)
    url = api[0].replace("http://", "ws://") + t["streamPath"]
    for protocols in (["logs.v1"], ["logs.v1", "ticket.garbage"]):
        with pytest.raises(InvalidStatus) as e:
            websockets_sync.connect(url, subprotocols=protocols)
        assert e.value.response.status_code == 403
    with websockets_sync.connect(url, subprotocols=["logs.v1", "ticket." + t["ticket"]]) as ws:  # first use works
        with pytest.raises(ConnectionClosed):
            while True:
                ws.recv(timeout=5)
    with pytest.raises(InvalidStatus):  # a repeat does not
        websockets_sync.connect(url, subprotocols=["logs.v1", "ticket." + t["ticket"]])


def test_run_api_helper_gives_a_logged_in_client_everything_it_needs(api):
    base, gateway, stop = api
    assert base.startswith("http://127.0.0.1:") and stop.bootstrap_secret and stop.settings.allowed_namespaces == ["demo"]
    assert requests.get(f"{base}/healthz").json() == {"status": "alive"}
    assert requests.get(f"{base}/api/v1/deployments").status_code == 401
