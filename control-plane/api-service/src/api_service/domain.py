"""Plain data snapshots of cluster state. Decouples health logic and the API from Kubernetes types."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ContainerSnap:
    name: str
    ready: bool = False
    restart_count: int = 0
    state: str = "waiting"  # waiting | running | terminated
    reason: Optional[str] = None
    message: Optional[str] = None


@dataclass
class PodSnap:
    name: str
    phase: str = "Unknown"
    node: Optional[str] = None
    restart_count: int = 0
    containers: list[ContainerSnap] = field(default_factory=list)
    terminating: bool = False


@dataclass
class ConditionSnap:
    type: str
    status: str
    reason: Optional[str] = None
    message: Optional[str] = None
    last_transition_time: Optional[str] = None
    last_update_time: Optional[str] = None


@dataclass
class DeploymentSnap:
    uid: str
    name: str
    namespace: str
    image: str = ""
    generation: int = 0
    observed_generation: Optional[int] = None
    desired: int = 0
    replicas: int = 0
    ready: int = 0
    available: int = 0
    unavailable: int = 0
    updated: int = 0
    conditions: list[ConditionSnap] = field(default_factory=list)
    created_at: Optional[str] = None
    selector: dict = field(default_factory=dict)
    resource_version: Optional[str] = None

    @property
    def updated_at(self) -> Optional[str]:
        """Latest condition update time, else creation time (a Deployment has no single update stamp)."""
        stamps = [c.last_update_time for c in self.conditions if c.last_update_time]
        return max(stamps) if stamps else self.created_at
