# app/ (deprecated, marked for removal)

This is the **earlier** OIDC discovery and pod-listing service. It is **not part of the assignment deliverable** and is
not deployed. The control-plane deployment API replaces it: [`control-plane/`](../control-plane/spec/req.md). Its ideas
(OIDC discovery, JWT validation, hardened chart) were carried into `control-plane/api-service/`, which has its own code
and does not import anything from here.

It still works (8 unit tests pass, its chart lints), and is kept only until removal.

Marked for removal together, as one stack:

| Path | What |
| :--- | :--- |
| `app/` | the service |
| `tests/`, `pytest.ini` (root) | its unit tests |
| `Dockerfile` (root) | its image (`make build-image`) |
| `charts/app/` | its Helm chart (`deprecated: true` in `Chart.yaml`) |
| `environments/*/values.yaml`, `environments/*/secrets.example.yaml` | its overlays (the API uses `environments/*/api-service.yaml`) |
| `scripts/test_e2e.py` | its live end-to-end test |
| `docs/` (architecture, runbook, review walkthrough), `specs/` | its design docs and the exercise spec it was built for |

When removed: make `api-service` the Makefile's default `CHART`, drop `build-image`, `test-unit` and `test-e2e`, and
update the README directory map. The CI workflow and `scripts/{lint,render,deploy,test}.sh` are shared and stay.
