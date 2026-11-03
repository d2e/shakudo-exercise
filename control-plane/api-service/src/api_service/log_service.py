"""Log streaming use cases (D28, req-logs.md): the ticket request, the one stream check, per-replica limits.

No FastAPI imports here. The WebSocket route is not covered by the HTTP middleware, so everything it must
enforce (ticket, Origin, live re-check, the R21 limit) is in `LogService.authorize`, the single check function (LG7).
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import validation as v
from .auth.tokens import AuthError, Principal, TokenService
from .config import Settings
from .errors import ApiError
from .models import LogTicketOut, LogTicketRequest
from .ports import ClusterPort, KubeFailure
from .service import DeploymentService, kube_error

STREAM_PATH = "/api/v1/logs/stream"
SUBPROTOCOL = "logs.v1"
TICKET_PREFIX = "ticket."
STREAMABLE_PHASES = frozenset({"Running", "Succeeded", "Failed"})  # a Pending pod has no log to read yet


class StreamRefused(Exception):
    """The handshake must be refused. `reason` is for the audit log only and never reaches the client."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class StreamRegistry:
    """Open streams per user (R21, LG11) and tickets already used (LG5). Per replica, in memory, no cluster data."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.time):
        self._limit, self._clock = limit, clock
        self._open: dict[str, int] = {}
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()

    def open_count(self, sub: str) -> int:
        with self._lock:
            return self._open.get(sub, 0)

    def total_open(self) -> int:
        with self._lock:
            return sum(self._open.values())

    def acquire(self, sub: str) -> bool:
        with self._lock:
            if self._open.get(sub, 0) >= self._limit:
                return False
            self._open[sub] = self._open.get(sub, 0) + 1
            return True

    def release(self, sub: str) -> None:
        with self._lock:
            left = self._open.get(sub, 0) - 1
            if left > 0:
                self._open[sub] = left
            else:
                self._open.pop(sub, None)

    def consume(self, jti: str, exp: float) -> bool:
        """True the first time a ticket id is seen on this replica, False on a repeat. Expired ids are forgotten."""
        now = self._clock()
        with self._lock:
            for key in [k for k, e in self._used.items() if e < now]:
                del self._used[key]
            if jti in self._used:
                return False
            self._used[jti] = exp
            return True


@dataclass
class LogSession:
    """What an accepted stream needs. `release` gives back the user's stream slot; call it exactly once."""
    sub: str
    uid: str
    namespace: str
    name: str
    pods: list[str]
    tail_lines: int
    since_time: Optional[str]
    stream_until: float
    release: Callable[[], None] = field(default=lambda: None)


class LogService:
    def __init__(self, settings: Settings, cluster: ClusterPort, tokens: TokenService,
                 deployments: DeploymentService, registry: StreamRegistry, clock: Callable[[], float] = time.time):
        self.settings, self.cluster, self.tokens = settings, cluster, tokens
        self.deployments, self.registry, self._clock = deployments, registry, clock

    # ---- POST /api/v1/deployments/{id}/logs/ticket (normal pipeline: auth, rate limit, then these checks) ----
    def issue_ticket(self, principal: Principal, uid: str, req: LogTicketRequest) -> LogTicketOut:
        snap = self.deployments.find(uid)  # 400, 404 (unknown, unmanaged or outside the allowlist, D1, D2), 500
        try:
            pods = self.cluster.pods_of(snap)
        except KubeFailure as e:
            raise kube_error(e, "deployment not found") from None
        if req.pod is not None and req.pod not in {p.name for p in pods}:
            raise ApiError(404, "not_found", "pod not found")
        if self.registry.open_count(principal.sub) >= self.settings.max_log_streams_per_user:  # best effort (LG11)
            raise ApiError(429, "too_many_streams", "too many open log streams")
        ticket, ttl = self.tokens.mint_log_ticket(
            sub=principal.sub, uid=snap.uid, namespace=snap.namespace, pod=req.pod, tail_lines=req.tailLines,
            since_time=req.sinceTime, stream_until=principal.exp)
        return LogTicketOut(ticket=ticket, streamPath=STREAM_PATH, expiresIn=ttl)

    # ---- the one check for the WebSocket route (LG6-LG9, LG11). Blocking: call it in a thread. ----
    def authorize(self, subprotocols: list[str], origin: Optional[str]) -> LogSession:
        tickets = [p[len(TICKET_PREFIX):] for p in subprotocols if p.startswith(TICKET_PREFIX)]
        if SUBPROTOCOL not in subprotocols or len(tickets) != 1 or not tickets[0]:
            raise StreamRefused("no_ticket")
        if origin is not None and origin not in self.settings.cors_allowed_origins:  # LG8: WebSockets ignore CORS
            raise StreamRefused("origin_not_allowed")
        try:
            claims = self.tokens.verify_log_ticket(tickets[0])
        except AuthError as e:
            raise StreamRefused("ticket_expired" if e.expired else "ticket_invalid") from None
        if not self.registry.consume(claims["jti"], claims["exp"]):
            raise StreamRefused("ticket_reused")
        if self._clock() >= claims["stream_until"]:
            raise StreamRefused("token_expired")

        # LG9: about 30 s may have passed, so look again at the allowlist, the deployment and the pods.
        ns, uid, wanted = claims["ns"], claims["uid"], claims.get("pod")
        if ns in v.RESERVED_NAMESPACES or ns not in self.settings.allowed_namespaces:
            raise StreamRefused("namespace_not_allowed")
        try:
            snap = self.cluster.find_by_uid(uid, [ns])
            if snap is None:
                raise StreamRefused("deployment_gone")
            pods = self.cluster.pods_of(snap)
        except KubeFailure:
            raise StreamRefused("cluster_unavailable") from None
        names = [p.name for p in pods]
        if wanted is not None:
            if wanted not in names:
                raise StreamRefused("pod_gone")
            targets = [wanted]
        else:  # D30, LG12: every pod present now, up to the replica cap (R19)
            targets = [p.name for p in pods if p.phase in STREAMABLE_PHASES][: self.settings.max_replicas]

        sub = claims["sub"]
        if not self.registry.acquire(sub):  # authoritative (LG11)
            raise StreamRefused("too_many_streams")
        return LogSession(sub=sub, uid=uid, namespace=ns, name=snap.name, pods=targets, tail_lines=claims["tail"],
                          since_time=claims.get("since"), stream_until=float(claims["stream_until"]),
                          release=lambda: self.registry.release(sub))
