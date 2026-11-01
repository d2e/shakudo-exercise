#!/usr/bin/env python3
import sys
import subprocess
import time
import requests

def run():
    print("=== [1/6] Starting port-forward to service/app-dev (8000:8000) ===")
    pf = subprocess.Popen(
        ["kubectl", "port-forward", "-n", "app-dev", "svc/app-dev", "8000:8000"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)

    try:
        base_url = "http://127.0.0.1:8000"

        # 1. Health check
        print("=== [2/6] Verifying health probe (/healthz) ===")
        r = requests.get(f"{base_url}/healthz", timeout=3)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        print(f"✔ Health status: {r.json()}")

        # 2. OIDC Discovery
        print("=== [3/6] Verifying OIDC Discovery (/.well-known/openid-configuration) ===")
        r = requests.get(f"{base_url}/.well-known/openid-configuration", timeout=3)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        data = r.json()
        assert "jwks_uri" in data
        print(f"✔ OIDC Issuer: {data['issuer']}")
        print(f"✔ JWKS URI: {data['jwks_uri']}")

        # 3. Unauthenticated request rejected
        print("=== [4/6] Verifying unauthenticated request is rejected (401) ===")
        r = requests.get(f"{base_url}/api/v1/namespaces/app-dev/pods", timeout=3)
        assert r.status_code == 401, f"Expected 401, got {r.status_code}"
        print(f"✔ Correctly rejected with 401: {r.json()['detail']}")

        # 4. Token minting & Authenticated pod listing in scoped namespace (app-dev)
        print("=== [5/6] Minting JWT & Listing pods in scoped namespace (app-dev) ===")
        token_resp = requests.post(
            f"{base_url}/api/v1/auth/token",
            json={
                "sub": "sre-engineer",
                "email": "sre@shakudo.local",
                "roles": ["platform-engineer"],
                "expires_in_seconds": 3600
            },
            timeout=3
        )
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        r = requests.get(f"{base_url}/api/v1/namespaces/app-dev/pods", headers=headers, timeout=5)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        pod_data = r.json()
        print(f"✔ Successfully listed pods in '{pod_data['namespace']}' (Count: {pod_data['pod_count']}):")
        for p in pod_data["pods"]:
            print(f"   • Pod: {p['name']} | Phase: {p['phase']} | Ready: {p['ready']} | IP: {p['ip']}")

        # 5. RBAC Namespace-scoping verification (403 in non-permitted namespace)
        print("=== [6/6] Verifying RBAC restriction in unpermitted namespace (kube-system) ===")
        r = requests.get(f"{base_url}/api/v1/namespaces/kube-system/pods", headers=headers, timeout=5)
        assert r.status_code == 403, f"Expected 403 Forbidden, got {r.status_code}: {r.text}"
        print(f"✔ RBAC properly enforced: {r.json()['detail']}")

        print("\n🎉 ALL E2E REQUIREMENTS FULLY VERIFIED ON LIVE CLUSTER!")

    finally:
        pf.terminate()

if __name__ == "__main__":
    run()
