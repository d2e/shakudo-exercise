"""Request pipeline: request id, authentication first (D17, A1-A5), rate limits, headers, logging.

Authentication is enforced here, in one place (A2): every path is protected unless it is on the
explicit public list (A3). It runs before body parsing and validation, so an unauthenticated
caller only ever sees 401 (A4).
"""
import json
import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .auth.tokens import AuthError, TokenService
from .config import Settings
from .errors import KUBE_MESSAGES, ApiError, error_body
from .ratelimit import RateLimiter

access_log = logging.getLogger("api_service.access")

PUBLIC_PATHS = frozenset({
    "/healthz", "/readyz",
    "/api/v1/auth/token", "/api/v1/auth/refresh",
    "/.well-known/openid-configuration", "/.well-known/jwks.json",
})
# WebSocket routes the HTTP middleware never sees. Each one must validate a ticket before accepting (A3, LG7, LT9).
TICKET_AUTH_PATHS = frozenset({"/api/v1/logs/stream"})
DOC_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})
RATE_LIMITED_AUTH_PATHS = frozenset({"/api/v1/auth/token", "/api/v1/auth/refresh"})
MUTATING = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _json(status: int, code: str, message: str, rid: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_body(code, message, rid), headers=headers or {})


def install_security(app: FastAPI, settings: Settings, tokens: TokenService, limiter: RateLimiter) -> None:
    public = set(PUBLIC_PATHS) | (set(DOC_PATHS) if settings.docs_enabled else set())

    @app.middleware("http")
    async def pipeline(request: Request, call_next):
        started = time.perf_counter()
        inbound = request.headers.get("x-request-id", "")
        rid = inbound if _REQUEST_ID.match(inbound) else uuid.uuid4().hex
        request.state.request_id = rid
        request.state.principal = None
        path = request.url.path
        client = request.client.host if request.client else "unknown"

        response = await _guard(request, call_next, path, client, rid)

        response.headers["X-Request-ID"] = rid
        response.headers["Cache-Control"] = "no-store"
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if path not in ("/healthz", "/readyz"):  # probes are not logged at info (H10)
            principal = request.state.principal
            access_log.info(json.dumps({
                "event": "request", "request_id": rid, "http.request.method": request.method,
                "http.route": getattr(request.scope.get("route"), "path", path),
                "http.response.status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "enduser.id": principal.sub if principal else None,
            }))
        return response

    async def _guard(request: Request, call_next, path: str, client: str, rid: str):
        # 1. auth endpoints: rate limit by client address before anything else (R4, R18)
        if path in RATE_LIMITED_AUTH_PATHS:
            wait = limiter.check("auth", client, settings.rate_limit_auth_per_minute)
            if wait:
                return _json(429, "rate_limited", "too many requests", rid, {"Retry-After": str(wait)})
        # 2. authenticate (A1): default deny
        if path not in public:
            header = request.headers.get("authorization", "")
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                return _json(401, "unauthenticated", "missing bearer token", rid,
                             {"WWW-Authenticate": "Bearer"})
            try:
                request.state.principal = tokens.verify(token.strip(), "access")
            except AuthError as e:
                challenge = 'Bearer error="invalid_token"'
                if e.expired:
                    challenge += ', error_description="token expired"'
                return _json(401, "invalid_token", e.message if e.expired else "invalid token", rid,
                             {"WWW-Authenticate": challenge})
            # 3. per-user rate limit and body size (after authentication, A4)
            bucket = "mutate" if request.method in MUTATING else "read"
            limit = settings.rate_limit_mutate_per_minute if bucket == "mutate" else settings.rate_limit_read_per_minute
            wait = limiter.check(bucket, request.state.principal.sub, limit)
            if wait:
                return _json(429, "rate_limited", "too many requests", rid, {"Retry-After": str(wait)})
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return _json(413, "payload_too_large", "request body is too large", rid)
        try:
            return await call_next(request)
        except ApiError as e:  # defensive: handlers normally convert these
            return _json(e.status, e.code, e.message, rid, e.headers)
        except Exception as e:  # noqa: BLE001 - never leak internals; headers still get applied
            access_log.error(json.dumps({"event": "unhandled_error", "request_id": rid,
                                         "error_type": type(e).__name__}))
            return _json(500, "internal_error", KUBE_MESSAGES["other"], rid)
