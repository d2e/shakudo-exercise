"""Probes controller: liveness and readiness of the API pod itself (H1-H6, H7).

Unauthenticated and unversioned (A3, P3). Never returns URLs or error text.
"""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..ports import ClusterPort, KubeFailure


def build_probes_controller(kube: ClusterPort, state) -> APIRouter:
    router = APIRouter(tags=["Health"])

    @router.get("/healthz", operation_id="liveness", summary="Liveness: process is up")
    def healthz():
        return {"status": "alive"}  # never depends on anything external (H2)

    @router.get("/readyz", operation_id="readiness", summary="Readiness: cluster reachable")
    def readyz():
        checks = {}
        if state.shutting_down:
            checks["shutdown"] = "in_progress"  # H7
        else:
            try:
                kube.ping()  # live call, no cached result (H4)
            except KubeFailure:
                checks["kubernetes"] = "failed"  # no URLs or error text (H6)
        if checks:
            return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
        return {"status": "ready"}

    return router
