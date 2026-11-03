"""Logs controller (D28, req-logs.md): the ticket request, and the WebSocket that only a ticket opens.

The ticket request goes through the normal HTTP pipeline (authentication first, rate limits). The WebSocket
route is not covered by that middleware, so it calls the one check function `LogService.authorize` before
accepting anything (LG7). A refused handshake is closed before `accept`, so there is no upgrade.
"""
import json
import logging
import time

from fastapi import APIRouter, Request, WebSocket
from starlette.concurrency import run_in_threadpool

from ..log_service import STREAM_PATH, SUBPROTOCOL, LogService, StreamRefused
from ..models import LogTicketOut, LogTicketRequest
from ..ports import ClusterPort
from ..streaming import run_stream
from .deployments import errs

audit_log = logging.getLogger("api_service.audit")

TICKET_ERRORS = errs(400, 401, 403, 404, 429, 500)


def _audit(action: str, **fields) -> None:
    """LG16: who, which deployment, which pod, how long. Never log content, never the ticket."""
    audit_log.info(json.dumps({"event": "audit", "action": action, **fields}))


def build_logs_controller(service: LogService, state, settings, cluster: ClusterPort) -> APIRouter:
    router = APIRouter(tags=["Logs"])

    @router.post("/api/v1/deployments/{id}/logs/ticket", response_model=LogTicketOut, operation_id="createLogTicket",
                 summary="Get a short-lived ticket to open the log WebSocket", responses=TICKET_ERRORS)
    def create_ticket(id: str, body: LogTicketRequest, request: Request):
        """Authenticated with the Bearer token. The ticket is valid for 30 s and opens one stream
        (`GET /api/v1/logs/stream`, WebSocket, subprotocols `logs.v1` and `ticket.<value>`)."""
        who = request.state.principal
        out = service.issue_ticket(who, id, body)
        _audit("logs.ticket", request_id=request.state.request_id, sub=who.sub, uid=id, pod=body.pod)
        return out

    @router.websocket(STREAM_PATH)
    async def stream(websocket: WebSocket):
        try:
            if state.shutting_down:
                raise StreamRefused("shutting_down")
            session = await run_in_threadpool(service.authorize, list(websocket.scope.get("subprotocols") or []),
                                              websocket.headers.get("origin"))
        except StreamRefused as e:
            _audit("logs.stream.refused", reason=e.reason)
            await websocket.close(code=1008)  # before accept: the handshake fails, nothing is upgraded
            return
        except Exception as e:  # noqa: BLE001 - an unexpected failure still refuses
            _audit("logs.stream.refused", reason=type(e).__name__)
            await websocket.close(code=1008)
            return
        started = time.monotonic()
        result = {"code": None, "lines": 0}
        try:
            await websocket.accept(subprotocol=SUBPROTOCOL)
            _audit("logs.stream.open", sub=session.sub, uid=session.uid, pods=session.pods,
                   open_streams=service.registry.total_open())
            result = await run_stream(websocket, session, cluster, settings, state)
        finally:
            session.release()
            _audit("logs.stream.close", sub=session.sub, uid=session.uid, code=result["code"], lines=result["lines"],
                   duration_ms=round((time.monotonic() - started) * 1000), open_streams=service.registry.total_open())

    return router
