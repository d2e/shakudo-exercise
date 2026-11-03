"""VALIDATION GATE, happy path: every API action is confirmed against the real Kubernetes API (Q7-Q13, D9).

After each call the oracle reads the cluster directly. If the API says it did something and the
cluster disagrees, the test fails with both sides in the message.
"""
from itsupport import body, wait_for

NEW_IMAGE = "hashicorp/http-echo:0.2.3"


def live(oracle, ns, name="echo"):
    return {p.metadata.name for p in oracle.ready_pods(ns, name)}


def test_every_action_is_confirmed_by_the_cluster(api, oracle, namespace):
    ns, req = namespace, body(namespace, replicas=2, labels={"team": "platform"}, env=[{"name": "LOG", "value": "info"}])

    # create: the cluster really has the Deployment, built as asked and hardened as promised
    oracle.assert_nothing_in(ns, "before any request")
    r = api.call("POST", "", json=req)
    assert r.status_code == 201, r.text
    created = r.json()
    uid = created["uid"]
    assert created["namespaceCreated"] is True
    oracle.assert_created_as_requested(req, created)
    oracle.assert_namespace_provisioned(ns)

    # the cluster brings 2 pods to Ready, and only then does the API agree on health and counts
    wait_for(lambda: live(oracle, ns), lambda p: len(p) == 2, "2 Ready pods in the cluster")
    detail = api.wait_health(uid, "HEALTHY")
    oracle.assert_status_agrees(ns, "echo", detail)

    # list: same set as the cluster holds
    listed = {d["uid"] for d in api.call("GET", "", params={"namespace": ns}).json()["items"]}
    assert listed == {d.metadata.uid for d in oracle.deployments_in(ns)} == {uid}

    # scale up, down, to zero, and back: spec changes immediately, pods converge to it
    for target in (3, 1, 0, 1):
        assert api.call("PATCH", f"/{uid}", json={"replicas": target}).status_code == 200
        assert oracle.deployment(ns, "echo").spec.replicas == target, f"cluster spec.replicas != {target}"
        wait_for(lambda: live(oracle, ns), lambda p, t=target: len(p) == t, f"{target} Ready pods in the cluster")
        detail = api.wait_health(uid, "HEALTHY")
        assert detail["replicas"]["desired"] == target
        oracle.assert_status_agrees(ns, "echo", detail)

    # image update: the template changes, the rollout finishes, the new image is what runs
    assert api.call("PATCH", f"/{uid}", json={"image": NEW_IMAGE}).status_code == 200
    assert oracle.deployment(ns, "echo").spec.template.spec.containers[0].image == NEW_IMAGE
    wait_for(lambda: oracle.ready_pods(ns, "echo"),
             lambda ps: len(ps) == 1 and ps[0].spec.containers[0].image == NEW_IMAGE, "pod running the new image")
    oracle.assert_status_agrees(ns, "echo", api.wait_health(uid, "HEALTHY"))

    # restart: annotation on the template, and the pod is actually replaced
    before = live(oracle, ns)
    assert api.call("POST", f"/{uid}/restart").status_code == 202
    ann = oracle.deployment(ns, "echo").spec.template.metadata.annotations or {}
    assert "kubectl.kubernetes.io/restartedAt" in ann, "restart did not patch the template annotation"
    wait_for(lambda: live(oracle, ns), lambda p: len(p) == 1 and not (p & before), "pod replaced after restart")
    oracle.assert_status_agrees(ns, "echo", api.wait_health(uid, "HEALTHY"))

    # delete: gone from the cluster, cascade complete, empty API-created namespace removed (D9)
    r = api.call("DELETE", f"/{uid}")
    assert r.status_code == 202 and r.json()["uid"] == uid
    wait_for(lambda: oracle.deployment(ns, "echo"), lambda d: d is None, "Deployment gone from the cluster")
    wait_for(lambda: (oracle.replicasets_in(ns), oracle.pods_in(ns)), lambda t: t == ([], []),
             "no ReplicaSets or Pods left (cascade)")
    wait_for(lambda: oracle.namespace_gone_or_terminating(ns), bool, "empty namespace removed (D9)")
    assert api.call("GET", f"/{uid}").status_code == 404
