# Site Reliability Engineering (SRE) Runbook

This runbook outlines operational procedures, incident response steps, and diagnostic triage sequences for workloads deployed via the Helm management framework.

---

## 1. Routine Operational Procedures

### Deploy / Upgrade
```bash
# Lint before deploying
make lint

# Deploy to target environment
make deploy-dev
# or: make deploy-staging / make deploy-prod
```

### Rollback
If a deployment exhibits degraded latency or downstream failures:
```bash
# View release revision history
helm history app-prod -n app-prod

# Roll back to the previous healthy revision immediately
helm rollback app-prod <previous-revision-number> -n app-prod --wait
```

### Cluster Resource Inspection
```bash
# Inspect all resources in the target environment
make status-dev
# or via kubectl directly:
kubectl -n app-prod get pods,svc,ingress,hpa,pdb -o wide
```

---

## 2. Fast Incident Triage Guide

### A. Pod in `CrashLoopBackOff`
1. Check the previous container exit log:
   ```bash
   kubectl -n <namespace> logs <pod-name> --previous
   ```
2. Inspect pod termination state and exit code:
   ```bash
   kubectl -n <namespace> get pod <pod-name> -o jsonpath='{.status.containerStatuses[0].lastState.terminated}'
   ```
3. Common Causes:
   * **Exit Code 1**: Application boot failure, missing environment variable, syntax error.
   * **Exit Code 137**: OOMKilled (Out of Memory). See section C below.
   * **Exit Code 126/127**: Command or entrypoint binary not found in container image.

---

### B. Pod in `ImagePullBackOff` or `ErrImagePull`
1. Inspect the exact image tag and failure message:
   ```bash
   kubectl -n <namespace> describe pod <pod-name> | grep -A 10 Events
   ```
2. Verify the image exists on the remote registry or locally on k3s:
   ```bash
   # Check k3s internal containerd image cache
   sudo k3s ctr images list | grep <image-name>
   ```
3. For private registries, verify `imagePullSecrets` configuration in `values.yaml`.

---

### C. Pod Terminated with `OOMKilled`
1. Check the pod's memory limit:
   ```bash
   kubectl -n <namespace> get pod <pod-name> -o jsonpath='{.spec.containers[0].resources.limits.memory}'
   ```
2. Review real-time node and pod metrics:
   ```bash
   kubectl top pod -n <namespace>
   kubectl top node
   ```
3. Remediate by adjusting `resources.limits.memory` in `environments/<env>/values.yaml` and redeploying.

---

### D. Pod in `Pending` Status
1. Check scheduler failure events:
   ```bash
   kubectl -n <namespace> describe pod <pod-name> | grep -A 5 Events
   ```
2. Common Causes:
   * **Insufficient CPU/Memory**: Node capacity is exhausted. Lower `resources.requests` or add cluster nodes.
   * **Anti-Affinity Collisions**: `podAntiAffinity` prevents scheduling more pods than there are unique nodes.
   * **Taints and Tolerations**: Pod lacks toleration for a dedicated node taint.

---

### E. Service Returning 502 / 503 Bad Gateway
1. Verify whether the Service has active endpoints:
   ```bash
   kubectl -n <namespace> get endpoints <service-name>
   ```
2. If `ENDPOINTS` is `<none>`, check pod labels matching service selector:
   ```bash
   kubectl -n <namespace> get pods --show-labels
   kubectl -n <namespace> get svc <service-name> -o jsonpath='{.spec.selector}'
   ```
3. Check `readinessProbe` health: If readiness probes fail, Kubernetes strips the pod IP from service endpoints until the probe returns HTTP 200.

---

## 3. Automated Post-Deployment Verification

Always execute the Helm integration test hook after any change:
```bash
make test-dev
# or:
helm test <release-name> -n <namespace> --logs
```
This triggers a lightweight, isolated probe container within the cluster network to verify end-to-end DNS resolution, ingress routing, and HTTP 200 response codes.
