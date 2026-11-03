"""Mock identity provider and token validation (D10, D17, T1-T6, N9).

Access and refresh tokens are signed RS256 JWTs. A `token_use` claim keeps them from being
swapped (T6). Validation always goes through the key cache and accepts RS256 only (N9).
"""
import base64
import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import ExpiredSignatureError, PyJWTError

from ..config import Settings
from .jwks_cache import JwksCache, KeyUnavailable

logger = logging.getLogger("api_service.auth")


class AuthError(Exception):
    def __init__(self, message: str = "invalid authentication token", expired: bool = False):
        super().__init__(message)
        self.message, self.expired = message, expired


@dataclass
class Principal:
    sub: str
    iss: str
    email: Optional[str] = None
    roles: list[str] = field(default_factory=list)


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).decode().rstrip("=")


def load_signing_key(settings: Settings):
    """Load the signing key from its Secret file (K1). Only dev may fall back to an ephemeral key."""
    if settings.signing_key_file:
        key = serialization.load_pem_private_key(Path(settings.signing_key_file).read_bytes(), password=None)
    else:
        logger.warning("SIGNING_KEY_FILE not set: using an ephemeral key (dev only; tokens die on restart)")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key().public_numbers()
    kid = hashlib.sha256(f"{pub.n}:{pub.e}".encode()).hexdigest()[:16]
    return key, kid


def public_jwks(key, kid: str) -> dict:
    pub = key.public_key().public_numbers()
    return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid, "n": _b64(pub.n), "e": _b64(pub.e)}]}


class RemoteJwksFetcher:
    """Real provider: discovery document, then JWKS. HTTPS expected, no redirects, short timeout."""

    def __init__(self, settings: Settings):
        self._url = settings.discovery_endpoint

    def __call__(self) -> dict:
        disc = requests.get(self._url, timeout=5, allow_redirects=False)
        disc.raise_for_status()
        jwks_uri = disc.json()["jwks_uri"]
        resp = requests.get(jwks_uri, timeout=5, allow_redirects=False)
        resp.raise_for_status()
        return resp.json()


class TokenService:
    def __init__(self, settings: Settings, cache: JwksCache, private_key=None, kid: Optional[str] = None,
                 clock: Callable[[], float] = time.time):
        self.settings, self.cache, self._private, self.kid, self._clock = settings, cache, private_key, kid, clock

    @property
    def can_mint(self) -> bool:
        return self._private is not None

    def _encode(self, sub: str, email, roles, ttl: int, use: str) -> str:
        now = int(self._clock())
        payload = {
            "sub": sub, "iss": self.settings.oidc_issuer, "aud": self.settings.oidc_audience,
            "iat": now, "nbf": now, "exp": now + ttl, "token_use": use,
            "email": email, "roles": roles,
        }
        pem = self._private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        return jwt.encode(payload, pem, algorithm="RS256", headers={"kid": self.kid})

    def mint_pair(self, sub: str, email: Optional[str], roles: list[str],
                  requested_ttl: Optional[int] = None) -> dict:
        """New access and refresh tokens. Access lifetime is capped at the configured maximum (T1)."""
        ttl = min(requested_ttl or self.settings.access_token_ttl_seconds, self.settings.access_token_ttl_seconds)
        refresh_ttl = self.settings.refresh_token_ttl_seconds
        return {
            "access_token": self._encode(sub, email, roles, ttl, "access"),
            "refresh_token": self._encode(sub, email, roles, refresh_ttl, "refresh"),
            "token_type": "Bearer", "expires_in": ttl, "refresh_expires_in": refresh_ttl,
        }

    def verify(self, token: str, use: str = "access") -> Principal:
        """Validate against the cached signing key (N9). Raises AuthError for any problem."""
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise AuthError("unsupported signing algorithm")
            key = self.cache.get_key(header.get("kid"))
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                audience=self.settings.oidc_audience, issuer=self.settings.oidc_issuer,
                leeway=self.settings.clock_skew_seconds,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except ExpiredSignatureError:
            raise AuthError("token expired", expired=True) from None
        except KeyUnavailable:
            raise AuthError() from None
        except PyJWTError:
            raise AuthError() from None
        if claims.get("token_use") != use:
            raise AuthError("wrong token type")
        return Principal(sub=claims["sub"], iss=claims["iss"], email=claims.get("email"),
                         roles=claims.get("roles") or [])
