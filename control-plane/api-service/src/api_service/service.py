"""Deployment use cases. Order of checks per A1/R7: input (400), allowlist (403), then the cluster.

The cluster adapter (ClusterPort) is injected, so tests use a hand-written fake (Q1). Nothing here keeps cluster state (N1).
"""
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from . import builder, validation as v
from .config import Settings
from .domain import DeploymentSnap, PodSnap
from .errors import KUBE_MESSAGES, ApiError
from .health import compute_health
from .ports import ClusterPort, KubeFailure
from .models import (ConditionOut, ContainerStatusOut, DeleteOut, DeploymentCreate, DeploymentDetailOut,
                     DeploymentCreatedOut, DeploymentOut, DeploymentPatch, PodOut, QuantityPair, Replicas, Resources)

logger = logging.getLogger("api_service.service")
CLEANUP_MARGIN_SECONDS = 30  # wait = pod grace period + this


def kube_error(e: KubeFailure, not_found: str = "resource not found") -> ApiError:
    """Fixed, sanitized messages only (R24, R25)."""
    if e.kind == "not_found":
        return ApiError(404, "not_found", not_found)
    if e.kind == "conflict":
        return ApiError(409, "conflict", "name already in use")
    msg = KUBE_MESSAGES.get(e.kind, KUBE_MESSAGES["other"])
    return ApiError(500, "cluster_error", msg)


def _out(d: DeploymentSnap, pods: Optional[list[PodSnap]] = None) -> dict:
    return {
        "uid": d.uid, "name": d.name, "namespace": d.namespace, "image": d.image,
        "replicas": Replicas(desired=d.desired, ready=d.ready, available=d.available, unavailable=d.unavailable),
        "conditions": [ConditionOut(type=c.type, status=c.status, reason=c.reason, message=c.message,
                                    lastTransitionTime=c.last_transition_time) for c in d.conditions],
        "createdAt": d.created_at, "updatedAt": d.updated_at, "health": compute_health(d, pods),
    }


def deployment_out(d: DeploymentSnap, pods: Optional[list[PodSnap]] = None) -> DeploymentOut:
    return DeploymentOut(**_out(d, pods))


def detail_out(d: DeploymentSnap, pods: list[PodSnap]) -> DeploymentDetailOut:
    return DeploymentDetailOut(**_out(d, pods), pods=[
        PodOut(name=p.name, phase=p.phase, restartCount=p.restart_count, node=p.node, terminating=p.terminating,
               containerStatuses=[ContainerStatusOut(name=c.name, ready=c.ready, restartCount=c.restart_count,
                                                     state=c.state, reason=c.reason, message=c.message)
                                  for c in p.containers])
        for p in pods])


class DeploymentService:
    def __init__(self, settings: Settings, gateway: ClusterPort, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.settings, self.kube, self._sleep, self._clock = settings, gateway, sleep, clock

    # ---- static checks that depend on settings (before any cluster call, V1) ----
    def _image_problems(self, image: str, field: str = "image") -> list[dict]:
        allowed = self.settings.allowed_registries
        if allowed and v.parse_image(image).registry not in allowed:
            return [{"field": field, "rule": "registry_not_allowed", "message": "image registry is not allowed"}]
        return []

    def _limit_problems(self, req: DeploymentCreate) -> list[dict]:
        s, out = self.settings, []
        if req.replicas > s.max_replicas:
            out.append({"field": "replicas", "rule": "too_large",
                        "message": f"replicas must be at most {s.max_replicas}"})
        if v.parse_quantity(req.resources.limits.cpu) > v.parse_quantity(s.max_cpu_limit):
            out.append({"field": "resources.limits.cpu", "rule": "too_large",
                        "message": f"cpu limit must be at most {s.max_cpu_limit}"})
        if v.parse_quantity(req.resources.limits.memory) > v.parse_quantity(s.max_memory_limit):
            out.append({"field": "resources.limits.memory", "rule": "too_large",
                        "message": f"memory limit must be at most {s.max_memory_limit}"})
        return out

    @staticmethod
    def _raise(problems: list[dict]) -> None:
        if problems:
            raise ApiError(400, "invalid_request", "request validation failed", problems)

    def check_namespace(self, namespace: str) -> None:
        """R6-R8: empty allowlist denies all; reserved namespaces are refused even if listed."""
        if namespace in v.RESERVED_NAMESPACES or namespace not in self.settings.allowed_namespaces:
            raise ApiError(403, "namespace_not_allowed", "namespace is not allowed")

    def _scope(self, namespace: Optional[str]) -> list[str]:
        if namespace:
            try:
                v.check_dns_label(namespace, "namespace")
            except ValueError as e:
                raise ApiError(400, "invalid_request", "request validation failed",
                               [{"field": "namespace", "rule": "invalid", "message": str(e)}]) from None
            self.check_namespace(namespace)
            return [namespace]
        return [n for n in self.settings.allowed_namespaces if n not in v.RESERVED_NAMESPACES]

    def _check_secrets(self, req: DeploymentCreate) -> None:
        for e in req.env:
            if e.secretRef is None:
                continue
            labels = self.kube.secret_labels(req.namespace, e.secretRef.name)
            if labels is None or labels.get(builder.ALLOW_SECRET_LABEL) != "true":
                # identical answer for missing and unlabeled Secrets (R12)
                raise ApiError(400, "secret_not_allowed", "secret reference is not allowed",
                               [{"field": f"env.{e.name}.secretRef", "rule": "secret_not_allowed",
                                 "message": "secret reference is not allowed"}])

    def _with_resources(self, req: DeploymentCreate) -> tuple[DeploymentCreate, bool]:
        """D31: an omitted `resources` becomes the configured defaults. Returns (request, defaulted)."""
        if req.resources is not None:
            return req, False
        s = self.settings
        defaults = Resources(requests=QuantityPair(cpu=s.default_cpu_request, memory=s.default_memory_request),
                             limits=QuantityPair(cpu=s.default_cpu_limit, memory=s.default_memory_limit))
        return req.model_copy(update={"resources": defaults}), True

    # ---- use cases ----
    def create(self, req: DeploymentCreate) -> DeploymentCreatedOut:
        req, defaulted = self._with_resources(req)
        self._raise(self._image_problems(req.image) + self._limit_problems(req))
        self.check_namespace(req.namespace)
        try:
            self._check_secrets(req)
            created_ns = self.kube.ensure_namespace(req.namespace)
            snap = self.kube.create_deployment(builder.build_deployment(req, self.settings.progress_deadline_seconds))
        except KubeFailure as e:
            raise kube_error(e) from None
        return DeploymentCreatedOut(**_out(snap), namespaceCreated=created_ns, resourcesDefaulted=defaulted)

    def list(self, namespace: Optional[str]) -> list[DeploymentOut]:
        scope = self._scope(namespace)
        try:
            return [deployment_out(d) for ns in scope for d in self.kube.list_deployments(ns)]
        except KubeFailure as e:
            raise kube_error(e) from None

    def _find(self, uid: str) -> DeploymentSnap:
        try:
            v.check_uid(uid)
        except ValueError as e:
            raise ApiError(400, "invalid_request", "request validation failed",
                           [{"field": "id", "rule": "invalid", "message": str(e)}]) from None
        try:
            snap = self.kube.find_by_uid(uid, self._scope(None))
        except KubeFailure as e:
            raise kube_error(e) from None
        if snap is None:
            raise ApiError(404, "not_found", "deployment not found")
        return snap

    def find(self, uid: str) -> DeploymentSnap:
        """The managed deployment in an allowed namespace, or 400, 404 or 500 (same rules as get)."""
        return self._find(uid)

    def get(self, uid: str) -> DeploymentDetailOut:
        snap = self._find(uid)
        try:
            return detail_out(snap, self.kube.pods_of(snap))
        except KubeFailure as e:
            raise kube_error(e, "deployment not found") from None

    def patch(self, uid: str, body: DeploymentPatch) -> DeploymentOut:
        problems = self._image_problems(body.image) if body.image else []
        if body.replicas is not None and body.replicas > self.settings.max_replicas:
            problems.append({"field": "replicas", "rule": "too_large",
                             "message": f"replicas must be at most {self.settings.max_replicas}"})
        self._raise(problems)
        snap = self._find(uid)
        try:
            patched = self.kube.patch_deployment(
                snap.namespace, snap.name, builder.patch_body(body.replicas, body.image, snap.name))
        except KubeFailure as e:
            raise kube_error(e, "deployment not found") from None
        return deployment_out(patched)

    def restart(self, uid: str) -> DeploymentOut:
        snap = self._find(uid)
        stamp = self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            patched = self.kube.patch_deployment(snap.namespace, snap.name, builder.restart_body(stamp))
        except KubeFailure as e:
            raise kube_error(e, "deployment not found") from None
        return deployment_out(patched)

    def delete(self, uid: str) -> tuple[DeleteOut, str]:
        """Foreground delete (cascade). Returns the body and the namespace for cleanup (D9)."""
        snap = self._find(uid)
        try:
            self.kube.delete_deployment(snap.namespace, snap.name)
        except KubeFailure as e:
            raise kube_error(e, "deployment not found") from None
        return DeleteOut(uid=snap.uid, name=snap.name, namespace=snap.namespace), snap.namespace

    def cleanup_namespace(self, namespace: str, attempts: Optional[int] = None, interval: float = 1.0) -> bool:
        """D9 and R11: delete the namespace only if allowlisted, managed, and live-checked empty.

        Foreground deletion keeps the Deployment and its pods until they are gone, and a pod that ignores
        SIGTERM lives for the whole grace period. The wait therefore defaults to the grace period plus a
        margin (found by the live gate: a window equal to the grace period gave up just too early).
        Every outcome that keeps the namespace is logged with its reason. Any failure keeps it.
        """
        log = {"namespace": namespace}
        try:
            self.check_namespace(namespace)
        except ApiError:
            logger.info("namespace kept: not on the allowlist", extra=log)
            return False
        attempts = attempts or int((builder.TERMINATION_GRACE_SECONDS + CLEANUP_MARGIN_SECONDS) / interval)
        try:
            labels = self.kube.namespace_labels(namespace)
            if labels is None:
                logger.info("namespace kept: already gone", extra=log)
                return False
            if labels.get(v.MANAGED_BY_LABEL) != builder.MANAGED_BY_VALUE:
                logger.info("namespace kept: not created by the API", extra=log)
                return False
            for i in range(attempts):
                if self.kube.namespace_is_empty(namespace):
                    self.kube.delete_namespace(namespace)
                    logger.info("namespace deleted", extra=log)
                    return True
                if i < attempts - 1:
                    self._sleep(interval)
            logger.warning("namespace kept: still not empty after waiting", extra={**log, "attempts": attempts})
        except KubeFailure as e:
            logger.warning("namespace kept: cluster check failed", extra={**log, "kind": e.kind})
        return False
