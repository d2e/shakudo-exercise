#!/usr/bin/env python3
"""Validate pod log streaming and deployment lifecycle via the kubernetes Python client.

Throwaway namespace, always cleaned up. Usage:
  .venv/bin/python control-plane/scripts/validate_logs_lifecycle.py
"""
import sys
import threading
import time
import uuid

from kubernetes import client, config, watch
from kubernetes.client.exceptions import ApiException

NS = f"cp-life-{uuid.uuid4().hex[:6]}"
NAME = "ticker"
IMG_V1, IMG_V2, IMG_BAD = "busybox:1.36.1", "busybox:1.37.0", "busybox:does-not-exist"
results: list[bool] = []


def check(name, fn):
    try:
        detail = fn() or ""
        results.append(True)
        print(f"  PASS  {name}  {detail}")
    except Exception as e:  # noqa: BLE001
        results.append(False)
        print(f"  FAIL  {name}  {e!r}")


def deployment(image, deadline=None):
    sec = client.V1SecurityContext(
        run_as_non_root=True, run_as_user=65532, allow_privilege_escalation=False,
        read_only_root_filesystem=True, capabilities=client.V1Capabilities(drop=["ALL"]),
        seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"))
    c = client.V1Container(
        name=NAME, image=image, security_context=sec,
        command=["sh", "-c", "i=0; while true; do echo tick $i; i=$((i+1)); sleep 1; done"],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "10m", "memory": "16Mi"}, limits={"cpu": "50m", "memory": "32Mi"}))
    labels = {"app": NAME}
    return client.V1Deployment(
        metadata=client.V1ObjectMeta(name=NAME, namespace=NS),
        spec=client.V1DeploymentSpec(
            replicas=1, progress_deadline_seconds=deadline,
            selector=client.V1LabelSelector(match_labels=labels),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=labels),
                spec=client.V1PodSpec(containers=[c], termination_grace_period_seconds=2))))


def cond(d, t):
    return next((c for c in (d.status.conditions or []) if c.type == t), None)


def wait(fn, timeout=90, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(1)
    raise AssertionError(f"timeout waiting for {what}")


def main() -> int:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    core, apps = client.CoreV1Api(), client.AppsV1Api()
    print(f"Namespace under test: {NS}\n")
    core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(
        name=NS, labels={"pod-security.kubernetes.io/enforce": "restricted"})))
    try:
        apps.create_namespaced_deployment(NS, deployment(IMG_V1))

        def pod_name():
            items = core.list_namespaced_pod(NS, label_selector=f"app={NAME}").items
            ready = [p for p in items if p.status.phase == "Running" and not p.metadata.deletion_timestamp]
            return ready[0].metadata.name if ready else None

        print("== Log streaming ==")
        pod = wait(pod_name, what="running pod")

        check("logs: one-shot read with tail_lines", lambda: (
            f"{len(core.read_namespaced_pod_log(pod, NS, tail_lines=2).splitlines())} lines"))
        check("logs: timestamps + since_seconds", lambda: (
            core.read_namespaced_pod_log(pod, NS, timestamps=True, since_seconds=60).splitlines()[0][:30]))

        def stream_lines():
            """Follow via Watch wrapper: lines must arrive incrementally, not in one burst."""
            w, got = watch.Watch(), []
            for line in w.stream(core.read_namespaced_pod_log, name=pod, namespace=NS,
                                 follow=True, tail_lines=0, _request_timeout=30):
                got.append((time.time(), line))
                if len(got) >= 4:
                    w.stop()
            span = got[-1][0] - got[0][0]
            assert len(got) >= 4 and span >= 2, f"burst, not stream: {len(got)} lines in {span:.1f}s"
            return f"{len(got)} new lines over {span:.1f}s, last={got[-1][1]!r}"
        check("logs: follow=True streams new lines incrementally (Watch)", stream_lines)

        def raw_stream():
            resp = core.read_namespaced_pod_log(pod, NS, follow=True, tail_lines=0, _preload_content=False)
            first = next(resp.stream(decode_content=True))
            resp.release_conn()
            return f"first chunk {first!r}"
        check("logs: raw urllib3 stream + release_conn", raw_stream)

        def capped_stream_in_thread():
            """What the API must do: stream in a worker with a hard duration cap, then close."""
            lines, stop = [], threading.Event()

            def run():
                resp = core.read_namespaced_pod_log(pod, NS, follow=True, tail_lines=0, _preload_content=False)
                try:
                    for chunk in resp.stream():
                        lines.append(chunk)
                        if stop.is_set():
                            break
                finally:
                    resp.release_conn()
            t = threading.Thread(target=run, daemon=True)
            t.start()
            time.sleep(3)
            stop.set()
            t.join(timeout=5)
            assert not t.is_alive(), "stream thread did not stop after cap"
            assert lines, "no data"
            return f"stopped cleanly, {len(lines)} chunks"
        check("logs: capped stream stops and frees thread", capped_stream_in_thread)

        def deleted_pod_logs():
            core.delete_namespaced_pod(pod, NS, grace_period_seconds=0)
            try:
                core.read_namespaced_pod_log(pod, NS)
            except ApiException as e:
                assert e.status == 404, e.status
                return "404 after pod deleted"
            raise AssertionError("expected 404")
        check("logs: deleted pod -> 404", deleted_pod_logs)

        print("\n== Deployment lifecycle ==")
        d = wait(lambda: (lambda x: x if (x.status.available_replicas or 0) == 1 else None)(
            apps.read_namespaced_deployment(NAME, NS)), what="available")
        check("status: Available + Progressing conditions after create", lambda: (
            f"Available={cond(d, 'Available').status}, Progressing={cond(d, 'Progressing').reason}"
            if cond(d, "Available").status == "True" else _fail("not available")))
        check("status: observed_generation == generation", lambda: (
            f"gen={d.metadata.generation}" if d.status.observed_generation == d.metadata.generation
            else _fail("stale")))
        check("status: replicas/updated/ready/available counts", lambda: (
            f"{d.status.replicas}/{d.status.updated_replicas}/{d.status.ready_replicas}/{d.status.available_replicas}"))

        def revision(n):
            rs = apps.list_namespaced_replica_set(NS, label_selector=f"app={NAME}").items
            revs = {int(r.metadata.annotations["deployment.kubernetes.io/revision"]): r for r in rs}
            return revs.get(n)
        check("history: revision 1 ReplicaSet exists", lambda: revision(1).metadata.name)

        def watch_rollout():
            """Watch deployment events while rolling to v2; ends when new RS is fully available."""
            apps.patch_namespaced_deployment(NAME, NS, {"spec": {"template": {"spec": {"containers": [
                {"name": NAME, "image": IMG_V2}]}}}})
            w, seen = watch.Watch(), []
            for ev in w.stream(apps.list_namespaced_deployment, NS,
                               field_selector=f"metadata.name={NAME}", timeout_seconds=90):
                s = ev["object"].status
                state = (s.replicas, s.updated_replicas, s.available_replicas)
                if not seen or seen[-1] != state:
                    seen.append(state)
                if (ev["object"].status.observed_generation == ev["object"].metadata.generation
                        and s.updated_replicas == 1 and s.replicas == 1 and s.available_replicas == 1):
                    w.stop()
            assert seen, "no watch events"
            return f"{len(seen)} status transitions {seen}"
        check("rollout: watch stream shows v1 -> v2 transitions", watch_rollout)
        check("history: revision 2 ReplicaSet exists, image v2", lambda: (
            revision(2).spec.template.spec.containers[0].image
            if revision(2).spec.template.spec.containers[0].image == IMG_V2 else _fail("wrong image")))

        def events():
            ev = core.list_namespaced_event(NS, field_selector=f"involvedObject.name={NAME}").items
            reasons = sorted({e.reason for e in ev})
            assert "ScalingReplicaSet" in reasons, reasons
            return f"reasons={reasons}"
        check("events: deployment events (ScalingReplicaSet)", events)

        def rollback():
            old = revision(1).spec.template
            apps.patch_namespaced_deployment(NAME, NS, {"spec": {"template": {"spec": {"containers": [
                {"name": NAME, "image": old.spec.containers[0].image}]}}}})
            wait(lambda: (lambda x: x.status.updated_replicas == 1 and x.status.available_replicas == 1
                          and x.status.observed_generation == x.metadata.generation)(
                apps.read_namespaced_deployment(NAME, NS)), what="rollback")
            img = apps.read_namespaced_deployment(NAME, NS).spec.template.spec.containers[0].image
            assert img == IMG_V1
            return f"back on {img} (no apps/v1 rollback API: re-apply old template)"
        check("rollback: restore previous revision's template", rollback)

        def failed_rollout():
            apps.patch_namespaced_deployment(NAME, NS, {"spec": {
                "progressDeadlineSeconds": 15,
                "template": {"spec": {"containers": [{"name": NAME, "image": IMG_BAD}]}}}})

            def stuck():
                x = apps.read_namespaced_deployment(NAME, NS)
                p = cond(x, "Progressing")
                return x if p and p.reason == "ProgressDeadlineExceeded" else None
            x = wait(stuck, timeout=90, what="ProgressDeadlineExceeded")
            pods = core.list_namespaced_pod(NS, label_selector=f"app={NAME}").items
            waiting = {cs.state.waiting.reason for p in pods for cs in (p.status.container_statuses or [])
                       if cs.state and cs.state.waiting}
            assert waiting & {"ImagePullBackOff", "ErrImagePull"}, waiting
            old_ok = (x.status.available_replicas or 0) == 1
            return (f"Progressing=False/{cond(x, 'Progressing').reason}; pod waiting={sorted(waiting)}; "
                    f"old pod still serving={old_ok}")
        check("failure: bad image detected (ProgressDeadlineExceeded + ImagePullBackOff)", failed_rollout)

        def fail_events():
            ev = core.list_namespaced_event(NS).items
            reasons = {e.reason for e in ev}
            assert reasons & {"Failed", "BackOff"}, reasons
            return f"pod events include {sorted(reasons & {'Failed', 'BackOff', 'Pulling'})}"
        check("failure: pod events explain the failure", fail_events)

        def delete_lifecycle():
            apps.delete_namespaced_deployment(NAME, NS, propagation_policy="Foreground")
            wait(lambda: not core.list_namespaced_pod(NS, label_selector=f"app={NAME}").items, what="pods gone")
            try:
                apps.read_namespaced_deployment(NAME, NS)
            except ApiException as e:
                return "deployment + pods gone, get -> 404" if e.status == 404 else _fail(str(e.status))
            raise AssertionError("still exists")
        check("delete: foreground deletion removes pods, then 404", delete_lifecycle)
    finally:
        print(f"\nCleaning up namespace {NS} ...")
        try:
            core.delete_namespace(NS)
        except ApiException as e:
            print(f"cleanup failed: {e.status} {e.reason}")

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


def _fail(msg):
    raise AssertionError(msg)


if __name__ == "__main__":
    sys.exit(main())
