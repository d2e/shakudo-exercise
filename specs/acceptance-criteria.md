# Acceptance Criteria & Verification Matrix

This matrix tracks the validation commands and criteria used to confirm compliance with each requirement before the final review.

| Requirement ID | Description | Verification Command / Target | Status |
| :---: | :--- | :--- | :---: |
| **REQ-01** | Multi-environment linting | `make lint` passes with 0 errors across dev, staging, prod | Passed |
| **REQ-02** | Workload health on k3s cluster | `make deploy-dev` & `make status-dev` returns 1/1 Running | Passed |
| **REQ-03** | In-cluster integration test | `make test-dev` passes Helm test hook | Passed |
| **REQ-04** | PSS restricted compliance | Non-root, immutable rootfs, drop all capabilities | Passed |
| **REQ-05** | Problem-specific criteria | *(Populated when exercise begins)* | Pending |
