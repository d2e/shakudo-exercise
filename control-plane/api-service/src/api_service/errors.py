"""Structured, sanitized errors (V2, V3, R24). Invalid input is 400, never FastAPI's default 422."""
import logging
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("api_service")

# Fixed messages for cluster failures: no raw Kubernetes text, hostnames or RBAC details (R24).
KUBE_MESSAGES = {
    "unreachable": "cluster unreachable",
    "forbidden": "operation not permitted by the cluster",
    "other": "cluster operation failed",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 details: Optional[list[dict]] = None, headers: Optional[dict] = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details, headers or {}


def error_body(code: str, message: str, request_id: str = "", details: Optional[list[dict]] = None) -> dict:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if details:
        err["details"] = details
    return {"error": err}


def request_id_of(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def error_response(request: Request, e: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=e.status,
        content=error_body(e.code, e.message, request_id_of(request), e.details),
        headers=e.headers,
    )


def _field(loc: tuple) -> str:
    parts = [str(p) for p in loc if p not in ("body", "query", "path")]
    return ".".join(parts) or "body"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Report every violation at once (V3). Only 'msg' is used: never the input value (V7).
        details = [
            {"field": _field(err["loc"]), "rule": err["type"],
             "message": str(err.get("msg", "invalid value")).removeprefix("Value error, ")}
            for err in exc.errors()
        ]
        return error_response(request, ApiError(400, "invalid_request", "request validation failed", details))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        message = {404: "resource not found", 405: "method not allowed"}.get(exc.status_code, "request failed")
        return error_response(request, ApiError(exc.status_code, code, message))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        logger.error("unhandled error", extra={"error_type": type(exc).__name__})
        return error_response(request, ApiError(500, "internal_error", KUBE_MESSAGES["other"]))
