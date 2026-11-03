"""Unit-test helpers: a hand-written fake Kubernetes gateway (Q1), settings and snapshot builders."""
import itertools

from api_service.builder import MANAGED_BY_VALUE
from api_service.config import Settings
from api_service.domain import ConditionSnap, ContainerSnap, DeploymentSnap, PodSnap
from api_service.ports import KubeFailure
from api_service.validation import MANAGED_BY_LABEL

BOOTSTRAP = "test-bootstrap-secret"


class FakeGateway:
    """In-memory stand-in. Records every call so tests can assert 'no cluster call' (V22, R7)."""

    def __init__(self):
        self.calls: list[str] = []
        self.namespaces: dict[str, dict] = {}
        self.deployments: dict[tuple, DeploymentSnap] = {}
        self.manifests: dict[tuple, dict] = {}
        self.secrets: dict[tuple, dict] = {}
        self.pods: dict[str, list[PodSnap]] = {}
        self.empty = True
        self.fail: str | None = None  # a KubeFailure kind raised by every call
        self._uids = (f"00000000-0000-4000-8000-{n:012d}" for n in itertools.count(1))

    def _enter(self, name: str):
        self.calls.append(name)
        if self.fail:
            raise KubeFailure(self.fail)

    def ping(self, timeout: int = 2):
        self._enter("ping")

    def namespace_labels(self, name):
        self._enter("namespace_labels")
        return self.namespaces.get(name)

    def ensure_namespace(self, name):
        self._enter("ensure_namespace")
        if name in self.namespaces:
            return False
        self.namespaces[name] = {MANAGED_BY_LABEL: MANAGED_BY_VALUE}
        return True

    def namespace_is_empty(self, name):
        self._enter("namespace_is_empty")
        return self.empty

    def delete_namespace(self, name):
        self._enter("delete_namespace")
        self.namespaces.pop(name, None)

    def secret_labels(self, namespace, name):
        self._enter("secret_labels")
        return self.secrets.get((namespace, name))

    def create_deployment(self, manifest):
        self._enter("create_deployment")
        meta = manifest["metadata"]
        key = (meta["namespace"], meta["name"])
        if key in self.deployments:
            raise KubeFailure("conflict")
        spec = manifest["spec"]
        snap = DeploymentSnap(
            uid=next(self._uids), name=meta["name"], namespace=meta["namespace"],
            image=spec["template"]["spec"]["containers"][0]["image"], generation=1, observed_generation=1,
            desired=spec["replicas"], replicas=spec["replicas"], ready=0, available=0,
            unavailable=spec["replicas"], updated=spec["replicas"], created_at="2026-01-01T00:00:00Z",
            selector=spec["selector"]["matchLabels"],
            conditions=[ConditionSnap("Progressing", "True", "NewReplicaSetCreated")])
        self.deployments[key] = snap
        self.manifests[key] = manifest
        return snap

    def list_deployments(self, namespace=None):
        self._enter("list_deployments")
        return [d for d in self.deployments.values() if namespace in (None, d.namespace)]

    def find_by_uid(self, uid, namespaces=None):
        self._enter("find_by_uid")
        return next((d for d in self.deployments.values()
                     if d.uid == uid and (namespaces is None or d.namespace in namespaces)), None)

    def pods_of(self, d):
        self._enter("pods_of")
        return self.pods.get(d.uid, [])

    def patch_deployment(self, namespace, name, body):
        self._enter("patch_deployment")
        self.manifests.setdefault((namespace, name, "patches"), []).append(body)
        d = self.deployments[(namespace, name)]
        spec = body.get("spec", {})
        if "replicas" in spec:
            d.desired = spec["replicas"]
        for c in spec.get("template", {}).get("spec", {}).get("containers", []):
            d.image = c["image"]
        return d

    def delete_deployment(self, namespace, name):
        self._enter("delete_deployment")
        self.deployments.pop((namespace, name))


def make_settings(**over) -> Settings:
    base = dict(bootstrap_secret_file="", allowed_namespaces=["demo"], mock_auth_enabled=True)
    return Settings(**{**base, **over})




def pod(name="p1", reason=None, state="running", ready=True, restarts=0, phase="Running"):
    return PodSnap(name=name, phase=phase, node="node-1", restart_count=restarts,
                   containers=[ContainerSnap("c", ready, restarts, state, reason)])
