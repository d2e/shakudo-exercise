"""Auth controller: mock identity endpoints (D10, D20) and the OIDC documents. Mounted only when mock auth is on."""
import hmac
from pathlib import Path

from fastapi import APIRouter, Header, Request

from ..auth.tokens import AuthError, TokenService, public_jwks
from ..errors import ApiError
from ..models import Error, RefreshRequest, TokenRequest, TokenResponse

ERRORS = {401: {"model": Error}, 400: {"model": Error}, 429: {"model": Error}}


def build_auth_controller(settings, tokens: TokenService, private_key, kid: str) -> APIRouter:
    router = APIRouter()
    secret = Path(settings.bootstrap_secret_file).read_bytes().strip() if settings.bootstrap_secret_file else b""

    @router.post("/api/v1/auth/token", response_model=TokenResponse, tags=["Auth"], operation_id="mintToken",
                 summary="Mint a token pair (mock identity provider, dev only)", responses=ERRORS)
    def mint_token(body: TokenRequest, x_bootstrap_secret: str = Header("", description="Bootstrap secret (R1)")):
        """Requires the bootstrap secret (compared in constant time). Access tokens last at most 5 minutes."""
        if not secret or not hmac.compare_digest(x_bootstrap_secret.encode(), secret):
            raise ApiError(401, "invalid_bootstrap_secret", "invalid bootstrap secret",
                           headers={"WWW-Authenticate": "Bearer"})
        return tokens.mint_pair(body.sub, body.email, body.roles, body.expires_in_seconds)

    @router.post("/api/v1/auth/refresh", response_model=TokenResponse, tags=["Auth"], operation_id="refreshToken",
                 summary="Exchange a refresh token for a new token pair", responses=ERRORS)
    def refresh_token(body: RefreshRequest):
        try:
            who = tokens.verify(body.refresh_token, "refresh")
        except AuthError as e:
            raise ApiError(401, "invalid_token", e.message if e.expired else "invalid token",
                           headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}) from None
        return tokens.mint_pair(who.sub, who.email, who.roles)

    @router.get("/.well-known/openid-configuration", tags=["Auth"], operation_id="oidcDiscovery",
                summary="OIDC discovery document (mock)")
    def discovery(request: Request):
        issuer = settings.oidc_issuer.rstrip("/")
        return {"issuer": settings.oidc_issuer, "jwks_uri": f"{issuer}/.well-known/jwks.json",
                "response_types_supported": ["token"], "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"]}

    @router.get("/.well-known/jwks.json", tags=["Auth"], operation_id="jwks", summary="Signing keys (mock)")
    def jwks():
        return public_jwks(private_key, kid)

    return router
