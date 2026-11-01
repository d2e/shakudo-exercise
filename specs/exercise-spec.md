# Technical Assessment Specification

> **Session Details**
> - **Format**: Solo exercise, screen-recorded via Google Meet / Fireflies
> - **Time Limit**: ~3.5 hours (3h build + 30m review)
> - **AI Tooling**: Permitted
> - **Internet Access**: Fully permitted

---

## 1. Problem Statement
Implement a secure, production-grade Kubernetes-integrated API service that performs OIDC discovery and JWT validation, securely handles bearer tokens, and integrates with the Kubernetes API under least-privilege RBAC (Role and RoleBinding) to provide namespace-scoped pod listing.

---

## 2. Core Requirements & Deliverables

- [x] **REQ-1: OIDC Discovery and JWT Validation**:
  - Discover configuration via OIDC discovery endpoint (`.well-known/openid-configuration`).
  - Retrieve and cache public signing keys via JWKS URI.
  - Cryptographically validate JWT signature, issuer (`iss`), audience (`aud`), and expiration (`exp`).

- [x] **REQ-2: Secure Token Handling**:
  - Secure extraction of bearer tokens from HTTP `Authorization: Bearer <token>` headers.
  - Constant-time validation checks; zero token/secret leakage in logs or HTTP error responses.
  - Secure claims extraction (subject, roles, email, validity).

- [x] **REQ-3: Kubernetes API Integration**:
  - Kubernetes client initialization with dual-mode support (in-cluster ServiceAccount token when inside pod, fallback to local `kubeconfig` during local development).
  - Robust error handling for API latency, timeouts, and network interruptions.

- [x] **REQ-4: Role and RoleBinding Configuration**:
  - Declarative Kubernetes RBAC templates (`templates/rbac.yaml`) in Helm.
  - Least-privilege `Role` granting `["get", "list", "watch"]` strictly on `["pods", "pods/status"]`.
  - `RoleBinding` associating the application's `ServiceAccount` to the scoped namespace.

- [x] **REQ-5: Namespace-Scoped Pod Listing**:
  - Authenticated endpoint: `GET /api/v1/namespaces/{namespace}/pods`.
  - Rejects unauthenticated or invalid tokens with HTTP 401.
  - Rejects attempts to access unauthorized namespaces with HTTP 403.
  - Returns structured JSON payload containing pod names, phases, readiness, IP, node, and restarts.

---

## 3. Constraints & Technical Boundaries
- **Runtime Environment**: Kubernetes cluster (`k3s`)
- **Package Management**: Helm v3
- **Security Baseline**: Pod Security Standard (PSS) restricted profile (non-root UID, read-only rootfs)
- **Deployment Strategy**: Zero-downtime atomic rollouts with automated rollback on failure
