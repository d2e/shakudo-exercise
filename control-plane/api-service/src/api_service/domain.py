"""Plain data snapshots of cluster state. Decouples health logic and the API from Kubernetes types."""
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_RFC3339 = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$")


def log_time_key(ts: Optional[str]) -> Optional[tuple[float, int]]:
    """Orderable key for an RFC 3339 time with up to nanoseconds, or None when it is not one.

    Kubernetes trims trailing zeros from log timestamps, so strings cannot be compared as text.
    """
    m = _RFC3339.match(ts or "")
    if not m:
        return None
    try:
        base = datetime.fromisoformat(m.group(1) + ("+00:00" if m.group(3) == "Z" else m.group(3)))
    except ValueError:
        return None
    return base.timestamp(), int(m.group(2).ljust(9, "0")) if m.group(2) else 0


@dataclass
class LogLine:
    """One log line with the time Kubernetes recorded for it (RFC 3339, may be empty if unknown)."""
    time: str
    line: str


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
