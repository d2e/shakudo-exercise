"""Unit-test fixtures: an app with mock auth on, backed by the fake gateway (helpers live in support.py)."""
import pytest
from fastapi.testclient import TestClient

from api_service.app import create_app
from support import BOOTSTRAP, FakeGateway, make_settings

@pytest.fixture
def bootstrap_file(tmp_path):
    p = tmp_path / "bootstrap"
    p.write_text(BOOTSTRAP)
    return str(p)


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def make_client(bootstrap_file, gateway):
    def build(**over) -> TestClient:
        settings = make_settings(bootstrap_secret_file=bootstrap_file, **over)
        return TestClient(create_app(settings, gateway=gateway), raise_server_exceptions=False)
    return build


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def auth(client):
    r = client.post("/api/v1/auth/token", json={"sub": "tester"}, headers={"X-Bootstrap-Secret": BOOTSTRAP})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def valid_body():
    return {"name": "http-echo", "namespace": "demo", "image": "hashicorp/http-echo:1.0.0", "replicas": 2,
            "port": 5678, "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                                        "limits": {"cpu": "200m", "memory": "128Mi"}}}
