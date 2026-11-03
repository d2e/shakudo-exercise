"""Manifest hardening (D8, AGENTS.md) and the pure health mapping (S4-S8, Q14-Q17)."""
import pytest

from api_service import health as h
from api_service.builder import build_deployment, build_namespace, patch_body
from api_service.domain import ConditionSnap, DeploymentSnap
from api_service.models import DeploymentCreate
from support import pod


def create_req(**over):
    base = {"name": "web", "namespace": "demo", "image": "nginx:1.27.0", "port": 8080,
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "100m", "memory": "128Mi"}},
            "labels": {"team": "a"}}
    return DeploymentCreate(**{**base, **over})


def test_pod_spec_is_pss_restricted():
    m = build_deployment(create_req(command=["/bin/app"], args=["--x"]))
    spec = m["spec"]["template"]["spec"]
    c = spec["containers"][0]
    assert c["securityContext"] == {
        "runAsNonRoot": True, "runAsUser": 65532, "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"}}
    assert spec["terminationGracePeriodSeconds"] == 30
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert {"name": "tmp", "mountPath": "/tmp"} in c["volumeMounts"]
    assert spec["volumes"][0]["emptyDir"]["medium"] == "Memory"
    assert c["resources"]["requests"] and c["resources"]["limits"]
    assert c["readinessProbe"] and c["livenessProbe"]
    assert c["command"] == ["/bin/app"] and c["args"] == ["--x"]
    assert ":latest" not in c["image"] and m["spec"]["progressDeadlineSeconds"] == 60


def test_labels_and_selector_carry_managed_label():
    m = build_deployment(create_req())
    labels = m["metadata"]["labels"]
    assert labels["team"] == "a" and labels["app.kubernetes.io/managed-by"] == "control-plane"
    assert m["spec"]["template"]["metadata"]["labels"] == labels
    assert m["spec"]["selector"]["matchLabels"]["app.kubernetes.io/name"] == "web"


def test_no_port_means_no_probes_or_ports():
    c = build_deployment(create_req(port=None))["spec"]["template"]["spec"]["containers"][0]
    assert "ports" not in c and "readinessProbe" not in c


def test_new_namespace_enforces_restricted():
    labels = build_namespace("demo")["metadata"]["labels"]
    assert labels["pod-security.kubernetes.io/enforce"] == "restricted"
    assert labels["app.kubernetes.io/managed-by"] == "control-plane"


def test_patch_body_changes_only_requested_fields():
    assert patch_body(3, None, "web") == {"spec": {"replicas": 3}}
    body = patch_body(None, "nginx:1.28.0", "web")
    assert body["spec"]["template"]["spec"]["containers"] == [{"name": "web", "image": "nginx:1.28.0"}]


# ---- health ----

def dep(**kw):
    base = dict(uid="u", name="n", namespace="ns", generation=1, observed_generation=1, desired=3, replicas=3,
                ready=3, available=3, unavailable=0, updated=3,
                conditions=[ConditionSnap("Progressing", "True", "NewReplicaSetAvailable")])
    return DeploymentSnap(**{**base, **kw})


def terminating(p):
    p.terminating = True
    return p


def cond(reason, ctype="Progressing", status="True"):
    return [ConditionSnap(ctype, status, reason)]


CASES = [
    ("healthy", dep(), [], h.HEALTHY),
    ("scaled to zero is healthy", dep(desired=0, replicas=0, ready=0, available=0, updated=0), [], h.HEALTHY),
    ("partial readiness", dep(ready=1, available=1, unavailable=2), [], h.DEGRADED),
    ("none ready, no rollout", dep(ready=0, available=0, unavailable=3), [], h.FAILING),
    ("startup, none ready", dep(ready=0, available=0, unavailable=3, conditions=cond("NewReplicaSetCreated")), [], h.PROGRESSING),
    ("new generation not observed", dep(observed_generation=0), [], h.PROGRESSING),
    ("updated below desired", dep(updated=1), [], h.PROGRESSING),
    ("old pods draining", dep(replicas=4), [], h.PROGRESSING),
    ("deadline exceeded", dep(conditions=cond("ProgressDeadlineExceeded", status="False")), [], h.FAILING),
    ("image pull failure", dep(), [pod(state="waiting", ready=False, reason="ImagePullBackOff")], h.FAILING),
    ("crash loop", dep(), [pod(state="waiting", ready=False, reason="CrashLoopBackOff")], h.FAILING),
    ("missing secret", dep(), [pod(state="waiting", ready=False, reason="CreateContainerConfigError")], h.FAILING),
    ("failing beats progressing", dep(updated=1), [pod(state="waiting", ready=False, reason="ErrImagePull")], h.FAILING),
    ("progressing beats degraded", dep(ready=1, available=1, updated=1), [], h.PROGRESSING),
    ("terminating pod ignored", dep(), [terminating(pod(state="waiting", ready=False, reason="ImagePullBackOff"))], h.HEALTHY),
    ("missing deployment", None, [], h.UNKNOWN),
    ("ready above desired but unavailable", dep(unavailable=1), [], h.UNKNOWN),
]


@pytest.mark.parametrize("label,d,pods,expected", CASES, ids=[c[0] for c in CASES])
def test_health_mapping(label, d, pods, expected):
    assert h.compute_health(d, pods) == expected
