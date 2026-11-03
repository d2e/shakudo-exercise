"""Gateway conversion and error classification, using real kubernetes model objects (no cluster)."""
from datetime import datetime, timezone

import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from api_service.kube import KubernetesAdapter, classify, to_deployment_snap, to_pod_snap
from api_service.ports import KubeFailure
from support import make_settings


def k8s_deployment():
    meta = client.V1ObjectMeta(name="web", namespace="demo", uid="u-1", generation=2,
                               creation_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc), resource_version="9")
    container = client.V1Container(name="web", image="nginx:1.27.0")
    spec = client.V1DeploymentSpec(
        replicas=3, selector=client.V1LabelSelector(match_labels={"a": "b"}),
        template=client.V1PodTemplateSpec(spec=client.V1PodSpec(containers=[container])))
    status = client.V1DeploymentStatus(
        observed_generation=2, replicas=3, ready_replicas=1, available_replicas=1, updated_replicas=3,
        unavailable_replicas=2, conditions=[client.V1DeploymentCondition(
            type="Available", status="False", reason="MinimumReplicasUnavailable",
            last_update_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
            last_transition_time=datetime(2026, 1, 2, tzinfo=timezone.utc))])
    return client.V1Deployment(metadata=meta, spec=spec, status=status)


def test_deployment_conversion():
    d = to_deployment_snap(k8s_deployment())
    assert (d.uid, d.image, d.desired, d.ready, d.unavailable) == ("u-1", "nginx:1.27.0", 3, 1, 2)
    assert d.created_at == "2026-01-01T00:00:00Z" and d.updated_at == "2026-01-02T00:00:00Z"
    assert d.selector == {"a": "b"}


def test_deployment_without_status_is_zeroed():
    obj = k8s_deployment()
    obj.status = None
    d = to_deployment_snap(obj)
    assert d.ready == 0 and d.observed_generation is None and d.conditions == []


def pod_with(state, restarts=0):
    return client.V1Pod(
        metadata=client.V1ObjectMeta(name="p"), spec=client.V1PodSpec(node_name="n1", containers=[]),
        status=client.V1PodStatus(phase="Pending", container_statuses=[client.V1ContainerStatus(
            name="c", image="i", image_id="", ready=False, restart_count=restarts, state=state)]))


def test_pod_container_states():
    waiting = to_pod_snap(pod_with(client.V1ContainerState(
        waiting=client.V1ContainerStateWaiting(reason="ImagePullBackOff", message="m")), restarts=2))
    assert waiting.containers[0].state == "waiting" and waiting.containers[0].reason == "ImagePullBackOff"
    assert waiting.restart_count == 2 and waiting.node == "n1"
    term = to_pod_snap(pod_with(client.V1ContainerState(terminated=client.V1ContainerStateTerminated(
        exit_code=1, reason="Error"))))
    assert term.containers[0].state == "terminated" and term.containers[0].reason == "Error"
    run = to_pod_snap(pod_with(client.V1ContainerState(running=client.V1ContainerStateRunning())))
    assert run.containers[0].state == "running"


def test_error_classification_hides_details():
    assert classify(ApiException(status=404)).kind == "not_found"
    assert classify(ApiException(status=409)).kind == "conflict"
    assert classify(ApiException(status=403)).kind == "forbidden"
    assert classify(ApiException(status=500, reason="etcd at 10.0.0.1 down")).kind == "other"
    assert classify(urllib3.exceptions.MaxRetryError(None, "https://10.0.0.1:6443")).kind == "unreachable"
    assert classify(ConnectionRefusedError()).kind == "unreachable"
    assert str(classify(ApiException(status=500, reason="secret detail"))) == "other"


class RaisingApi:
    def __init__(self, exc):
        self.exc = exc

    def __getattr__(self, name):
        def call(*a, **kw):
            raise self.exc
        return call


def test_adapter_wraps_raw_errors():
    gw = KubernetesAdapter(make_settings(), api_client=client.ApiClient())
    gw._core = RaisingApi(urllib3.exceptions.HTTPError("https://10.0.0.1 refused"))
    try:
        gw.ping()
    except KubeFailure as e:
        assert e.kind == "unreachable" and "10.0.0.1" not in str(e)
    else:
        raise AssertionError("expected KubeFailure")


def test_missing_namespace_reads_as_none():
    gw = KubernetesAdapter(make_settings(), api_client=client.ApiClient())
    gw._core = RaisingApi(ApiException(status=404))
    assert gw.namespace_labels("x") is None
    assert gw.secret_labels("x", "s") is None
