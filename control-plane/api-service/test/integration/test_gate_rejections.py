"""VALIDATION GATE, refusals: a request the API rejects must leave nothing behind in the cluster."""
import uuid

import pytest
from itsupport import body, make_restricted_namespace, stays

UNKNOWN = "11111111-1111-4111-8111-111111111111"


def test_rejected_creates_leave_no_trace(api, oracle, namespace):
    other = f"{namespace}-x"  # not on the allowlist
    try:
        cases = [
            ("invalid image", body(namespace, image="nginx:latest"), 400),
            ("invalid name", body(namespace, name="Bad_Name"), 400),
            ("unknown field", {**body(namespace), "replica": 3}, 400),
            ("namespace not allowed", body(other), 403),
            ("missing secret", body(namespace, env=[{"name": "P", "secretRef": {"name": "nope", "key": "k"}}]), 400),
        ]
        for label, req, expected in cases:
            r = api.call("POST", "", json=req)
            assert r.status_code == expected, f"{label}: {r.status_code} {r.text}"
        oracle.assert_nothing_in(namespace, "rejected creates")
        oracle.assert_nothing_in(other, "namespace outside the allowlist")
    finally:
        oracle.delete_namespace(other)


def test_missing_token_changes_nothing(api, oracle, namespace):
    r = api.tc.post("/api/v1/deployments", json=body(namespace))
    assert r.status_code == 401
    oracle.assert_nothing_in(namespace, "unauthenticated request")


def test_duplicate_and_bad_updates_leave_the_original_untouched(api, oracle, namespace):
    req = body(namespace)
    first = api.call("POST", "", json=req)
    assert first.status_code == 201
    uid = first.json()["uid"]
    snapshot = oracle.deployment(namespace, "echo")

    assert api.call("POST", "", json=req).status_code == 409
    assert api.call("PATCH", f"/{uid}", json={"replicas": -1}).status_code == 400
    assert api.call("PATCH", f"/{uid}", json={"image": "nginx:latest"}).status_code == 400
    assert api.call("PATCH", f"/{uid}", json={"replicas": 999}).status_code == 400
    assert api.call("PATCH", f"/{UNKNOWN}", json={"replicas": 2}).status_code == 404
    assert api.call("DELETE", f"/{UNKNOWN}").status_code == 404

    after = oracle.deployments_in(namespace)
    assert [d.metadata.uid for d in after] == [uid], "duplicate or bad update changed the set of Deployments"
    assert after[0].metadata.generation == snapshot.metadata.generation, "a rejected request modified the Deployment"
    assert after[0].spec.replicas == req["replicas"]


def test_unmanaged_deployment_is_invisible_and_untouched(api, oracle, namespace):
    """D2: a Deployment the API did not create gets 404 on every verb and is never modified."""
    from kubernetes import client as k8s
    make_restricted_namespace(oracle, namespace)
    foreign = k8s.V1Deployment(
        metadata=k8s.V1ObjectMeta(name="foreign"),
        spec=k8s.V1DeploymentSpec(
            replicas=0, selector=k8s.V1LabelSelector(match_labels={"app": "foreign"}),
            template=k8s.V1PodTemplateSpec(metadata=k8s.V1ObjectMeta(labels={"app": "foreign"}),
                                           spec=k8s.V1PodSpec(containers=[k8s.V1Container(name="c", image="nginx:1.27.0")]))))
    created = oracle.apps.create_namespaced_deployment(namespace, foreign)
    uid, generation = created.metadata.uid, created.metadata.generation

    assert api.call("GET", f"/{uid}").status_code == 404
    assert api.call("PATCH", f"/{uid}", json={"replicas": 5}).status_code == 404
    assert api.call("POST", f"/{uid}/restart").status_code == 404
    assert api.call("DELETE", f"/{uid}").status_code == 404
    assert api.call("GET", "", params={"namespace": namespace}).json()["items"] == []

    after = oracle.deployment(namespace, "foreign")
    assert after is not None and after.metadata.generation == generation and after.spec.replicas == 0
    assert not (after.spec.template.metadata.annotations or {}), "restart annotation was written to a foreign Deployment"


def test_namespace_not_made_by_the_api_survives_delete(api, oracle, namespace):
    """D9/R11: only API-created (managed) namespaces are ever removed."""
    make_restricted_namespace(oracle, namespace)  # no managed label
    r = api.call("POST", "", json=body(namespace))
    assert r.status_code == 201 and r.json()["namespaceCreated"] is False
    uid = r.json()["uid"]
    assert api.call("DELETE", f"/{uid}").status_code == 202
    from itsupport import wait_for
    wait_for(lambda: (oracle.deployments_in(namespace), oracle.pods_in(namespace)), lambda t: t == ([], []),
             "Deployment and pods gone")
    assert stays(lambda: oracle.namespace(namespace) is not None and oracle.namespace(namespace).status.phase == "Active"), \
        "a namespace the API did not create was deleted"


def test_secret_reference_is_checked_live_and_stored_as_reference(api, oracle, namespace):
    """R12: labeled Secret accepted as secretKeyRef (value never copied); unlabeled refused, nothing created."""
    from kubernetes import client as k8s
    make_restricted_namespace(oracle, namespace)
    for name, labels in (("allowed", {"control-plane.io/allow-reference": "true"}), ("plain", {})):
        oracle.core.create_namespaced_secret(namespace, k8s.V1Secret(
            metadata=k8s.V1ObjectMeta(name=name, labels=labels), string_data={"pw": "TOPSECRET-VALUE"}))

    ref = lambda n: [{"name": "DB_PASSWORD", "secretRef": {"name": n, "key": "pw"}}]  # noqa: E731
    refused = api.call("POST", "", json=body(namespace, name="refused", env=ref("plain")))
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "secret_not_allowed"
    assert oracle.deployment(namespace, "refused") is None

    ok = api.call("POST", "", json=body(namespace, name="accepted", env=ref("allowed")))
    assert ok.status_code == 201, ok.text
    env = oracle.deployment(namespace, "accepted").spec.template.spec.containers[0].env
    assert env[0].value is None and env[0].value_from.secret_key_ref.name == "allowed"
    assert "TOPSECRET-VALUE" not in ok.text
