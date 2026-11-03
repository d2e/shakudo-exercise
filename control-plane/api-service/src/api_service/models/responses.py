"""Response models for deployments, pods and conditions (S1-S3)."""
from typing import Optional

from pydantic import BaseModel, Field


class Replicas(BaseModel):
    desired: int
    ready: int
    available: int
    unavailable: int


class ConditionOut(BaseModel):
    type: str
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    lastTransitionTime: Optional[str] = None


class DeploymentOut(BaseModel):
    uid: str
    name: str
    namespace: str
    image: str
    replicas: Replicas
    conditions: list[ConditionOut]
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None
    health: str = Field(..., description="HEALTHY, DEGRADED, FAILING, PROGRESSING or UNKNOWN")


class DeploymentCreatedOut(DeploymentOut):
    """Create response only: whether this call also created the namespace (D9) and used default resources (D31)."""
    namespaceCreated: bool
    resourcesDefaulted: bool


class ContainerStatusOut(BaseModel):
    name: str
    ready: bool
    restartCount: int
    state: str
    reason: Optional[str] = None
    message: Optional[str] = None


class PodOut(BaseModel):
    name: str
    phase: str
    restartCount: int
    node: Optional[str] = None
    terminating: bool = Field(False, description="Pod is being deleted (old pods during a rollout, or after a delete)")
    containerStatuses: list[ContainerStatusOut]


class DeploymentDetailOut(DeploymentOut):
    pods: list[PodOut]


class DeploymentListOut(BaseModel):
    items: list[DeploymentOut]


class DeleteOut(BaseModel):
    uid: str
    name: str
    namespace: str
    status: str = "deleting"
