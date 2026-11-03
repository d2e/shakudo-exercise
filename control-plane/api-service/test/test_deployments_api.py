"""Deployment API behaviour with a fake cluster: lifecycle, errors, scope, secrets (Q7-Q13, Q20-Q26, Q31-Q33)."""
import copy
import logging

import pytest

from support import pod

URL = "/api/v1/deployments"
SECRET_LABEL = "control-plane.io/allow-reference"


def create(client, auth, body, status=201):
    r = client.post(URL, json=body, headers=auth)
    assert r.status_code == status, r.text
    return r.json()


def test_lifecycle(client, auth, gateway, valid_body):
    created = create(client, auth, valid_body)
    uid = created["uid"]
    assert created["health"] == "PROGRESSING" and created["namespaceCreated"] is True
    assert created["replicas"]["desired"] == 2

    listed = client.get(URL, params={"namespace": "demo"}, headers=auth).json()["items"]
    assert [d["uid"] for d in listed] == [uid]

    gateway.pods[uid] = [pod("p1", state="waiting", ready=False, reason="ImagePullBackOff")]
    detail = client.get(f"{URL}/{uid}", headers=auth).json()
    assert detail["health"] == "FAILING"
    assert detail["pods"][0]["containerStatuses"][0]["reason"] == "ImagePullBackOff"

    scaled = client.patch(f"{URL}/{uid}", json={"replicas": 4}, headers=auth)
    assert scaled.status_code == 200 and scaled.json()["replicas"]["desired"] == 4
    updated = client.patch(f"{URL}/{uid}", json={"image": "hashicorp/http-echo:1.0.1"}, headers=auth)
    assert updated.json()["image"] == "hashicorp/http-echo:1.0.1"

    restarted = client.post(f"{URL}/{uid}/restart", headers=auth)
    assert restarted.status_code == 202
    patch = gateway.manifests[("demo", "http-echo", "patches")][-1]
    assert "kubectl.kubernetes.io/restartedAt" in patch["spec"]["template"]["metadata"]["annotations"]

    deleted = client.delete(f"{URL}/{uid}", headers=auth)
    assert deleted.status_code == 202 and deleted.json()["status"] == "deleting"
    assert client.get(f"{URL}/{uid}", headers=auth).status_code == 404
    assert client.get(URL, headers=auth).json()["items"] == []
    # D9: the API-created, now empty namespace is removed after the response
    assert "demo" not in gateway.namespaces


def test_second_create_does_not_report_namespace_created(client, auth, valid_body):
    create(client, auth, valid_body)
    second = create(client, auth, {**valid_body, "name": "other"})
    assert second["namespaceCreated"] is False


def test_duplicate_name_is_409(client, auth, valid_body):
    create(client, auth, valid_body)
    r = client.post(URL, json=valid_body, headers=auth)
    assert r.status_code == 409
    assert r.json()["error"]["message"] == "name already in use"


def test_unknown_and_malformed_id(client, auth):
    unknown = "11111111-1111-4111-8111-111111111111"
    for call in (lambda: client.get(f"{URL}/{unknown}", headers=auth),
                 lambda: client.patch(f"{URL}/{unknown}", json={"replicas": 1}, headers=auth),
                 lambda: client.delete(f"{URL}/{unknown}", headers=auth),
                 lambda: client.post(f"{URL}/{unknown}/restart", headers=auth)):
        assert call().status_code == 404
    bad = client.get(f"{URL}/not-a-uid", headers=auth)
    assert bad.status_code == 400 and bad.json()["error"]["details"][0]["field"] == "id"


def test_unmanaged_deployment_is_invisible(client, auth, gateway, valid_body):
    """D2/Q26: the gateway only returns managed deployments, so an unknown uid is 404 and nothing is patched."""
    uid = "22222222-2222-4222-8222-222222222222"
    assert client.patch(f"{URL}/{uid}", json={"replicas": 9}, headers=auth).status_code == 404
    assert "patch_deployment" not in gateway.calls


def test_list_without_filter_only_covers_allowlist(client, auth, gateway, valid_body):
    create(client, auth, valid_body)
    gateway.deployments[("other", "x")] = copy.copy(gateway.deployments[("demo", "http-echo")])
    gateway.deployments[("other", "x")].namespace = "other"
    names = [d["namespace"] for d in client.get(URL, headers=auth).json()["items"]]
    assert names == ["demo"]
    assert client.get(URL, params={"namespace": "other"}, headers=auth).status_code == 403


# ---- namespace scope (R6-R8) ----

@pytest.mark.parametrize("settings,namespace", [
    ({"allowed_namespaces": []}, "demo"),
    ({}, "not-listed"),
    ({"allowed_namespaces": ["kube-system"]}, "kube-system"),
])
def test_namespace_refused_before_any_cluster_call(make_client, gateway, valid_body, settings, namespace):
    client = make_client(**settings)
    token = client.post("/api/v1/auth/token", json={"sub": "t"}, headers={"X-Bootstrap-Secret": "test-bootstrap-secret"})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    r = client.post(URL, json={**valid_body, "namespace": namespace}, headers=headers)
    if namespace == "kube-system":
        assert r.status_code == 400  # reserved names fail validation first
    else:
        assert r.status_code == 403 and r.json()["error"]["code"] == "namespace_not_allowed"
    assert gateway.calls == []


def test_registry_allowlist_and_caps_reported_together(make_client, valid_body, gateway):
    client = make_client(allowed_registries=["registry.example.com"], max_replicas=3)
    token = client.post("/api/v1/auth/token", json={"sub": "t"}, headers={"X-Bootstrap-Secret": "test-bootstrap-secret"})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    body = {**valid_body, "replicas": 10}
    body["resources"]["limits"] = {"cpu": "8", "memory": "128Mi"}
    r = client.post(URL, json=body, headers=headers)
    assert r.status_code == 400
    fields = {d["field"] for d in r.json()["error"]["details"]}
    assert fields == {"image", "replicas", "resources.limits.cpu"}
    assert gateway.calls == []


# ---- secret references (R12, R13) ----

def with_secret(body, name="db"):
    return {**body, "env": [{"name": "PASSWORD", "secretRef": {"name": name, "key": "pw"}}]}


def test_secret_reference_requires_allow_label(client, auth, gateway, valid_body):
    gateway.secrets[("demo", "labeled")] = {SECRET_LABEL: "true"}
    gateway.secrets[("demo", "unlabeled")] = {}
    created = create(client, auth, with_secret(valid_body, "labeled"))
    env = gateway.manifests[("demo", "http-echo")]["spec"]["template"]["spec"]["containers"][0]["env"]
    assert env == [{"name": "PASSWORD", "valueFrom": {"secretKeyRef": {"name": "labeled", "key": "pw"}}}]
    assert created["name"] == "http-echo"

    for name in ("missing", "unlabeled"):  # same answer for both, existence is not revealed
        r = client.post(URL, json=with_secret({**valid_body, "name": "other"}, name), headers=auth)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "secret_not_allowed"
        assert r.json()["error"]["message"] == "secret reference is not allowed"


# ---- cluster failures (R24) ----

@pytest.mark.parametrize("kind,message", [
    ("unreachable", "cluster unreachable"),
    ("forbidden", "operation not permitted by the cluster"),
    ("other", "cluster operation failed"),
])
def test_cluster_failures_are_sanitized(client, auth, gateway, valid_body, kind, message):
    gateway.fail = kind
    r = client.post(URL, json=valid_body, headers=auth)
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["message"] == message and err["request_id"]
    assert r.headers["x-request-id"] == err["request_id"]


# ---- D9 namespace cleanup (R11, Q32) ----

def service_for(gateway, **over):
    from api_service.service import DeploymentService
    from support import make_settings
    return DeploymentService(make_settings(**over), gateway, sleep=lambda _: None)


def test_cleanup_deletes_only_managed_allowlisted_empty_namespace(gateway):
    gateway.namespaces["demo"] = {"app.kubernetes.io/managed-by": "control-plane"}
    assert service_for(gateway).cleanup_namespace("demo", attempts=2) is True
    assert "demo" not in gateway.namespaces


@pytest.mark.parametrize("labels,allowed,empty", [
    ({}, ["demo"], True),                                              # not managed
    ({"app.kubernetes.io/managed-by": "control-plane"}, [], True),     # not allowlisted
    ({"app.kubernetes.io/managed-by": "control-plane"}, ["demo"], False),  # not empty
])
def test_cleanup_refuses(gateway, labels, allowed, empty):
    gateway.namespaces["demo"] = labels
    gateway.empty = empty
    assert service_for(gateway, allowed_namespaces=allowed).cleanup_namespace("demo", attempts=2) is False
    assert "demo" in gateway.namespaces


def test_cleanup_keeps_namespace_when_check_fails(gateway):
    gateway.namespaces["demo"] = {"app.kubernetes.io/managed-by": "control-plane"}
    gateway.fail = "unreachable"
    assert service_for(gateway).cleanup_namespace("demo", attempts=2) is False
    assert "demo" in gateway.namespaces


def test_no_secret_value_in_logs(client, auth, gateway, valid_body, caplog):
    caplog.set_level(logging.DEBUG)
    body = {**valid_body, "env": [{"name": "TOKEN", "value": "super-secret-value", "extra": 1}]}
    client.post(URL, json=body, headers=auth)
    assert "super-secret-value" not in caplog.text


def test_cleanup_waits_longer_than_the_pod_grace_period(gateway):
    """Regression (found by the live gate): a pod ignoring SIGTERM lives the full grace period, so the
    default wait must outlast it or the namespace is never removed."""
    from api_service.builder import TERMINATION_GRACE_SECONDS
    gateway.namespaces["demo"] = {"app.kubernetes.io/managed-by": "control-plane"}
    checks, naps = [], []
    real = gateway.namespace_is_empty

    def empty_only_after_grace(name):
        real(name)
        checks.append(1)
        return sum(naps) >= TERMINATION_GRACE_SECONDS  # pods vanish after one full grace period

    gateway.namespace_is_empty = empty_only_after_grace
    from api_service.service import DeploymentService
    from support import make_settings
    svc = DeploymentService(make_settings(), gateway, sleep=naps.append)
    assert svc.cleanup_namespace("demo") is True and "demo" not in gateway.namespaces


def test_cleanup_logs_why_it_kept_the_namespace(gateway, caplog):
    caplog.set_level(logging.INFO, logger="api_service.service")
    gateway.namespaces["demo"] = {}
    service_for(gateway).cleanup_namespace("demo", attempts=2)
    gateway.namespaces["demo"] = {"app.kubernetes.io/managed-by": "control-plane"}
    gateway.empty = False
    service_for(gateway).cleanup_namespace("demo", attempts=2)
    assert "not created by the API" in caplog.text and "still not empty after waiting" in caplog.text


def test_namespace_created_only_appears_on_create(client, auth, gateway, valid_body):
    created = create(client, auth, valid_body)
    uid = created["uid"]
    assert created["namespaceCreated"] is True
    assert "namespaceCreated" not in client.get(f"{URL}/{uid}", headers=auth).json()
    assert all("namespaceCreated" not in d for d in client.get(URL, headers=auth).json()["items"])
    assert "namespaceCreated" not in client.patch(f"{URL}/{uid}", json={"replicas": 1}, headers=auth).json()
    assert "namespaceCreated" not in client.post(f"{URL}/{uid}/restart", headers=auth).json()


def test_detail_marks_terminating_pods(client, auth, gateway, valid_body):
    uid = create(client, auth, valid_body)["uid"]
    old = pod("old", state="running")
    old.terminating = True
    gateway.pods[uid] = [pod("new"), old]
    pods = {p["name"]: p for p in client.get(f"{URL}/{uid}", headers=auth).json()["pods"]}
    assert pods["old"]["terminating"] is True and pods["new"]["terminating"] is False
