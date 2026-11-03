"""VALIDATION GATE, least privilege: the API running as the chart's ServiceAccount, not as admin.

Everything here talks to the cluster with the ServiceAccount token only. The oracle (admin) confirms
the result. This is where gaps in the chart's RBAC show up.
"""
import pytest
from itsupport import body, build_api, make_restricted_namespace, wait_for
from kubernetes import client as k8s

from api_service.config import Settings
from rbac_support import SYS_NS, deprovision, provision

PRE, NEW = "cp-rbac-a", "cp-rbac-new"  # PRE exists at install (gets the chart's Role); NEW is created on demand


@pytest.fixture(scope="module")
def sa(cluster_ok, oracle, tmp_path_factory):
    for ns in (SYS_NS, PRE):
        make_restricted_namespace(oracle, ns)
    identity = provision([PRE])  # the chart can only template Roles for namespaces that already exist
    try:
        yield identity
    finally:
        deprovision(identity)
        for ns in (PRE, NEW, SYS_NS):
            oracle.delete_namespace(ns)
        wait_for(lambda: [oracle.namespace(n) for n in (PRE, NEW, SYS_NS)], lambda r: not any(r),
                 "RBAC test namespaces removed", timeout=120)


@pytest.fixture
def sa_api(sa, bootstrap_file):
    return build_api(Settings(
        bootstrap_secret_file=bootstrap_file, allowed_namespaces=[PRE, NEW],
        kube_api_server=sa.server, kube_ca_file=sa.ca_file, kube_token_file=sa.token_file,
        rate_limit_read_per_minute=100000, rate_limit_mutate_per_minute=100000, rate_limit_auth_per_minute=100000))


@pytest.fixture(autouse=True)
def clean(oracle, sa):
    yield
    for ns in (PRE, NEW):
        for d in oracle.deployments_in(ns):
            oracle.apps.delete_namespaced_deployment(d.metadata.name, ns, propagation_policy="Background")
    oracle.delete_namespace(NEW)
    wait_for(lambda: oracle.namespace(NEW), lambda n: n is None, "on-demand namespace removed", timeout=120)


def test_permission_matrix_matches_the_design(sa):
    """What the ServiceAccount may and may not do, asked of the real authorizer."""
    allowed = [("create", "deployments"), ("patch", "deployments"), ("delete", "deployments"), ("list", "deployments"),
               ("create", "namespaces"), ("delete", "namespaces"), ("get", "namespaces"), ("list", "namespaces"),
               ("list", "pods"), ("get", "pods/log"), ("list", "replicasets"), ("create", "resourcequotas"),
               ("create", "limitranges")]
    denied = [("update", "deployments"), ("get", "secrets"), ("list", "secrets"), ("create", "secrets"),
              ("create", "pods"), ("delete", "pods"), ("create", "pods/exec"), ("create", "services"),
              ("list", "nodes"), ("create", "clusterroles"), ("create", "rolebindings"), ("create", "serviceaccounts"),
              ("delete", "customresourcedefinitions"), ("patch", "namespaces")]
    wrong = [f"{v} {r} should be allowed" for v, r in allowed if not sa.can_i(v, r)]
    wrong += [f"{v} {r} should be DENIED" for v, r in denied if sa.can_i(v, r)]
    assert not wrong, "\n".join(wrong)
    # the secret and emptiness reads are namespace-scoped, never cluster-wide (R14)
    assert sa.can_i("get", "secrets", PRE) and sa.can_i("list", "configmaps", PRE)
    assert not sa.can_i("get", "secrets", "kube-system") and not sa.can_i("get", "secrets", NEW)


def test_readiness_works_with_least_privilege(sa_api):
    assert sa_api.tc.get("/readyz").status_code == 200


def test_lifecycle_in_a_provisioned_namespace(sa_api, oracle):
    """Every API action works as the ServiceAccount, confirmed by the admin oracle."""
    req = body(PRE, replicas=2)
    r = sa_api.call("POST", "", json=req)
    assert r.status_code == 201, r.text
    uid = r.json()["uid"]
    assert r.json()["namespaceCreated"] is False
    oracle.assert_created_as_requested(req, r.json())

    wait_for(lambda: oracle.ready_pods(PRE, "echo"), lambda p: len(p) == 2, "2 Ready pods")
    oracle.assert_status_agrees(PRE, "echo", sa_api.wait_health(uid, "HEALTHY"))

    assert sa_api.call("PATCH", f"/{uid}", json={"replicas": 1}).status_code == 200
    assert oracle.deployment(PRE, "echo").spec.replicas == 1
    assert sa_api.call("PATCH", f"/{uid}", json={"image": "hashicorp/http-echo:0.2.3"}).status_code == 200
    assert sa_api.call("POST", f"/{uid}/restart").status_code == 202
    assert "kubectl.kubernetes.io/restartedAt" in oracle.deployment(PRE, "echo").spec.template.metadata.annotations
    detail = sa_api.call("GET", f"/{uid}")
    assert detail.status_code == 200 and detail.json()["pods"], detail.text  # pod listing and ownership lookup

    assert sa_api.call("DELETE", f"/{uid}").status_code == 202
    wait_for(lambda: oracle.deployment(PRE, "echo"), lambda d: d is None, "Deployment gone")
    assert sa_api.call("GET", f"/{uid}").status_code == 404


def test_secret_reference_in_a_provisioned_namespace(sa_api, oracle):
    """R12 via the chart's Role: the labeled Secret is readable here, the unlabeled one is refused."""
    for name, labels in (("allowed", {"control-plane.io/allow-reference": "true"}), ("plain", {})):
        oracle.core.create_namespaced_secret(PRE, k8s.V1Secret(
            metadata=k8s.V1ObjectMeta(name=name, labels=labels), string_data={"pw": "x"}))
    ref = lambda n: [{"name": "P", "secretRef": {"name": n, "key": "pw"}}]  # noqa: E731
    try:
        assert sa_api.call("POST", "", json=body(PRE, name="no", env=ref("plain"))).status_code == 400
        ok = sa_api.call("POST", "", json=body(PRE, name="yes", env=ref("allowed")))
        assert ok.status_code == 201, ok.text
    finally:
        for s in ("allowed", "plain"):
            oracle.core.delete_namespaced_secret(s, PRE)


def test_on_demand_namespace_under_least_privilege(sa_api, oracle):
    """D9 under RBAC. The chart cannot template a Role into a namespace that does not exist yet, so for an
    API-created namespace the emptiness check cannot complete, and R11 says then do NOT delete it.
    This test records the real outcome so the conflict with D9 is visible rather than assumed."""
    r = sa_api.call("POST", "", json=body(NEW))
    assert r.status_code == 201, r.text
    assert r.json()["namespaceCreated"] is True
    oracle.assert_namespace_provisioned(NEW)  # SA could create the namespace, quota and limit range
    uid = r.json()["uid"]

    refused = sa_api.call("POST", "", json=body(NEW, name="withsecret",
                          env=[{"name": "P", "secretRef": {"name": "s", "key": "k"}}]))
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "secret_not_allowed"

    assert sa_api.call("DELETE", f"/{uid}").status_code == 202
    wait_for(lambda: oracle.deployment(NEW, "echo"), lambda d: d is None, "Deployment gone")
    wait_for(lambda: oracle.pods_in(NEW), lambda p: p == [], "pods gone")
    ns = oracle.namespace(NEW)
    assert ns is not None and ns.status.phase == "Active", (
        "the namespace WAS deleted: either the emptiness check no longer needs the missing permissions "
        "or R11 is being bypassed; check which")
