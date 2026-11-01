import time
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import mint_mock_token

client = TestClient(app)


def test_health_probes():
    """Verify liveness and readiness probe endpoints."""
    resp_live = client.get("/healthz")
    assert resp_live.status_code == 200
    assert resp_live.json() == {"status": "alive"}

    resp_ready = client.get("/readyz")
    assert resp_ready.status_code == 200
    assert resp_ready.json() == {"status": "ready"}


def test_oidc_discovery_and_jwks():
    """Verify OIDC discovery document and JWKS schema compliance."""
    # 1. Discovery document
    resp = client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200
    data = resp.json()
    assert "issuer" in data
    assert "jwks_uri" in data
    assert "RS256" in data["id_token_signing_alg_values_supported"]

    # 2. JWKS endpoint
    resp_jwks = client.get("/api/v1/jwks.json")
    assert resp_jwks.status_code == 200
    jwks_data = resp_jwks.json()
    assert "keys" in jwks_data
    assert len(jwks_data["keys"]) > 0
    key = jwks_data["keys"][0]
    assert key["kty"] == "RSA"
    assert key["alg"] == "RS256"
    assert "n" in key and "e" in key


def test_token_minting_and_validation():
    """Verify token generation endpoint and Bearer authentication."""
    # 1. Generate token
    resp = client.post(
        "/api/v1/auth/token",
        json={
            "sub": "alice-test",
            "email": "alice@shakudo.local",
            "roles": ["platform-admin"],
            "expires_in_seconds": 3600
        }
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    assert len(token) > 20

    # 2. Access protected endpoint with valid token
    headers = {"Authorization": f"Bearer {token}"}
    resp_pods = client.get("/api/v1/namespaces/default/pods", headers=headers)
    # Status should be 200 (if K8s responds) or 403/404, but definitely NOT 401
    assert resp_pods.status_code != 401


def test_unauthenticated_request_rejected():
    """Verify request with no Authorization header returns 401."""
    resp = client.get("/api/v1/namespaces/default/pods")
    assert resp.status_code == 401
    assert "Missing Authorization Bearer header" in resp.json()["detail"]


def test_tampered_token_rejected():
    """Verify request with tampered/invalid signature returns 401."""
    headers = {"Authorization": "Bearer invalid.jwt.token"}
    resp = client.get("/api/v1/namespaces/default/pods", headers=headers)
    assert resp.status_code == 401


def test_expired_token_rejected():
    """Verify expired token returns 401 with appropriate message."""
    # Mint token that expired 10 seconds ago
    expired_token = mint_mock_token(
        sub="expired-user",
        email="expired@shakudo.local",
        roles=["viewer"],
        expires_in_seconds=-10
    )
    headers = {"Authorization": f"Bearer {expired_token}"}
    resp = client.get("/api/v1/namespaces/default/pods", headers=headers)
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()
