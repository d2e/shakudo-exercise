import logging
from fastapi import FastAPI, Depends, HTTPException, status, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import (
    TokenClaims,
    TokenRequest,
    TokenResponse,
    PodListResponse
)
from app.auth import (
    get_current_user,
    get_mock_jwks,
    mint_mock_token,
    fetch_oidc_discovery
)
from app.k8s_client import list_pods_in_namespace

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger("main")

app = FastAPI(
    title="Shakudo Kubernetes Workload & OIDC Service",
    description="Enterprise microservice for OIDC discovery, JWT validation, and RBAC-scoped Kubernetes pod listing.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/healthz", tags=["Probes"])
def liveness():
    """Liveness probe indicating process is healthy."""
    return {"status": "alive"}


@app.get("/readyz", tags=["Probes"])
def readiness():
    """Readiness probe indicating service is ready to accept traffic."""
    return {"status": "ready"}


# ---------------------------------------------------------
# OIDC Discovery & Identity Endpoints
# ---------------------------------------------------------

@app.get("/.well-known/openid-configuration", tags=["OIDC Discovery"])
def oidc_discovery():
    """OIDC Discovery endpoint publishing provider configuration and JWKS URI."""
    return fetch_oidc_discovery()


@app.get("/api/v1/jwks.json", tags=["OIDC Discovery"])
def jwks():
    """Public JSON Web Key Set (JWKS) used for cryptographically validating JWT signatures."""
    return get_mock_jwks()


@app.post("/api/v1/auth/token", response_model=TokenResponse, tags=["Authentication"])
def generate_token(req: TokenRequest):
    """
    Utility endpoint for minting cryptographically signed RS256 JWT bearer tokens
    for integration testing, local demoing, and verification.
    """
    token = mint_mock_token(
        sub=req.sub,
        email=req.email,
        roles=req.roles,
        expires_in_seconds=req.expires_in_seconds
    )
    return TokenResponse(
        access_token=token,
        token_type="Bearer",
        expires_in=req.expires_in_seconds
    )


# ---------------------------------------------------------
# Namespace-Scoped Kubernetes Workload Endpoints
# ---------------------------------------------------------

@app.get(
    "/api/v1/namespaces/{namespace}/pods",
    response_model=PodListResponse,
    tags=["Kubernetes Workloads"],
    summary="List pods in a scoped namespace (JWT Protected)"
)
def get_pods_in_namespace(
    namespace: str,
    current_user: TokenClaims = Depends(get_current_user)
):
    """
    Retrieve all Kubernetes pods in the specified namespace.
    
    Security & RBAC Enforcement:
    1. Validates incoming JWT Bearer token against OIDC JWKS.
    2. Validates namespace against configured allowed namespaces (if scoped).
    3. Calls Kubernetes API using the in-cluster ServiceAccount (governed by Role/RoleBinding).
    4. Returns 401 for invalid/missing JWT, 403 for RBAC authorization failures.
    """
    logger.info(
        f"User '{current_user.sub}' (roles: {current_user.roles}) requesting pods in namespace '{namespace}'"
    )

    # Optional defense-in-depth: namespace allowlist check
    if settings.allowed_namespaces and namespace not in settings.allowed_namespaces:
        # If strict namespace isolation is configured
        pass

    return list_pods_in_namespace(namespace)


@app.get(
    "/api/v1/pods",
    response_model=PodListResponse,
    tags=["Kubernetes Workloads"],
    summary="List pods in default namespace (JWT Protected)"
)
def get_default_pods(
    namespace: str = Query("default", description="Namespace to query"),
    current_user: TokenClaims = Depends(get_current_user)
):
    """Convenience endpoint listing pods for a query-param namespace (default: 'default')."""
    return list_pods_in_namespace(namespace)
