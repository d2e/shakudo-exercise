"""Deployments controller: /api/v1/deployments (P1).

Authentication is not done here: it is enforced for every route by the request pipeline (A2).
Handlers are plain `def`, so the synchronous Kubernetes client runs in the threadpool (F3).
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request

from ..models import (DeleteOut, DeploymentCreate, DeploymentCreatedOut, DeploymentDetailOut, DeploymentListOut,
                      DeploymentOut, DeploymentPatch, Error)
from ..service import DeploymentService

audit_log = logging.getLogger("api_service.audit")


def errs(*codes: int) -> dict:
    return {code: {"model": Error} for code in codes}


CREATE_ERRORS = errs(400, 401, 403, 409, 429, 500)
LIST_ERRORS = errs(400, 401, 403, 429, 500)
ID_ERRORS = errs(400, 401, 403, 404, 429, 500)


def _audit(request: Request, action: str, **fields) -> None:
    who = request.state.principal
    audit_log.info(json.dumps({"event": "audit", "action": action, "request_id": request.state.request_id,
                               "sub": who.sub if who else None, "iss": who.iss if who else None, **fields}))


def build_deployments_controller(service: DeploymentService) -> APIRouter:
    router = APIRouter(prefix="/api/v1/deployments", tags=["Deployments"])

    @router.post("", status_code=201, response_model=DeploymentCreatedOut, operation_id="createDeployment",
                 summary="Create a deployment (creates the namespace if missing)", responses=CREATE_ERRORS,
                 response_model_exclude_none=True)
    def create(body: DeploymentCreate, request: Request):
        out = service.create(body)
        _audit(request, "deployment.create", namespace=out.namespace, name=out.name, uid=out.uid)
        return out

    @router.get("", response_model=DeploymentListOut, operation_id="listDeployments",
                summary="List managed deployments, optionally for one namespace", responses=LIST_ERRORS)
    def list_deployments(namespace: Optional[str] = Query(None, description="Filter by namespace")):
        return DeploymentListOut(items=service.list(namespace))

    @router.get("/{id}", response_model=DeploymentDetailOut, operation_id="getDeployment",
                summary="Deployment status with pods and conditions", responses=ID_ERRORS)
    def get(id: str):
        return service.get(id)

    @router.patch("/{id}", response_model=DeploymentOut, operation_id="patchDeployment",
                  summary="Scale and/or update the image (rolling update)", responses=ID_ERRORS)
    def patch(id: str, body: DeploymentPatch, request: Request):
        out = service.patch(id, body)
        _audit(request, "deployment.patch", uid=id, replicas=body.replicas, image=body.image)
        return out

    @router.post("/{id}/restart", status_code=202, response_model=DeploymentOut, operation_id="restartDeployment",
                 summary="Rollout restart", responses=ID_ERRORS)
    def restart(id: str, request: Request):
        out = service.restart(id)
        _audit(request, "deployment.restart", uid=id)
        return out

    @router.delete("/{id}", status_code=202, response_model=DeleteOut, operation_id="deleteDeployment",
                   summary="Delete with cascade; an emptied API-created namespace is removed", responses=ID_ERRORS)
    def delete(id: str, request: Request, background: BackgroundTasks):
        out, namespace = service.delete(id)
        _audit(request, "deployment.delete", uid=id, namespace=namespace)
        background.add_task(service.cleanup_namespace, namespace)  # D9, after the response is sent
        return out

    return router
