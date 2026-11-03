"""Aggregated deployment health (S4-S8). Pure function: no cluster, no I/O.

Order, first match wins: FAILING, PROGRESSING, DEGRADED, HEALTHY, otherwise UNKNOWN.
"""
from typing import Optional

from .domain import DeploymentSnap, PodSnap

HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
FAILING = "FAILING"
PROGRESSING = "PROGRESSING"
UNKNOWN = "UNKNOWN"
ALL_HEALTH = (HEALTHY, DEGRADED, FAILING, PROGRESSING, UNKNOWN)

FAILURE_REASONS = frozenset({
    "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
    "CrashLoopBackOff", "CreateContainerConfigError",
})


def _condition(d: DeploymentSnap, ctype: str):
    return next((c for c in d.conditions if c.type == ctype), None)


def rollout_in_progress(d: DeploymentSnap) -> bool:
    if d.observed_generation is None or d.observed_generation < d.generation:
        return True
    if d.updated < d.desired or d.replicas > d.desired:
        return True
    progressing = _condition(d, "Progressing")
    return bool(progressing and progressing.status == "True" and progressing.reason != "NewReplicaSetAvailable")


def pod_failure(pods: list[PodSnap]) -> bool:
    return any(
        c.state == "waiting" and c.reason in FAILURE_REASONS
        for p in pods if not p.terminating
        for c in p.containers
    )


def compute_health(d: Optional[DeploymentSnap], pods: Optional[list[PodSnap]] = None) -> str:
    if d is None:
        return UNKNOWN
    pods = pods or []
    progressing = _condition(d, "Progressing")
    if (progressing and progressing.reason == "ProgressDeadlineExceeded") or pod_failure(pods):
        return FAILING
    rolling = rollout_in_progress(d)
    # "no replicas ready" is a failure only when no rollout is under way, so fresh deployments
    # report PROGRESSING while their pods start (refinement of D5, req-status.md).
    if d.desired > 0 and d.ready == 0 and not rolling:
        return FAILING
    if rolling:
        return PROGRESSING
    if 0 < d.ready < d.desired:
        return DEGRADED
    if d.ready == d.desired and d.unavailable == 0:
        return HEALTHY
    return UNKNOWN
