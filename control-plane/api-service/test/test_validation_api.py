"""Input validation (V1-V24): every invalid case is 400, names the field, and makes no cluster call."""
import pytest

URL = "/api/v1/deployments"


def mutate(body, **changes):
    out = {**body, **changes}
    return {k: v for k, v in out.items() if v is not ...}


CASES = [
    ("empty image", {"image": ""}, "image"),
    ("no tag", {"image": "nginx"}, "image"),
    ("latest", {"image": "nginx:latest"}, "image"),
    ("empty tag", {"image": "nginx:"}, "image"),
    ("double colon", {"image": "nginx::1"}, "image"),
    ("uppercase", {"image": "NGINX:1.0"}, "image"),
    ("space in tag", {"image": "nginx:bad tag"}, "image"),
    ("negative replicas", {"replicas": -1}, "replicas"),
    ("string replicas", {"replicas": "3"}, "replicas"),
    ("bool replicas", {"replicas": True}, "replicas"),
    ("float replicas", {"replicas": 1.5}, "replicas"),
    ("uppercase name", {"name": "Bad"}, "name"),
    ("underscore name", {"name": "a_b"}, "name"),
    ("dot name", {"name": "a.b"}, "name"),
    ("leading dash", {"name": "-a"}, "name"),
    ("empty name", {"name": ""}, "name"),
    ("long name", {"name": "a" * 64}, "name"),
    ("bad namespace", {"namespace": "Bad_NS"}, "namespace"),
    ("reserved namespace", {"namespace": "kube-system"}, "namespace"),
    ("port zero", {"port": 0}, "port"),
    ("port too big", {"port": 70000}, "port"),
    ("missing name", {"name": ...}, "name"),
    ("missing image", {"image": ...}, "image"),
    ("partial resources", {"resources": {"requests": {"cpu": "50m", "memory": "64Mi"}}}, "resources.limits"),
    ("empty resources", {"resources": {}}, "resources"),
    ("unknown field", {"replica": 3}, "replica"),
    ("bad quantity", {"resources": {"requests": {"cpu": "abc", "memory": "64Mi"},
                                    "limits": {"cpu": "1", "memory": "128Mi"}}}, "resources.requests.cpu"),
    ("zero quantity", {"resources": {"requests": {"cpu": "0", "memory": "64Mi"},
                                     "limits": {"cpu": "1", "memory": "128Mi"}}}, "resources.requests.cpu"),
    ("limits below requests", {"resources": {"requests": {"cpu": "500m", "memory": "64Mi"},
                                             "limits": {"cpu": "100m", "memory": "128Mi"}}}, "resources"),
    ("bad env name", {"env": [{"name": "1BAD", "value": "x"}]}, "env.0.name"),
    ("env without value", {"env": [{"name": "A"}]}, "env.0"),
    ("env with both", {"env": [{"name": "A", "value": "x", "secretRef": {"name": "s", "key": "k"}}]}, "env.0"),
    ("duplicate env", {"env": [{"name": "A", "value": "x"}, {"name": "A", "value": "y"}]}, "env"),
    ("bad secret ref", {"env": [{"name": "A", "secretRef": {"name": "Bad_Name", "key": "k"}}]}, "env.0.secretRef.name"),
    ("reserved label", {"labels": {"app.kubernetes.io/managed-by": "x"}}, "labels"),
    ("bad label value", {"labels": {"team": "has space"}}, "labels"),
    ("empty command entry", {"command": [""]}, "command"),
]


@pytest.mark.parametrize("label,changes,field", CASES, ids=[c[0] for c in CASES])
def test_invalid_create_is_400(client, auth, gateway, valid_body, label, changes, field):
    r = client.post(URL, json=mutate(valid_body, **changes), headers=auth)
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "invalid_request"
    assert any(d["field"].startswith(field) for d in err["details"]), err["details"]
    assert gateway.calls == []  # V22


def test_all_violations_reported_at_once(client, auth, valid_body):
    r = client.post(URL, json=mutate(valid_body, name="Bad", replicas=-1, image="nginx"), headers=auth)
    assert {d["field"] for d in r.json()["error"]["details"]} >= {"name", "replicas", "image"}


def test_malformed_json_and_empty_body(client, auth, gateway):
    bad = client.post(URL, content=b"{not json", headers={**auth, "Content-Type": "application/json"})
    empty = client.post(URL, headers=auth)
    assert bad.status_code == 400 and empty.status_code == 400
    assert gateway.calls == []


def test_oversized_body_is_413(make_client, auth, valid_body):
    client = make_client(max_body_bytes=100)
    token = client.post("/api/v1/auth/token", json={"sub": "t"}, headers={"X-Bootstrap-Secret": "test-bootstrap-secret"})
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    assert client.post(URL, json=valid_body, headers=headers).status_code == 413


@pytest.mark.parametrize("body", [{}, {"replicas": -1}, {"replicas": 1, "extra": 1}, {"image": "nginx:latest"}])
def test_invalid_patch_is_400(client, auth, gateway, body):
    uid = "11111111-1111-4111-8111-111111111111"
    assert client.patch(f"{URL}/{uid}", json=body, headers=auth).status_code == 400
    assert gateway.calls == []


def test_invalid_namespace_filter_is_400(client, auth, gateway):
    assert client.get(URL, params={"namespace": "Bad_NS"}, headers=auth).status_code == 400
    assert gateway.calls == []


def test_error_never_echoes_env_values(client, auth, valid_body):
    body = mutate(valid_body, env=[{"name": "1BAD", "value": "hunter2-secret"}])
    assert "hunter2-secret" not in client.post(URL, json=body, headers=auth).text


def test_unversioned_path_is_404(client, auth, valid_body):
    assert client.post("/api/deployments", json=valid_body, headers=auth).status_code == 404
