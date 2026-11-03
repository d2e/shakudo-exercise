"""D31: `resources` is optional on create. Omitted means the configured defaults; given means complete."""
import pytest

from api_service.builder import build_deployment
from api_service.config import ConfigError, load_settings, validate_settings
from api_service.models import DeploymentCreate
from support import BOOTSTRAP, make_settings

URL = "/api/v1/deployments"
DEFAULTS = {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}}


def headers(client):
    r = client.post("/api/v1/auth/token", json={"sub": "tester"}, headers={"X-Bootstrap-Secret": BOOTSTRAP})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def container(gateway):
    manifest = gateway.manifests[("demo", "http-echo")]
    return manifest["spec"]["template"]["spec"]["containers"][0]


def test_omitted_resources_get_the_defaults(client, auth, gateway, valid_body):
    del valid_body["resources"]
    r = client.post(URL, json=valid_body, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["resourcesDefaulted"] is True
    assert container(gateway)["resources"] == DEFAULTS  # the pod always has all four values


def test_given_resources_are_used_and_not_flagged(client, auth, gateway, valid_body):
    r = client.post(URL, json=valid_body, headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["resourcesDefaulted"] is False
    assert container(gateway)["resources"] == valid_body["resources"]


def test_configured_defaults_apply(make_client, gateway, valid_body):
    client = make_client(default_cpu_request="100m", default_cpu_limit="1", default_memory_request="32Mi",
                         default_memory_limit="64Mi")
    del valid_body["resources"]
    r = client.post(URL, json=valid_body, headers=headers(client))
    assert r.status_code == 201, r.text
    assert container(gateway)["resources"] == {"requests": {"cpu": "100m", "memory": "32Mi"},
                                                "limits": {"cpu": "1", "memory": "64Mi"}}


@pytest.mark.parametrize("partial", [
    {"requests": {"cpu": "50m", "memory": "64Mi"}},
    {"limits": {"cpu": "200m", "memory": "128Mi"}},
    {"requests": {"cpu": "50m"}, "limits": {"cpu": "200m", "memory": "128Mi"}},
])
def test_partial_resources_are_rejected_not_merged(client, auth, gateway, valid_body, partial):
    valid_body["resources"] = partial
    r = client.post(URL, json=valid_body, headers=auth)
    assert r.status_code == 400
    assert gateway.calls == []  # V22


def test_builder_refuses_unresolved_resources(valid_body):
    del valid_body["resources"]
    with pytest.raises(ValueError, match="resolved"):
        build_deployment(DeploymentCreate(**valid_body))


@pytest.mark.parametrize("env,fragment", [
    ({"DEFAULT_CPU_LIMIT": "abc"}, "DEFAULT_CPU_LIMIT"),
    ({"DEFAULT_MEMORY_REQUEST": "0"}, "DEFAULT_MEMORY_REQUEST"),
    ({"DEFAULT_CPU_REQUEST": "1", "DEFAULT_CPU_LIMIT": "500m"}, "DEFAULT_CPU_LIMIT: must be at least"),
    ({"DEFAULT_MEMORY_LIMIT": "8Gi"}, "DEFAULT_MEMORY_LIMIT: must not exceed"),
    ({"DEFAULT_CPU_LIMIT": "4"}, "DEFAULT_CPU_LIMIT: must not exceed"),
])
def test_bad_defaults_stop_startup(tmp_path, env, fragment):
    secret = tmp_path / "bootstrap"
    secret.write_text("x")
    with pytest.raises(ConfigError, match=fragment):
        load_settings({"BOOTSTRAP_SECRET_FILE": str(secret), **env})


def test_shipped_defaults_are_valid():
    assert [e for e in validate_settings(make_settings()) if e.startswith("DEFAULT_")] == []
