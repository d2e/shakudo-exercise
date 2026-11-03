"""Port for everything the API needs from the cluster (adapter pattern, D27).

The application layer (service, controllers) depends only on this module. It names no Kubernetes SDK
type: arguments and results are plain dicts, strings and the domain snapshots in domain.py. The
Kubernetes implementation is the adapter in kube.py; tests use a fake adapter (test/support.py).

Contract every adapter must keep:
- Each call goes to the cluster. Nothing is cached (N1-N3).
- The only exception that leaves an adapter is KubeFailure, carrying a fixed kind and no raw
  cluster text, hostnames or RBAC details (R24).
- Results are managed deployments only (D2) and live data only.
"""
from typing import Optional, Protocol, runtime_checkable

from .domain import DeploymentSnap, PodSnap

FAILURE_KINDS = ("not_found", "conflict", "unreachable", "forbidden", "other")


class KubeFailure(Exception):
    """kind: one of FAILURE_KINDS. Carries no raw cluster text."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@runtime_checkable
class ClusterPort(Protocol):
    # ---- dependency liveness (H4) ----
    def ping(self, timeout: int = 2) -> None:
        """Cheap authorized call. Raises KubeFailure when the cluster cannot be used."""

    # ---- namespaces ----
    def namespace_labels(self, name: str) -> Optional[dict]:
        """Labels of the namespace, or None when it does not exist."""

    def ensure_namespace(self, name: str) -> bool:
        """Create the namespace with its defaults if missing. True when this call created it (D9, R10)."""

    def namespace_is_empty(self, name: str) -> bool:
        """Final live check before deleting a namespace (R11). Any failure raises, so nothing is deleted."""

    def delete_namespace(self, name: str) -> None: ...

    # ---- secrets: metadata only, values are never read (R13) ----
    def secret_labels(self, namespace: str, name: str) -> Optional[dict]:
        """Labels of the Secret, or None when it is missing or not readable (R14)."""

    # ---- deployments ----
    def create_deployment(self, manifest: dict) -> DeploymentSnap: ...

    def list_deployments(self, namespace: Optional[str] = None) -> list[DeploymentSnap]: ...

    def find_by_uid(self, uid: str, namespaces: Optional[list[str]] = None) -> Optional[DeploymentSnap]:
        """Live lookup by UID among managed deployments, limited to `namespaces` when given (D1, N2)."""

    def pods_of(self, d: DeploymentSnap) -> list[PodSnap]:
        """Pods owned by the deployment (pod -> ReplicaSet -> deployment)."""

    def patch_deployment(self, namespace: str, name: str, body: dict) -> DeploymentSnap: ...

    def delete_deployment(self, namespace: str, name: str) -> None: ...
