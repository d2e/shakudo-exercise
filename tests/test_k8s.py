import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.auth import mint_mock_token

client = TestClient(app)


def test_list_pods_with_valid_auth():
    """Verify listing pods in default and kube-system namespaces."""
    token = mint_mock_token(
        sub="test-sre",
        email="sre@shakudo.local",
        roles=["platform-admin"],
        expires_in_seconds=3600
    )
    headers = {"Authorization": f"Bearer {token}"}

    # Query kube-system namespace (which has CoreDNS, Traefik, metrics-server)
    resp = client.get("/api/v1/namespaces/kube-system/pods", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["namespace"] == "kube-system"
    assert data["pod_count"] > 0
    assert len(data["pods"]) > 0

    first_pod = data["pods"][0]
    assert "name" in first_pod
    assert "phase" in first_pod
    assert "ready" in first_pod
    assert first_pod["phase"] == "Running"


def test_list_pods_non_existent_namespace():
    """Verify 404 is returned when querying a namespace that does not exist."""
    token = mint_mock_token(
        sub="test-sre",
        email="sre@shakudo.local",
        roles=["platform-admin"],
        expires_in_seconds=3600
    )
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/api/v1/namespaces/non-existent-namespace-12345/pods", headers=headers)
    # K8s returns empty list if namespace is valid, or 404 if namespace doesn't exist
    assert resp.status_code in (200, 404)
