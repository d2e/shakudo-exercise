"""Kubernetes adapter: implements ClusterPort (ports.py) with the official Python client (D27, F3).

The only module that imports the Kubernetes SDK or talks to the cluster (N1-N5, R24). It translates
in both directions: domain calls become SDK calls, SDK objects become domain snapshots, and SDK
errors become KubeFailure kinds, so no SDK type, raw Kubernetes text, hostname or RBAC detail
travels up. Every call goes to the API server; nothing is cached (N1-N3). The client object is
shared, which holds no cluster data (N5).
"""
import logging
import urllib3
from pathlib import Path
from typing import Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from .builder import (MANAGED_BY_VALUE, build_limit_range, build_namespace, build_resource_quota)
from .config import Settings
from .domain import ConditionSnap, ContainerSnap, DeploymentSnap, PodSnap
from .ports import ClusterPort, KubeFailure
from .validation import MANAGED_BY_LABEL

logger = logging.getLogger("api_service.kube")

MANAGED_SELECTOR = f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
# Kinds that must be absent before an API-created namespace is deleted (R11).
EMPTINESS_KINDS = ("deployments", "statefulsets", "daemonsets", "jobs", "cronjobs",
                   "pods", "services", "persistentvolumeclaims", "secrets", "configmaps")


def classify(exc: Exception) -> KubeFailure:
    if isinstance(exc, ApiException):
        kind = {404: "not_found", 409: "conflict", 401: "forbidden", 403: "forbidden"}.get(exc.status, "other")
        if exc.status not in (404, 409):  # expected outcomes are not worth a log line
            logger.warning("kubernetes call failed", extra={"status": exc.status, "reason": exc.reason})
        return KubeFailure(kind)
    if isinstance(exc, (urllib3.exceptions.HTTPError, OSError)):
        logger.warning("kubernetes unreachable: %s", type(exc).__name__)
        return KubeFailure("unreachable")
    logger.error("kubernetes client error: %s", type(exc).__name__)
    return KubeFailure("other")


def _iso(ts) -> Optional[str]:
    return ts.isoformat().replace("+00:00", "Z") if ts else None


def _api_client(settings: Settings) -> client.ApiClient:
    if settings.kube_api_server:
        cfg = client.Configuration()
        cfg.host = settings.kube_api_server
        cfg.verify_ssl = settings.kube_verify_tls
        cfg.ssl_ca_cert = settings.kube_ca_file or None
        cfg.api_key = {"authorization": "Bearer " + Path(settings.kube_token_file).read_text().strip()}
        return client.ApiClient(cfg)
    if settings.kubeconfig:
        return config.new_client_from_config(config_file=settings.kubeconfig,
                                             context=settings.kube_context or None)
    try:
        config.load_incluster_config()
    except config.ConfigException:
        return config.new_client_from_config(context=settings.kube_context or None)
    return client.ApiClient()


def to_deployment_snap(d) -> DeploymentSnap:
    status, spec, meta = d.status, d.spec, d.metadata
    containers = spec.template.spec.containers or []
    conditions = [
        ConditionSnap(type=c.type, status=c.status, reason=c.reason, message=c.message,
                      last_transition_time=_iso(c.last_transition_time), last_update_time=_iso(c.last_update_time))
        for c in (status.conditions or [])
    ] if status else []
    return DeploymentSnap(
        uid=meta.uid, name=meta.name, namespace=meta.namespace,
        image=containers[0].image if containers else "",
        generation=meta.generation or 0,
        observed_generation=status.observed_generation if status else None,
        desired=spec.replicas if spec.replicas is not None else 1,
        replicas=(status.replicas or 0) if status else 0,
        ready=(status.ready_replicas or 0) if status else 0,
        available=(status.available_replicas or 0) if status else 0,
        unavailable=(status.unavailable_replicas or 0) if status else 0,
        updated=(status.updated_replicas or 0) if status else 0,
        conditions=conditions, created_at=_iso(meta.creation_timestamp),
        selector=(spec.selector.match_labels or {}) if spec.selector else {},
        resource_version=meta.resource_version,
    )


def to_pod_snap(p) -> PodSnap:
    containers = []
    for cs in (p.status.container_statuses or []) if p.status else []:
        st = cs.state
        if st and st.running:
            state, reason, message = "running", None, None
        elif st and st.terminated:
            state, reason, message = "terminated", st.terminated.reason, st.terminated.message
        else:
            w = st.waiting if st else None
            state, reason, message = "waiting", (w.reason if w else None), (w.message if w else None)
        containers.append(ContainerSnap(name=cs.name, ready=bool(cs.ready), restart_count=cs.restart_count or 0,
                                        state=state, reason=reason, message=message))
    return PodSnap(
        name=p.metadata.name, phase=(p.status.phase if p.status else None) or "Unknown",
        node=p.spec.node_name if p.spec else None,
        restart_count=sum(c.restart_count for c in containers),
        containers=containers, terminating=p.metadata.deletion_timestamp is not None,
    )


class KubernetesAdapter(ClusterPort):
    def __init__(self, settings: Settings, api_client: Optional[client.ApiClient] = None):
        self._timeout = settings.kube_request_timeout_seconds
        api = api_client or _api_client(settings)
        self._core, self._apps = client.CoreV1Api(api), client.AppsV1Api(api)
        self._batch = client.BatchV1Api(api)

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, _request_timeout=self._timeout, **kwargs)
        except KubeFailure:
            raise
        except Exception as e:  # noqa: BLE001 - classified, never propagated raw
            raise classify(e) from None

    # ---- liveness of the dependency (H4) ----
    def ping(self, timeout: int = 2) -> None:
        try:
            self._core.list_namespace(limit=1, _request_timeout=timeout)
        except Exception as e:  # noqa: BLE001
            raise classify(e) from None

    # ---- namespaces ----
    def namespace_labels(self, name: str) -> Optional[dict]:
        try:
            return self._call(self._core.read_namespace, name).metadata.labels or {}
        except KubeFailure as e:
            if e.kind == "not_found":
                return None
            raise

    def ensure_namespace(self, name: str) -> bool:
        """Create the namespace if missing. Returns True when this call created it (D9, R10)."""
        if self.namespace_labels(name) is not None:
            return False
        try:
            self._call(self._core.create_namespace, build_namespace(name))
        except KubeFailure as e:
            if e.kind == "conflict":  # lost a race: someone else created it
                return False
            raise
        for create, body in ((self._core.create_namespaced_resource_quota, build_resource_quota(name)),
                             (self._core.create_namespaced_limit_range, build_limit_range(name))):
            try:
                self._call(create, name, body)
            except KubeFailure as e:
                if e.kind != "conflict":
                    raise
        return True

    def namespace_is_empty(self, name: str) -> bool:
        """Final live check before deleting a namespace (R11). Any failure raises, so nothing is deleted."""
        lists = {
            "deployments": self._apps.list_namespaced_deployment,
            "statefulsets": self._apps.list_namespaced_stateful_set,
            "daemonsets": self._apps.list_namespaced_daemon_set,
            "jobs": self._batch.list_namespaced_job,
            "cronjobs": self._batch.list_namespaced_cron_job,
            "pods": self._core.list_namespaced_pod,
            "services": self._core.list_namespaced_service,
            "persistentvolumeclaims": self._core.list_namespaced_persistent_volume_claim,
            "secrets": self._core.list_namespaced_secret,
            "configmaps": self._core.list_namespaced_config_map,
        }
        for kind in EMPTINESS_KINDS:
            items = self._call(lists[kind], name).items or []
            if kind == "configmaps":
                items = [i for i in items if i.metadata.name != "kube-root-ca.crt"]
            if items:
                return False
        return True

    def delete_namespace(self, name: str) -> None:
        self._call(self._core.delete_namespace, name)

    # ---- secrets (metadata only is used, R13) ----
    def secret_labels(self, namespace: str, name: str) -> Optional[dict]:
        try:
            return self._call(self._core.read_namespaced_secret, name, namespace).metadata.labels or {}
        except KubeFailure as e:
            if e.kind in ("not_found", "forbidden"):  # forbidden: no Role here (R14), treated as missing
                return None
            raise

    # ---- deployments ----
    def create_deployment(self, manifest: dict) -> DeploymentSnap:
        ns = manifest["metadata"]["namespace"]
        return to_deployment_snap(self._call(self._apps.create_namespaced_deployment, ns, manifest))

    def list_deployments(self, namespace: Optional[str] = None) -> list[DeploymentSnap]:
        if namespace:
            res = self._call(self._apps.list_namespaced_deployment, namespace, label_selector=MANAGED_SELECTOR)
        else:
            res = self._call(self._apps.list_deployment_for_all_namespaces, label_selector=MANAGED_SELECTOR)
        return [to_deployment_snap(d) for d in res.items or []]

    def find_by_uid(self, uid: str, namespaces: Optional[list[str]] = None) -> Optional[DeploymentSnap]:
        """Live lookup by UID among managed deployments (D1, N2). Scoped to allowed namespaces."""
        if namespaces is None:
            candidates = self.list_deployments()
        else:
            candidates = [d for ns in namespaces for d in self.list_deployments(ns)]
        return next((d for d in candidates if d.uid == uid), None)

    def pods_of(self, d: DeploymentSnap) -> list[PodSnap]:
        """Pods found by selector, then checked by ownership: pod -> ReplicaSet -> this deployment."""
        selector = ",".join(f"{k}={v}" for k, v in d.selector.items())
        rs_list = self._call(self._apps.list_namespaced_replica_set, d.namespace, label_selector=selector).items or []
        owned_rs = {rs.metadata.uid for rs in rs_list
                    if any(o.kind == "Deployment" and o.uid == d.uid for o in rs.metadata.owner_references or [])}
        pods = self._call(self._core.list_namespaced_pod, d.namespace, label_selector=selector).items or []
        return [to_pod_snap(p) for p in pods
                if any(o.kind == "ReplicaSet" and o.uid in owned_rs for o in p.metadata.owner_references or [])]

    def patch_deployment(self, namespace: str, name: str, body: dict) -> DeploymentSnap:
        return to_deployment_snap(self._call(self._apps.patch_namespaced_deployment, name, namespace, body))

    def delete_deployment(self, namespace: str, name: str) -> None:
        self._call(self._apps.delete_namespaced_deployment, name, namespace, propagation_policy="Foreground")
