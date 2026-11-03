#!/usr/bin/env python3
"""Validate the Kubernetes API calls the control plane will rely on.

Runs against the current kube context in a throwaway namespace and deletes it
at the end. Usage:  .venv/bin/python control-plane/scripts/validate_kube_api.py
"""
import sys
import time
import uuid

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

NS = f"cp-validate-{uuid.uuid4().hex[:6]}"
NAME = "http-echo"
LABELS = {"team": "platform", "tier": "demo"}
results: list[tuple[str, bool, str]] = []


def check(name, fn):
    try:
        detail = fn() or ""
        results.append((name, True, str(detail)))
        print(f"  PASS  {name}  {detail}")
    except Exception as e:  # noqa: BLE001
        results.append((name, False, repr(e)))
        print(f"  FAIL  {name}  {e!r}")


def ensure_namespace(core, ns):
    """get-then-create; 409 AlreadyExists counts as success (race-safe)."""
    try:
        core.read_namespace(ns)
        return False
    except ApiException as e:
        if e.status != 404:
            raise
    body = client.V1Namespace(metadata=client.V1ObjectMeta(name=ns, labels={
        "pod-security.kubernetes.io/enforce": "restricted",
        "app.kubernetes.io/managed-by": "control-plane",
    }))
    try:
        core.create_namespace(body)
        return True
    except ApiException as e:
        if e.status == 409:
            return False
        raise


def build_deployment(replicas=2, image="hashicorp/http-echo:1.0.0"):
    labels = {**LABELS, "app.kubernetes.io/name": NAME}
    sel = {"app.kubernetes.io/name": NAME}
    sec = client.V1SecurityContext(
        run_as_non_root=True, run_as_user=65532, allow_privilege_escalation=False,
        read_only_root_filesystem=True,
        capabilities=client.V1Capabilities(drop=["ALL"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"))
    probe = client.V1Probe(tcp_socket=client.V1TCPSocketAction(port=5678),
                           initial_delay_seconds=1, period_seconds=3)
    container = client.V1Container(
        name=NAME, image=image,
        command=["/http-echo"], args=["-text=hello", "-listen=:5678"],
        ports=[client.V1ContainerPort(name="http", container_port=5678)],
        env=[client.V1EnvVar(name="LOG_LEVEL", value="info")],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "50m", "memory": "64Mi"},
            limits={"cpu": "200m", "memory": "128Mi"}),
        security_context=sec, readiness_probe=probe, liveness_probe=probe,
        volume_mounts=[client.V1VolumeMount(name="tmp", mount_path="/tmp")])
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name=NAME, namespace=NS, labels=labels),
        spec=client.V1DeploymentSpec(
            replicas=replicas, selector=client.V1LabelSelector(match_labels=sel),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=labels),
                spec=client.V1PodSpec(
                    containers=[container], termination_grace_period_seconds=30,
                    volumes=[client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource(medium="Memory"))]))))


def main() -> int:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    core, apps = client.CoreV1Api(), client.AppsV1Api()
    print(f"Namespace under test: {NS}\n")

    try:
        # --- Namespaces ---
        check("namespace: get missing -> 404", lambda: (
            _expect_status(lambda: core.read_namespace(NS), 404)))
        check("namespace: ensure creates it", lambda: f"created={ensure_namespace(core, NS)}")
        check("namespace: ensure is idempotent", lambda: (
            "created=False" if ensure_namespace(core, NS) is False else _fail("recreated")))
        check("namespace: get returns PSS label", lambda: (
            core.read_namespace(NS).metadata.labels["pod-security.kubernetes.io/enforce"]))

        # --- Deployment: server-side dry run ---
        check("deployment: dry-run validates", lambda: (
            apps.create_namespaced_deployment(NS, build_deployment(), dry_run="All").metadata.name))
        check("deployment: dry-run creates nothing", lambda: (
            "none" if not apps.list_namespaced_deployment(NS).items else _fail("exists")))
        check("deployment: invalid resources rejected (422)", lambda: _expect_status(
            lambda: apps.create_namespaced_deployment(NS, _bad_resources(), dry_run="All"), 422))

        # --- Deployment: create + verify fields ---
        check("deployment: create", lambda: apps.create_namespaced_deployment(NS, build_deployment()).metadata.name)
        check("deployment: duplicate -> 409", lambda: _expect_status(
            lambda: apps.create_namespaced_deployment(NS, build_deployment()), 409))

        def verify_fields():
            d = apps.read_namespaced_deployment(NAME, NS)
            c = d.spec.template.spec.containers[0]
            assert d.spec.replicas == 2
            assert c.image == "hashicorp/http-echo:1.0.0"
            assert c.resources.limits == {"cpu": "200m", "memory": "128Mi"}
            assert c.resources.requests == {"cpu": "50m", "memory": "64Mi"}
            assert c.env[0].name == "LOG_LEVEL" and c.args[0] == "-text=hello"
            assert c.ports[0].container_port == 5678
            for k, v in LABELS.items():
                assert d.metadata.labels[k] == v and d.spec.template.metadata.labels[k] == v
            return "image/replicas/resources/env/labels/command+args/port OK"
        check("deployment: get returns configured fields", verify_fields)

        def rollout(ready):
            deadline = time.time() + 90
            while time.time() < deadline:
                d = apps.read_namespaced_deployment(NAME, NS)
                if (d.status.ready_replicas or 0) == ready and (d.status.updated_replicas or 0) == ready:
                    return f"ready_replicas={ready}"
                time.sleep(2)
            raise AssertionError(f"not ready={ready}; status={d.status.conditions}")
        check("deployment: pods become Ready (image pulls, PSS restricted accepted)", lambda: rollout(2))

        # --- Pods of the deployment + logs ---
        def pods():
            sel = ",".join(f"{k}={v}" for k, v in {"app.kubernetes.io/name": NAME}.items())
            items = core.list_namespaced_pod(NS, label_selector=sel).items
            assert len(items) == 2, len(items)
            return f"{len(items)} pods"
        check("pods: list by label selector", pods)

        def logs():
            pod = core.list_namespaced_pod(NS).items[0].metadata.name
            core.read_namespaced_pod_log(pod, NS, tail_lines=5)
            resp = core.read_namespaced_pod_log(pod, NS, follow=True, _preload_content=False, tail_lines=1)
            resp.release_conn()
            return "tail + follow stream opened"
        check("pods: log read + follow stream", logs)

        # --- Scale / update / restart ---
        check("deployment: scale to 3", lambda: apps.patch_namespaced_deployment_scale(
            NAME, NS, {"spec": {"replicas": 3}}).spec.replicas)
        check("deployment: scale 3 -> ready", lambda: rollout(3))
        check("deployment: update env (strategic patch)", lambda: apps.patch_namespaced_deployment(
            NAME, NS, {"spec": {"template": {"spec": {"containers": [
                {"name": NAME, "env": [{"name": "LOG_LEVEL", "value": "debug"}]}]}}}}).metadata.generation)
        check("deployment: stale resourceVersion update -> 409", lambda: _expect_status(
            lambda: apps.replace_namespaced_deployment(NAME, NS, _stale()), 409))
        check("deployment: rollout restart (annotation patch)", lambda: apps.patch_namespaced_deployment(
            NAME, NS, {"spec": {"template": {"metadata": {"annotations": {
                "kubectl.kubernetes.io/restartedAt": str(time.time())}}}}}).metadata.name)
        check("deployment: restart converges", lambda: rollout(3))

        # --- Delete ---
        def delete_and_verify():
            apps.delete_namespaced_deployment(NAME, NS)
            return _expect_status(lambda: _wait_gone(apps), 404)
        check("deployment: delete then get -> 404", delete_and_verify)
    finally:
        print(f"\nCleaning up namespace {NS} ...")
        try:
            core.delete_namespace(NS)
        except ApiException as e:
            print(f"cleanup failed: {e.status} {e.reason}")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


def _wait_gone(apps):
    for _ in range(15):
        apps.read_namespaced_deployment(NAME, NS)  # raises ApiException(404) once gone
        time.sleep(1)
    raise AssertionError("deployment still exists after 15s")


def _fail(msg):
    raise AssertionError(msg)


def _expect_status(fn, status):
    try:
        fn()
    except ApiException as e:
        if e.status == status:
            return f"got {status}"
        raise AssertionError(f"expected {status}, got {e.status}: {e.reason}")
    raise AssertionError(f"expected {status}, call succeeded")


def _bad_resources():
    d = build_deployment()
    d.spec.template.spec.containers[0].resources = client.V1ResourceRequirements(
        requests={"cpu": "500m", "memory": "64Mi"}, limits={"cpu": "100m", "memory": "128Mi"})
    return d


def _stale():
    d = build_deployment()
    d.metadata.resource_version = "1"
    return d


if __name__ == "__main__":
    sys.exit(main())
