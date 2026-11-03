"""Pure builders for the Kubernetes objects the API creates (D2, D6, D8, R10). No I/O.

Hardening is applied here, server-side, and cannot be relaxed by the caller (D8): PSS restricted
security context, a memory-backed /tmp, probes, a 30 s grace period and a progress deadline.
"""
from typing import Optional

from .models import DeploymentCreate
from .validation import MANAGED_BY_LABEL, NAME_LABEL

MANAGED_BY_VALUE = "control-plane"
ALLOW_SECRET_LABEL = "control-plane.io/allow-reference"
RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"
PSS_ENFORCE_LABEL = "pod-security.kubernetes.io/enforce"
TERMINATION_GRACE_SECONDS = 30  # AGENTS.md; cleanup after delete must outwait this
RUN_AS_USER = 65532  # explicit uid: most images declare no numeric user, runAsNonRoot needs one


def selector_labels(name: str) -> dict:
    return {NAME_LABEL: name, MANAGED_BY_LABEL: MANAGED_BY_VALUE}


def _env(item) -> dict:
    if item.secretRef is not None:
        return {"name": item.name, "valueFrom": {"secretKeyRef": {
            "name": item.secretRef.name, "key": item.secretRef.key}}}
    return {"name": item.name, "value": item.value}


def build_deployment(req: DeploymentCreate, progress_deadline: int = 60) -> dict:
    """Deployment manifest as a plain dict. Caller labels are applied first so reserved keys win."""
    if req.resources is None:  # the service applies the defaults first (D31); the pod always has all four values
        raise ValueError("resources must be resolved before building the manifest")
    labels = {**req.labels, **selector_labels(req.name)}
    container: dict = {
        "name": req.name,
        "image": req.image,
        "resources": {
            "requests": {"cpu": req.resources.requests.cpu, "memory": req.resources.requests.memory},
            "limits": {"cpu": req.resources.limits.cpu, "memory": req.resources.limits.memory},
        },
        "securityContext": {
            "runAsNonRoot": True, "runAsUser": RUN_AS_USER,
            "allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]}, "seccompProfile": {"type": "RuntimeDefault"},
        },
        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
    }
    if req.env:
        container["env"] = [_env(e) for e in req.env]
    if req.command is not None:
        container["command"] = list(req.command)
    if req.args is not None:
        container["args"] = list(req.args)
    if req.port is not None:
        container["ports"] = [{"containerPort": req.port, "protocol": "TCP"}]
        probe = {"tcpSocket": {"port": req.port}}
        container["readinessProbe"] = {**probe, "initialDelaySeconds": 2, "periodSeconds": 5}
        container["livenessProbe"] = {**probe, "initialDelaySeconds": 10, "periodSeconds": 10,
                                      "failureThreshold": 3}
    return {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": req.name, "namespace": req.namespace, "labels": labels},
        "spec": {
            "replicas": req.replicas,
            "progressDeadlineSeconds": progress_deadline,
            "selector": {"matchLabels": selector_labels(req.name)},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "automountServiceAccountToken": False,
                    "terminationGracePeriodSeconds": TERMINATION_GRACE_SECONDS,
                    "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                    "containers": [container],
                    "volumes": [{"name": "tmp", "emptyDir": {"medium": "Memory", "sizeLimit": "16Mi"}}],
                },
            },
        },
    }


def build_namespace(name: str) -> dict:
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": {
        "name": name, "labels": {MANAGED_BY_LABEL: MANAGED_BY_VALUE, PSS_ENFORCE_LABEL: "restricted"}}}


def build_resource_quota(namespace: str) -> dict:
    """Default quota for namespaces the API creates (R10, values are an assumption)."""
    return {"apiVersion": "v1", "kind": "ResourceQuota",
            "metadata": {"name": "control-plane-default", "namespace": namespace,
                         "labels": {MANAGED_BY_LABEL: MANAGED_BY_VALUE}},
            "spec": {"hard": {"pods": "50", "requests.cpu": "10", "requests.memory": "20Gi",
                              "limits.cpu": "20", "limits.memory": "40Gi"}}}


def build_limit_range(namespace: str) -> dict:
    return {"apiVersion": "v1", "kind": "LimitRange",
            "metadata": {"name": "control-plane-default", "namespace": namespace,
                         "labels": {MANAGED_BY_LABEL: MANAGED_BY_VALUE}},
            "spec": {"limits": [{"type": "Container",
                                 "default": {"cpu": "200m", "memory": "128Mi"},
                                 "defaultRequest": {"cpu": "50m", "memory": "64Mi"}}]}}


def patch_body(replicas: Optional[int], image: Optional[str], container_name: str) -> dict:
    """Strategic-merge patch for PATCH (V18): containers merge by name, so only the image changes."""
    body: dict = {}
    if replicas is not None:
        body["spec"] = {"replicas": replicas}
    if image is not None:
        body.setdefault("spec", {})["template"] = {"spec": {"containers": [{"name": container_name, "image": image}]}}
    return body


def restart_body(timestamp: str) -> dict:
    return {"spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: timestamp}}}}}
