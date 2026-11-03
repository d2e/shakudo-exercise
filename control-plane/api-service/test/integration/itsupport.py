"""Integration helpers: request bodies, polling (Q5), and the Api wrapper around the real app."""
import time

import pytest
from fastapi.testclient import TestClient
from kubernetes import client as k8s

from api_service.app import create_app
from api_service.config import Settings
from api_service.kube import KubernetesAdapter

BOOTSTRAP = "integration-bootstrap"
IMAGE = "hashicorp/http-echo:1.0.0"
POLL_TIMEOUT = 90  # Q5


def body(namespace, name="echo", **over):
    return {"name": name, "namespace": namespace, "image": IMAGE, "replicas": 1, "port": 5678,
            "resources": {"requests": {"cpu": "25m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
            "args": ["-text=hello", "-listen=:5678"], **over}


def wait_for(fetch, predicate, what, timeout=POLL_TIMEOUT, interval=1.0):
    """Poll until predicate(fetch()) is true. No fixed sleeps; reports the last value on timeout (Q5)."""
    deadline, last = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        last = fetch()
        if predicate(last):
            return last
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout}s waiting for {what}; last value: {last}")


class Api:
    def __init__(self, tc: TestClient, headers: dict, gateway: KubernetesAdapter):
        self.tc, self.headers, self.gateway = tc, headers, gateway  # no kube clients: assert via the oracle only

    def call(self, method, path, **kw):
        return self.tc.request(method, f"/api/v1/deployments{path}", headers=self.headers, **kw)

    def detail(self, uid):
        r = self.call("GET", f"/{uid}")
        assert r.status_code == 200, r.text
        return r.json()

    def wait_health(self, uid, wanted, timeout=POLL_TIMEOUT):
        return wait_for(lambda: self.detail(uid), lambda d: d["health"] == wanted, f"health {wanted}", timeout)


def build_api(settings: Settings) -> Api:
    gateway = KubernetesAdapter(settings)
    tc = TestClient(create_app(settings, gateway=gateway), raise_server_exceptions=False)
    token = tc.post("/api/v1/auth/token", json={"sub": "integration"}, headers={"X-Bootstrap-Secret": BOOTSTRAP})
    assert token.status_code == 200, token.text
    return Api(tc, {"Authorization": f"Bearer {token.json()['access_token']}"}, gateway)


def make_restricted_namespace(oracle, name, managed=False):
    """Pre-create a namespace directly in the cluster (the API did not make it)."""
    labels = {"pod-security.kubernetes.io/enforce": "restricted"}
    if managed:
        labels["app.kubernetes.io/managed-by"] = "control-plane"
    oracle.core.create_namespace(k8s.V1Namespace(metadata=k8s.V1ObjectMeta(name=name, labels=labels)))


def stays(predicate, seconds=4.0, interval=0.5) -> bool:
    """True when predicate holds on every poll for `seconds`: a bounded negative check, not a fixed sleep."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not predicate():
            return False
        time.sleep(interval)
    return predicate()
