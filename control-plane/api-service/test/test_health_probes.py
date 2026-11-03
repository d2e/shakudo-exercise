"""Pod probes (H1-H6, H12-H15) and the shutdown flag (H7)."""
from fastapi.testclient import TestClient

from api_service.app import RuntimeState, create_app
from support import make_settings


def test_liveness_ignores_cluster(client, gateway):
    gateway.fail = "unreachable"
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "alive"}


def test_readiness_follows_cluster(client, gateway):
    assert client.get("/readyz").json() == {"status": "ready"}
    gateway.fail = "unreachable"
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json() == {"status": "not_ready", "checks": {"kubernetes": "failed"}}


def test_readiness_is_live_each_time(client, gateway):
    client.get("/readyz"); client.get("/readyz")
    assert gateway.calls.count("ping") == 2  # N1: no cached result


def test_readiness_503_after_sigterm(bootstrap_file, gateway):
    state = RuntimeState()
    client = TestClient(create_app(make_settings(bootstrap_secret_file=bootstrap_file), gateway, state))
    assert client.get("/readyz").status_code == 200
    state.shutting_down = True
    assert client.get("/readyz").status_code == 503
    assert client.get("/healthz").status_code == 200
