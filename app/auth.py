import time
import logging
import requests
from typing import Optional, Dict, Any
import jwt
from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidTokenError
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings
from app.models import TokenClaims

logger = logging.getLogger("auth")
security = HTTPBearer(auto_error=False)

# In-memory RSA key pair for local discovery / mock OIDC verification
_PRIVATE_KEY = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048
)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_KEY_ID = "shakudo-demo-key-1"

_JWKS_CACHE: Dict[str, Any] = {}
_DISCOVERY_CACHE: Dict[str, Any] = {}


def get_mock_jwks() -> dict:
    """Export public key as JWKS for OIDC discovery."""
    public_numbers = _PUBLIC_KEY.public_numbers()
    import base64
    def int_to_base64(val: int) -> str:
        b = val.to_bytes((val.bit_length() + 7) // 8, byteorder='big')
        return base64.urlsafe_b64encode(b).decode('utf-8').rstrip('=')

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": _KEY_ID,
                "n": int_to_base64(public_numbers.n),
                "e": int_to_base64(public_numbers.e),
            }
        ]
    }


def mint_mock_token(sub: str, email: str, roles: list[str], expires_in_seconds: int = 3600) -> str:
    """Mint a securely signed RS256 JWT for testing and verification."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "iss": settings.oidc_issuer,
        "aud": settings.oidc_audience,
        "iat": now,
        "exp": now + expires_in_seconds,
        "nbf": now,
        "email": email,
        "roles": roles,
    }
    pem_private = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    token = jwt.encode(
        payload,
        pem_private,
        algorithm="RS256",
        headers={"kid": _KEY_ID}
    )
    return token


def fetch_oidc_discovery() -> dict:
    """Fetch and cache OIDC discovery document."""
    global _DISCOVERY_CACHE
    if _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE
    
    if settings.mock_auth_enabled:
        _DISCOVERY_CACHE = {
            "issuer": settings.oidc_issuer,
            "jwks_uri": f"{settings.oidc_issuer.rstrip('/')}/api/v1/jwks.json",
            "response_types_supported": ["id_token", "token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"]
        }
        return _DISCOVERY_CACHE

    try:
        resp = requests.get(settings.discovery_endpoint, timeout=5)
        resp.raise_for_status()
        _DISCOVERY_CACHE = resp.json()
        return _DISCOVERY_CACHE
    except Exception as e:
        logger.error(f"Failed to fetch OIDC discovery from {settings.discovery_endpoint}: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to reach identity provider discovery endpoint"
        )


def fetch_jwks() -> dict:
    """Fetch and cache JWKS keys from provider."""
    global _JWKS_CACHE
    if _JWKS_CACHE:
        return _JWKS_CACHE

    if settings.mock_auth_enabled:
        _JWKS_CACHE = get_mock_jwks()
        return _JWKS_CACHE

    discovery = fetch_oidc_discovery()
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        raise HTTPException(status_code=502, detail="Discovery document missing jwks_uri")

    try:
        resp = requests.get(jwks_uri, timeout=5)
        resp.raise_for_status()
        _JWKS_CACHE = resp.json()
        return _JWKS_CACHE
    except Exception as e:
        logger.error(f"Failed to fetch JWKS from {jwks_uri}: {e}")
        raise HTTPException(status_code=502, detail="Unable to retrieve signing keys from identity provider")


def validate_jwt_token(token: str) -> TokenClaims:
    """
    Cryptographically validate an RS256 JWT bearer token against OIDC discovery & JWKS.
    Enforces signature, expiration, issuer, and audience without leaking secrets.
    """
    try:
        # 1. Inspect header without verifying signature to extract kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        alg = unverified_header.get("alg")
        
        if alg != "RS256":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unsupported signing algorithm; RS256 required"
            )

        # 2. Retrieve public key matching kid
        jwks = fetch_jwks()
        matching_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid or not kid:
                matching_key = key
                break

        if not matching_key:
            # Fallback to local public key if mock auth
            if settings.mock_auth_enabled:
                public_key = _PUBLIC_KEY
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Signing key not found in identity provider JWKS"
                )
        else:
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(matching_key)

        # 3. Cryptographically verify signature and claims
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iat": True,
                "verify_aud": True,
                "verify_iss": True,
                "require": ["exp", "iss", "aud", "sub"]
            }
        )

        return TokenClaims(
            sub=payload["sub"],
            iss=payload["iss"],
            aud=payload["aud"],
            exp=payload["exp"],
            nbf=payload.get("nbf"),
            email=payload.get("email"),
            roles=payload.get("roles", [])
        )

    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token signature has expired"
        )
    except (InvalidTokenError, PyJWTError) as e:
        logger.warning(f"JWT validation failed: {type(e).__name__}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token credentials"
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> TokenClaims:
    """FastAPI dependency to extract and validate Bearer token from Authorization header."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Bearer header",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return validate_jwt_token(credentials.credentials)
