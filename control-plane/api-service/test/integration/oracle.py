"""Independent verifier: reads the real Kubernetes API with its own client (validation gate).

It deliberately shares no code with api_service.kube.KubernetesAdapter, so a bug in the adapter cannot
hide itself. Every API action in the gate tests is confirmed here against the cluster, and every
rejected request is confirmed to have left nothing behind.
"""
from typing import Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


class Oracle:
    def __init__(self):
        api = config.new_client_from_config()  # your kubeconfig, never the API under test
        self.apps, self.core = client.AppsV1Api(api), client.CoreV1Api(api)

    # ---- raw reads (None when absent) ----
    def deployment(self, ns: str, name: str):
        return self._get(self.apps.read_namespaced_deployment, name, ns)

    def namespace(self, ns: str):
        return self._get(self.core.read_namespace, ns)

    @staticmethod
    def _get(fn, *args):
        try:
            return fn(*args)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def deployments_in(self, ns: str) -> list:
        return self._list(self.apps.list_namespaced_deployment, ns)

    def replicasets_in(self, ns: str) -> list:
        return self._list(self.apps.list_namespaced_replica_set, ns)

    def pods_in(self, ns: str) -> list:
        return self._list(self.core.list_namespaced_pod, ns)

    @staticmethod
    def _list(fn, ns):
        try:
            return fn(ns).items
        except ApiException as e:
            if e.status == 404:
                return []
            raise

    def namespace_gone_or_terminating(self, ns: str) -> bool:
        n = self.namespace(ns)
        return n is None or n.status.phase == "Terminating"

    def delete_namespace(self, ns: str) -> None:
        try:
            self.core.delete_namespace(ns)
        except ApiException as e:
            if e.status != 404:
                raise

    def ready_pods(self, ns: str, name: str) -> list:
        """Running, Ready, not terminating pods of the deployment (selected by its own label)."""
        out = []
        for p in self.pods_in(ns):
            if (p.metadata.labels or {}).get("app.kubernetes.io/name") != name or p.metadata.deletion_timestamp:
                continue
            if any(c.type == "Ready" and c.status == "True" for c in (p.status.conditions or [])):
                out.append(p)
        return out

    # ---- assertions: API claim versus cluster truth ----
    def assert_created_as_requested(self, req: dict, response: dict) -> None:
        """The Deployment really exists with what was asked for, hardened as promised (D2, D8)."""
        d = self.deployment(req["namespace"], req["name"])
        assert d is not None, f"API said created but the cluster has no Deployment {req['namespace']}/{req['name']}"
        assert d.metadata.uid == response["uid"], f"uid mismatch: api={response['uid']} cluster={d.metadata.uid}"
        spec, pod = d.spec, d.spec.template.spec
        c = pod.containers[0]
        assert c.image == req["image"] and spec.replicas == req.get("replicas", 1)
        r = req["resources"]
        assert (c.resources.requests["cpu"], c.resources.requests["memory"]) == (r["requests"]["cpu"], r["requests"]["memory"])
        assert (c.resources.limits["cpu"], c.resources.limits["memory"]) == (r["limits"]["cpu"], r["limits"]["memory"])
        assert (d.metadata.labels or {}).get("app.kubernetes.io/managed-by") == "control-plane"
        for k, v in req.get("labels", {}).items():
            assert d.metadata.labels.get(k) == v and spec.template.metadata.labels.get(k) == v, f"label {k} missing"
        assert list(c.command or []) == req.get("command", []) and list(c.args or []) == req.get("args", [])
        if "port" in req:
            assert [p.container_port for p in c.ports] == [req["port"]]
        sc = c.security_context
        assert sc.run_as_non_root is True and sc.allow_privilege_escalation is False
        assert sc.read_only_root_filesystem is True and sc.capabilities.drop == ["ALL"]
        assert sc.seccomp_profile.type == "RuntimeDefault"
        assert pod.termination_grace_period_seconds == 30 and pod.automount_service_account_token is False
        assert d.spec.progress_deadline_seconds is not None
        assert any(v.name == "tmp" and v.empty_dir and v.empty_dir.medium == "Memory" for v in pod.volumes)
        assert c.readiness_probe is not None and c.liveness_probe is not None or "port" not in req

    def assert_namespace_provisioned(self, ns: str) -> None:
        n = self.namespace(ns)
        assert n is not None, f"namespace {ns} does not exist in the cluster"
        labels = n.metadata.labels or {}
        assert labels.get("app.kubernetes.io/managed-by") == "control-plane"
        assert labels.get("pod-security.kubernetes.io/enforce") == "restricted"
        assert self.core.list_namespaced_resource_quota(ns).items, "no ResourceQuota"
        assert self.core.list_namespaced_limit_range(ns).items, "no LimitRange"

    def assert_status_agrees(self, ns: str, name: str, detail: dict) -> None:
        """At a settled state the API's numbers equal the cluster's own status."""
        d = self.deployment(ns, name)
        assert d is not None, f"API returned {name} but the cluster has no such Deployment"
        s, rep = d.status, detail["replicas"]
        assert rep["desired"] == d.spec.replicas, f"desired: api={rep['desired']} cluster={d.spec.replicas}"
        assert rep["ready"] == (s.ready_replicas or 0), f"ready: api={rep['ready']} cluster={s.ready_replicas}"
        assert rep["available"] == (s.available_replicas or 0)
        assert detail["image"] == d.spec.template.spec.containers[0].image
        api_conditions = {(c["type"], c["status"]) for c in detail["conditions"]}
        assert api_conditions == {(c.type, c.status) for c in s.conditions or []}
        live = {p.metadata.name for p in self.ready_pods(ns, name)}
        assert {p["name"] for p in detail["pods"] if p["phase"] == "Running"} >= live, "API pod table misses live pods"

    def assert_nothing_in(self, ns: str, why: str) -> None:
        """A refused request must leave no namespace, no Deployment, nothing (validation gate)."""
        assert self.namespace(ns) is None, f"{why}: namespace {ns} was created in the cluster"
