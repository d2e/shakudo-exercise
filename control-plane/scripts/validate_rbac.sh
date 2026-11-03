#!/usr/bin/env bash
# Verify the control-plane ClusterRole: required actions are allowed, everything else is denied.
# Uses impersonation (kubectl --as) so the real RBAC authorizer evaluates the ServiceAccount's rights.
# Usage: control-plane/scripts/validate_rbac.sh
set -uo pipefail

# Default matches `make deploy-dev CHART=api-service` (release and namespace api-service-dev); override with SA=...
SA="${SA:-system:serviceaccount:api-service-dev:api-service-dev}"
TEST_NS="cp-rbac-$RANDOM"
fail=0

can() { # expect(yes|no) description kubectl-auth-can-i-args...
  local expect="$1" desc="$2"; shift 2
  local got; got=$(kubectl auth can-i "$@" --as="$SA" 2>/dev/null)
  if [[ "$got" == "$expect" ]]; then printf '  PASS  %-52s %s\n' "$desc" "$got"
  else printf '  FAIL  %-52s expected %s, got %s\n' "$desc" "$expect" "$got"; fail=1; fi
}

echo "== Authorization matrix for $SA =="
echo "-- must be allowed --"
can yes "create namespaces"                      create namespaces
can yes "get namespaces"                         get namespaces
can yes "list namespaces"                        list namespaces
can yes "create deployments (any ns)"            create deployments.apps -n any-ns
can yes "get/list deployments"                   list deployments.apps -n any-ns
can yes "list pods"                              list pods -n any-ns
can yes "get pods/log"                           get pods --subresource=log -n any-ns
echo "-- must be denied --"
can no  "delete namespaces"                      delete namespaces
can no  "update namespaces"                      update namespaces
can no  "delete deployments"                     delete deployments.apps -n any-ns
can no  "update deployments"                     update deployments.apps -n any-ns
can no  "patch deployments (scale/restart)"      patch deployments.apps -n any-ns
can no  "create pods directly"                   create pods -n any-ns
can no  "delete pods"                            delete pods -n any-ns
can no  "exec into pods"                         create pods --subresource=exec -n any-ns
can no  "get secrets"                            get secrets -n kube-system
can no  "list secrets"                           list secrets -n any-ns
can no  "create services"                        create services -n any-ns
can no  "create roles"                           create roles.rbac.authorization.k8s.io -n any-ns
can no  "create clusterrolebindings"             create clusterrolebindings.rbac.authorization.k8s.io
can no  "list nodes"                             list nodes
can no  "use wildcard (*) on *"                  '*' '*'

echo
echo "== Live calls as the ServiceAccount (namespace $TEST_NS) =="
step() { # expect-success(0|1) desc cmd...
  local ok="$1" desc="$2"; shift 2
  "$@" >/dev/null 2>&1; local rc=$?
  if { [[ $ok == 0 && $rc -eq 0 ]] || [[ $ok == 1 && $rc -ne 0 ]]; }; then
    printf '  PASS  %s\n' "$desc"
  else printf '  FAIL  %s (rc=%s)\n' "$desc" "$rc"; fail=1; fi
}
K="kubectl --as=$SA"
step 0 "create namespace"                         $K create namespace "$TEST_NS"
step 0 "create deployment (image, replicas, resources, env, labels, command/args)" \
  $K -n "$TEST_NS" apply -f - <<EOF
apiVersion: apps/v1
kind: Deployment
metadata: {name: http-echo, labels: {team: platform}}
spec:
  replicas: 1
  selector: {matchLabels: {app: http-echo}}
  template:
    metadata: {labels: {app: http-echo, team: platform}}
    spec:
      containers:
        - name: http-echo
          image: hashicorp/http-echo:1.0.0
          command: ["/http-echo"]
          args: ["-text=hello", "-listen=:5678"]
          env: [{name: LOG_LEVEL, value: info}]
          ports: [{containerPort: 5678}]
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits: {cpu: 200m, memory: 128Mi}
EOF
step 0 "get deployment"                           $K -n "$TEST_NS" get deployment http-echo
step 0 "list pods"                                $K -n "$TEST_NS" get pods
step 1 "scale deployment is DENIED"               $K -n "$TEST_NS" scale deployment http-echo --replicas=2
step 1 "delete deployment is DENIED"              $K -n "$TEST_NS" delete deployment http-echo
step 1 "read secrets is DENIED"                   $K -n kube-system get secrets
step 1 "delete namespace is DENIED"               $K delete namespace "$TEST_NS"

echo
echo "-- cleanup (as admin) --"
kubectl delete namespace "$TEST_NS" --wait=false >/dev/null 2>&1 && echo "  deleted $TEST_NS"

echo
if [[ $fail -eq 0 ]]; then echo "RBAC validation: ALL CHECKS PASSED"; else echo "RBAC validation: FAILURES"; fi
exit $fail
