"""Application factory: wires settings, identity, the cluster adapter, routes and the OpenAPI contract."""
import logging
from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from . import __version__
from .auth.jwks_cache import JwksCache
from .controllers import (build_auth_controller, build_deployments_controller, build_logs_controller,
                          build_probes_controller)
from .auth.tokens import RemoteJwksFetcher, TokenService, load_signing_key, public_jwks
from .config import Settings
from .errors import install_error_handlers
from .kube import KubernetesAdapter
from .log_service import LogService, StreamRegistry
from .ports import ClusterPort
from .ratelimit import RateLimiter
from .security import PUBLIC_PATHS, install_security
from .service import DeploymentService

ERROR_STATUSES = ("401", "429", "500")  # every protected operation can return these


@dataclass
class RuntimeState:
    shutting_down: bool = False


def _build_tokens(settings: Settings):
    cooldown, ttl, stale = (settings.jwks_refetch_cooldown_seconds, settings.jwks_cache_ttl_seconds,
                            settings.jwks_max_stale_seconds)
    if settings.mock_auth_enabled:
        key, kid = load_signing_key(settings)
        cache = JwksCache(lambda: public_jwks(key, kid), ttl, cooldown, stale)  # local keys: no network
        return TokenService(settings, cache, key, kid), key, kid
    cache = JwksCache(RemoteJwksFetcher(settings), ttl, cooldown, stale)
    ticket_key, ticket_kid = load_signing_key(settings)  # the IdP's key is not ours, so tickets need their own (LG4)
    return TokenService(settings, cache, ticket_key=ticket_key, ticket_kid=ticket_kid), None, None


def _install_openapi(app: FastAPI) -> None:
    """F10-F14: bearerAuth on protected operations, documented 4xx/5xx errors, no default 422."""
    def build() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        schema["components"].setdefault("securitySchemes", {})["bearerAuth"] = {
            "type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        for path, item in schema["paths"].items():
            for op in item.values():
                op.get("responses", {}).pop("422", None)
                if path not in PUBLIC_PATHS:
                    op["security"] = [{"bearerAuth": []}]
                    for status in ERROR_STATUSES:
                        op["responses"].setdefault(status, {
                            "description": "Error", "content": {"application/json": {
                                "schema": {"$ref": "#/components/schemas/Error"}}}})
        schema["components"]["schemas"].pop("HTTPValidationError", None)
        schema["components"]["schemas"].pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = build  # type: ignore[method-assign]


def create_app(settings: Settings, gateway: Optional[ClusterPort] = None, state: Optional[RuntimeState] = None) -> FastAPI:
    state = state or RuntimeState()
    docs = settings.docs_enabled
    app = FastAPI(
        title="Control Plane API", version=__version__,
        description="Create and manage Kubernetes Deployments. Every `/api/v1` call needs a Bearer JWT.",
        docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None,
    )
    tokens, key, kid = _build_tokens(settings)
    gateway = gateway or KubernetesAdapter(settings)  # the one place the adapter is chosen

    install_error_handlers(app)
    install_security(app, settings, tokens, RateLimiter())
    if settings.cors_allowed_origins:  # off by default (R17); outermost so preflights are answered
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_allowed_origins,
                           allow_methods=["GET", "POST", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"])

    app.include_router(build_probes_controller(gateway, state))
    deployments = DeploymentService(settings, gateway)
    registry = StreamRegistry(settings.max_log_streams_per_user)
    app.include_router(build_deployments_controller(deployments))
    app.include_router(build_logs_controller(LogService(settings, gateway, tokens, deployments, registry),
                                             state, settings, gateway))
    if settings.mock_auth_enabled:
        app.include_router(build_auth_controller(settings, tokens, key, kid))
    app.state.runtime, app.state.tokens, app.state.gateway = state, tokens, gateway
    app.state.log_registry = registry
    _install_openapi(app)
    return app
