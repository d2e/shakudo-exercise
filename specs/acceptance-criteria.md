# Acceptance Criteria & Verification Matrix

This matrix tracks the validation commands and criteria used to confirm compliance with each requirement before the final review.

| Requirement ID | Description | Verification Command / Target | Status |
| :---: | :--- | :--- | :---: |
| **REQ-01** | OIDC Discovery & JWKS Validation | Service parses `.well-known/openid-configuration` and validates RS256 JWT signature & claims | Verified |
| **REQ-02** | Secure Token Handling | Bearer token extracted safely; HTTP 401 on expired/tampered tokens; zero secrets logged | Verified |
| **REQ-03** | Kubernetes API Integration | Dual-mode K8s client handles in-cluster SA and local kubeconfig gracefully | Verified |
| **REQ-04** | Role & RoleBinding Configuration | Helm `templates/rbac.yaml` renders least-privilege Role and RoleBinding in target namespace | Verified |
| **REQ-05** | Namespace-Scoped Pod Listing | `GET /api/v1/namespaces/{namespace}/pods` returns 200 with structured pod JSON when authorized; 403 when RBAC denied | Verified |
| **REQ-06** | Multi-Environment Helm Lint | `make lint` passes strict validation across dev, staging, prod | Verified |
| **REQ-07** | PSS Restricted Compliance | Pod runs non-root, read-only rootfs, dropped capabilities, no privilege escalation | Verified |
