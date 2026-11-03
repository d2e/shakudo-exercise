"""Authentication order and mock identity (A1-A8, Q23, Q29, Q34, Q36, Q39)."""
import jwt
import pytest

from api_service.app import create_app

URL = "/api/v1/deployments"
UID = "11111111-1111-4111-8111-111111111111"
BOOT = {"X-Bootstrap-Secret": "test-bootstrap-secret"}


def protected_operations(client):
    from api_service.security import PUBLIC_PATHS
    for path, item in client.app.openapi()["paths"].items():
        if path not in PUBLIC_PATHS:
            for method in item:
                yield method.upper(), path.replace("{id}", UID)


def test_every_protected_route_is_401_without_token(client, gateway):
    ops = list(protected_operations(client))
    assert len(ops) >= 6
    for method, path in ops:
        r = client.request(method, path, json={})
        assert r.status_code == 401, (method, path)
        assert r.headers["www-authenticate"].startswith("Bearer")
    assert gateway.calls == []


def test_invalid_body_without_token_is_401_then_400_with_token(client, auth):
    assert client.post(URL, json={"bad": 1}).status_code == 401
    assert client.post(URL, json={"bad": 1}, headers=auth).status_code == 400


def test_tampered_and_expired_tokens(client, make_client, auth):
    token = auth["Authorization"].split()[1]
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    assert client.get(URL, headers={"Authorization": f"Bearer {tampered}"}).status_code == 401
    r = client.post("/api/v1/auth/token", json={"sub": "t", "expires_in_seconds": 1}, headers=BOOT)
    claims = jwt.decode(r.json()["access_token"], options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 1
    refresh_as_access = client.post("/api/v1/auth/token", json={"sub": "t"}, headers=BOOT).json()["refresh_token"]
    wrong_use = client.get(URL, headers={"Authorization": f"Bearer {refresh_as_access}"})
    assert wrong_use.status_code == 401  # T6: refresh tokens do not work as access tokens


def test_mint_requires_bootstrap_secret(client):
    body = {"sub": "tester"}
    assert client.post("/api/v1/auth/token", json=body).status_code == 401
    assert client.post("/api/v1/auth/token", json=body, headers={"X-Bootstrap-Secret": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/token", json=body, headers=BOOT).status_code == 200


def test_refresh_flow(client):
    pair = client.post("/api/v1/auth/token", json={"sub": "tester"}, headers=BOOT).json()
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r.status_code == 200 and r.json()["access_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": pair["access_token"]}).status_code == 401


def test_auth_endpoint_rate_limit(make_client):
    client = make_client(rate_limit_auth_per_minute=2)
    codes = [client.post("/api/v1/auth/token", json={"sub": "t"}, headers=BOOT).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    r = client.post("/api/v1/auth/token", json={"sub": "t"}, headers=BOOT)
    assert int(r.headers["retry-after"]) >= 1


def test_read_rate_limit_per_user(make_client):
    client = make_client(rate_limit_read_per_minute=2)
    token = client.post("/api/v1/auth/token", json={"sub": "t"}, headers=BOOT).json()["access_token"]
    codes = [client.get(URL, headers={"Authorization": f"Bearer {token}"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_responses_are_no_store(client, auth):
    assert client.get(URL, headers=auth).headers["cache-control"] == "no-store"
    assert client.get("/healthz").headers["cache-control"] == "no-store"


def test_cors_is_off_by_default(client):
    r = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_allows_only_listed_origin(make_client):
    client = make_client(cors_allowed_origins=["https://ui.example"])
    ok = client.get("/healthz", headers={"Origin": "https://ui.example"})
    other = client.get("/healthz", headers={"Origin": "https://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "https://ui.example"
    assert "access-control-allow-origin" not in other.headers


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_hidden_outside_dev(make_client, path):
    client = make_client(environment="staging", mock_auth_enabled=False, allowed_registries=["r.example"])
    assert client.get(path).status_code in (401, 404)


def test_mock_auth_refused_outside_dev(bootstrap_file):
    from api_service.config import validate_settings
    from support import make_settings
    errors = validate_settings(make_settings(environment="prod", bootstrap_secret_file=bootstrap_file))
    assert any(e.startswith("MOCK_AUTH_ENABLED") for e in errors)
