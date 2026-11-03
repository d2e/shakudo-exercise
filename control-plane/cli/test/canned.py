"""Canned API responses. Every one is checked against spec/openapi.yaml by FakeServer.reply (CL37)."""
from datetime import datetime, timezone

NOW = 1_800_000_000.0  # the fixed clock of every test
UID = "7f3a9c1e-1b2c-4d5e-8f60-123456789abc"
UID2 = "7f3a9c1e-9999-4d5e-8f60-123456789abd"
UID3 = "aaaa1111-1b2c-4d5e-8f60-123456789abe"


def iso(seconds_ago: float) -> str:
    return datetime.fromtimestamp(NOW - seconds_ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def deployment(uid=UID, name="http-echo", namespace="demo", image="hashicorp/http-echo:1.0.0", desired=2, ready=2,
               health="HEALTHY", age=300, conditions=None) -> dict:
    return {"uid": uid, "name": name, "namespace": namespace, "image": image,
            "replicas": {"desired": desired, "ready": ready, "available": ready, "unavailable": desired - ready},
            "conditions": conditions or [], "createdAt": iso(age), "updatedAt": iso(age), "health": health}


def created(namespace_created=False, resources_defaulted=False, **over) -> dict:
    return {**deployment(**over), "namespaceCreated": namespace_created, "resourcesDefaulted": resources_defaulted}


def container(name="http-echo", state="running", reason=None, message=None, ready=True, restarts=0) -> dict:
    out = {"name": name, "ready": ready, "restartCount": restarts, "state": state}
    if reason:
        out["reason"] = reason
    if message:
        out["message"] = message
    return out


def pod(name="http-echo-abc-1", phase="Running", restarts=0, node="node-1", containers=None) -> dict:
    return {"name": name, "phase": phase, "restartCount": restarts, "node": node,
            "containerStatuses": containers if containers is not None else [container(restarts=restarts)]}


def detail(pods=None, **over) -> dict:
    return {**deployment(**over), "pods": pods if pods is not None else [pod()]}


def failing_detail() -> dict:
    waiting = container(state="waiting", reason="ImagePullBackOff", ready=False,
                        message="Back-off pulling image \"nope:1.0\"")
    return detail(health="FAILING", desired=2, ready=0, pods=[pod("p-1", "Pending", containers=[waiting]), pod("p-2", "Pending", containers=[waiting])],
                  conditions=[{"type": "Progressing", "status": "False", "reason": "ProgressDeadlineExceeded",
                               "message": "ReplicaSet has timed out progressing."}])


def deleted(uid=UID, name="http-echo", namespace="demo") -> dict:
    return {"uid": uid, "name": name, "namespace": namespace, "status": "deleting"}


def error(code: str, message: str, details=None, request_id="req-123") -> dict:
    body = {"code": code, "message": message, "request_id": request_id}
    if details:
        body["details"] = details
    return {"error": body}


def tokens(access="access-1", refresh="refresh-1", expires_in=300, refresh_in=3600) -> dict:
    return {"access_token": access, "refresh_token": refresh, "token_type": "Bearer",
            "expires_in": expires_in, "refresh_expires_in": refresh_in}
