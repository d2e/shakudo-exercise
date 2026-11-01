# Contributing Guidelines

Thank you for contributing to the Kubernetes architecture framework.

## Development Workflow

1. **Prerequisites**: Ensure you have `helm` (v3.10+), `kubectl`, and GNU `make` installed.
2. **Branching**: Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
3. **Pre-commit Validation**:
   Always run linting before pushing commits:
   ```bash
   make lint
   ```
4. **Testing Changes**:
   Verify rendered templates across all environments:
   ```bash
   make render-dev
   make render-staging
   make render-prod
   ```
   If testing against a local cluster (`k3s` or `minikube`):
   ```bash
   make deploy-dev
   make test-dev
   ```
5. **Commit Standards**:
   Use [Conventional Commits](https://www.conventionalcommits.org/) format:
   - `feat(chart): ...`
   - `fix(env): ...`
   - `docs(runbook): ...`
   - `refactor(scripts): ...`
