"""The commands against the fake server (CT1-CT6, CT12-CT14). The server validates every body against the OpenAPI file."""
import json

import pytest

import canned
from canned import UID, UID2, UID3

BASE = "/api/v1/deployments"


# ---- CT1, CT2: create ----
def test_create_sends_the_required_fields_only(server, run):
    server.reply("POST", BASE, 201, canned.created(namespace_created=True, resources_defaulted=True))
    r = run("create", "--name", "http-echo", "--image", "hashicorp/http-echo:1.0.0", "--namespace", "demo")
    assert r.code == 0, r.err
    (seen,) = server.requests("POST", BASE)
    assert seen.body == {"name": "http-echo", "image": "hashicorp/http-echo:1.0.0", "namespace": "demo"}
    assert seen.headers["authorization"] == "Bearer tok-file-123"
    assert UID in r.out and "namespace demo was created" in r.out and "default resources applied" in r.out


def test_create_with_every_optional_flag(server, run):
    server.reply("POST", BASE, 201, canned.created())
    r = run("create", "--name", "http-echo", "--image", "hashicorp/http-echo:1.0.0", "--namespace", "demo",
            "--replicas", "2", "--port", "5678", "--env", "LOG_LEVEL=info", "--env", "EXPR=a=b",
            "--secret-env", "DB_PASSWORD=db-secret/password", "--label", "team=platform",
            "--command", "/http-echo", "--arg", "-text=hello", "--arg", "-listen=:5678",
            "--cpu-request", "50m", "--memory-request", "64Mi", "--cpu-limit", "200m", "--memory-limit", "128Mi")
    assert r.code == 0, r.err
    assert server.requests("POST", BASE)[0].body == {
        "name": "http-echo", "image": "hashicorp/http-echo:1.0.0", "namespace": "demo", "replicas": 2, "port": 5678,
        "env": [{"name": "LOG_LEVEL", "value": "info"}, {"name": "EXPR", "value": "a=b"},
                {"name": "DB_PASSWORD", "secretRef": {"name": "db-secret", "key": "password"}}],
        "labels": {"team": "platform"}, "command": ["/http-echo"], "args": ["-text=hello", "-listen=:5678"],
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}}}


@pytest.mark.parametrize("flags,fragment", [
    (["--cpu-request", "50m"], "all four"),
    (["--cpu-request", "50m", "--memory-request", "64Mi", "--cpu-limit", "1"], "all four"),
    (["--env", "NOEQUALS"], "NAME=VALUE"),
    (["--secret-env", "A=nosecretkey"], "SECRET/KEY"),
    (["--label", "=x"], "NAME=VALUE"),
])
def test_create_usage_errors_send_nothing(server, run, flags, fragment):
    r = run("create", "--name", "a", "--image", "nginx:1.27", "--namespace", "demo", *flags)
    assert r.code == 2 and fragment in r.err
    assert server.seen == []


def test_create_missing_required_flag_is_a_usage_error(server, run):
    r = run("create", "--name", "a")
    assert r.code == 2 and "--image" in r.err and server.seen == []


def test_create_shows_the_servers_validation_errors(server, run):
    details = [{"field": "name", "rule": "invalid", "message": "name must be a DNS label"},
               {"field": "image", "rule": "tag_required", "message": "image needs a pinned tag"}]
    server.reply("POST", BASE, 400, canned.error("invalid_request", "request validation failed", details))
    r = run("create", "--name", "Bad", "--image", "nginx", "--namespace", "demo")
    assert r.code == 1
    assert "request validation failed" in r.err and "- name: name must be a DNS label" in r.err
    assert "- image: image needs a pinned tag" in r.err and "request id: req-123" in r.err
    assert "{" not in r.err  # no raw JSON (CL4)


# ---- CT3: list ----
def test_list_table_is_sorted_and_complete(server, run):
    server.reply("GET", BASE, 200, {"items": [
        canned.deployment(uid=UID3, name="zeta", namespace="demo", health="FAILING", desired=3, ready=0, age=7200),
        canned.deployment(uid=UID, name="alpha", namespace="demo", age=300),
        canned.deployment(uid=UID2, name="beta", namespace="a-team", health="PROGRESSING", desired=2, ready=1, age=90000)]})
    r = run("list")
    assert r.code == 0, r.err
    lines = r.out.splitlines()
    assert lines[0].split() == ["NAMESPACE", "NAME", "UID", "READY", "HEALTH", "IMAGE", "AGE"]
    assert [l.split()[:2] for l in lines[1:]] == [["a-team", "beta"], ["demo", "alpha"], ["demo", "zeta"]]
    assert lines[1].split()[2:] == [UID2, "1/2", "PROGRESSING", "hashicorp/http-echo:1.0.0", "1d"]
    assert lines[2].split()[-1] == "5m" and lines[3].split()[-1] == "2h"


def test_list_namespace_filter_and_empty_result(server, run):
    server.reply("GET", BASE, 200, {"items": []})
    r = run("list", "-n", "demo")
    assert server.requests("GET", BASE)[0].query == {"namespace": "demo"}
    assert r.code == 0 and r.out.strip() == "No deployments found."


def test_list_json_is_the_apis_json(server, run):
    body = {"items": [canned.deployment()]}
    server.reply("GET", BASE, 200, body)
    r = run("list", "-o", "json")
    assert json.loads(r.out) == body


def test_options_work_after_the_command_too(server, run, token_file):
    server.reply("GET", BASE, 200, {"items": []})
    r = run("list", "-o", "json", "--timeout", "5", auth=True)
    assert r.code == 0 and json.loads(r.out) == {"items": []}


# ---- CT4: status ----
def test_status_for_a_failing_deployment(server, run):
    server.reply("GET", f"{BASE}/{UID}", 200, canned.failing_detail())
    r = run("status", UID)
    assert r.code == 0, r.err
    out = r.out
    for expected in ("Name:       http-echo", f"UID:        {UID}", "Health:     FAILING", "Why: pod p-1, container http-echo: ImagePullBackOff",
                     "Replicas", "unavailable  2", "Conditions", "Progressing=False  ProgressDeadlineExceeded",
                     "Pods (2)", "p-1 / http-echo: waiting ImagePullBackOff"):
        assert expected in out, expected
    assert out.index("Replicas") < out.index("Conditions") < out.index("Pods (2)")


def test_status_of_a_healthy_deployment_has_no_why_line(server, run):
    server.reply("GET", f"{BASE}/{UID}", 200, canned.detail())
    r = run("status", UID)
    assert "Why:" not in r.out and "Health:     HEALTHY" in r.out


def test_status_not_found_is_readable(server, run):
    server.reply("GET", f"{BASE}/{UID}", 404, canned.error("not_found", "deployment not found"))
    r = run("status", UID)
    assert r.code == 1 and "deployment not found" in r.err


# ---- scale, restart ----
def test_scale_patches_replicas(server, run):
    server.reply("PATCH", f"{BASE}/{UID}", 200, canned.deployment(desired=3, ready=1))
    r = run("scale", UID, "--replicas", "3")
    assert r.code == 0 and "to 3 replicas (1/3 ready)" in r.out
    assert server.requests("PATCH")[0].body == {"replicas": 3}
    assert server.requests("GET") == []  # no confirmation, so no extra read


def test_restart_posts_to_the_restart_endpoint(server, run):
    server.reply("POST", f"{BASE}/{UID}/restart", 202, canned.deployment())
    r = run("restart", UID)
    assert r.code == 0 and "Restart triggered for http-echo in demo" in r.out
    assert server.requests("POST")[0].path == f"{BASE}/{UID}/restart"


# ---- CT5: delete confirmation ----
def delete_routes(server, wait_gets=None):
    server.reply("GET", f"{BASE}/{UID}", 200, canned.detail())
    server.reply("DELETE", f"{BASE}/{UID}", 202, canned.deleted())


def test_delete_answer_no_aborts_without_deleting(server, run):
    delete_routes(server)
    r = run("delete", UID, tty=True, stdin="n\n")
    assert r.code == 1 and "Aborted." in r.err
    assert server.requests("DELETE") == []
    assert f"Delete http-echo in demo ({UID})?" in r.err and "[y/N]" in r.err


def test_delete_default_answer_is_no(server, run):
    delete_routes(server)
    assert run("delete", UID, tty=True, stdin="\n").code == 1 and server.requests("DELETE") == []


@pytest.mark.parametrize("answer", ["y\n", "YES\n"])
def test_delete_answer_yes_deletes_and_says_deleting(server, run, answer):
    delete_routes(server)
    r = run("delete", UID, tty=True, stdin=answer)
    assert r.code == 0, r.err
    assert len(server.requests("DELETE")) == 1
    assert f"Deleting http-echo in demo ({UID})" in r.out and "background" in r.out and "deleted" not in r.out.lower().replace("deleting", "")


def test_delete_yes_flag_skips_the_prompt(server, run):
    delete_routes(server)
    r = run("delete", UID, "--yes")
    assert r.code == 0 and "[y/N]" not in r.err and len(server.requests("DELETE")) == 1


def test_delete_without_terminal_or_yes_is_refused_before_any_request(server, run):
    delete_routes(server)
    r = run("delete", UID, tty=False)
    assert r.code == 2 and "--yes" in r.err
    assert server.seen == []


# ---- CT6: id prefix ----
def list_for_prefix(server):
    server.reply("GET", BASE, 200, {"items": [canned.deployment(uid=UID, name="a"), canned.deployment(uid=UID2, name="b"),
                                              canned.deployment(uid=UID3, name="c")]})


def test_unique_prefix_is_resolved_through_list(server, run):
    list_for_prefix(server)
    server.reply("GET", f"{BASE}/{UID3}", 200, canned.detail(uid=UID3))
    r = run("status", UID3[:8])
    assert r.code == 0, r.err
    assert [s.path for s in server.seen] == [BASE, f"{BASE}/{UID3}"]


def test_ambiguous_prefix_lists_the_matches(server, run):
    list_for_prefix(server)
    r = run("status", UID[:8])
    assert r.code == 2 and "matches 2 deployments" in r.err and UID in r.err and UID2 in r.err
    assert len(server.seen) == 1  # only the list


def test_unknown_and_malformed_prefixes(server, run):
    list_for_prefix(server)
    assert run("status", "deadbeef").code == 1
    for bad in ("abc", "not-a-uid!", "12345"):
        assert run("status", bad).code == 2, bad


def test_delete_refuses_a_prefix(server, run):
    r = run("delete", UID[:8], "--yes")
    assert r.code == 2 and "full UID" in r.err and server.seen == []


# ---- CT12: no unsafe retries ----
def test_a_post_that_times_out_is_sent_exactly_once(server, run):
    server.reply("POST", BASE, 201, canned.created(), delay=0.8)
    r = run("create", "--name", "a", "--image", "nginx:1.27", "--namespace", "demo", "--timeout", "0.2")
    assert r.code == 1 and "timed out" in r.err
    assert len(server.requests("POST", BASE)) == 1


def test_a_get_that_times_out_is_retried_once(server, run):
    server.reply("GET", BASE, 200, {"items": []}, delay=0.8)
    r = run("list", "--timeout", "0.2")
    assert r.code == 1 and len(server.requests("GET", BASE)) == 2


def test_unreachable_server_is_readable(run, other_server):
    url = other_server.url
    other_server.stop()
    r = run("list", target=type("T", (), {"url": url})())
    assert r.code == 1 and "cannot reach" in r.err and "Traceback" not in r.err


# ---- CT13: --wait ----
def test_create_wait_ends_at_healthy(server, run, clock):
    server.reply("POST", BASE, 201, canned.created(health="PROGRESSING", ready=0))
    for health, ready in (("PROGRESSING", 0), ("PROGRESSING", 1), ("HEALTHY", 2)):
        server.reply("GET", f"{BASE}/{UID}", 200, canned.detail(health=health, ready=ready))
    r = run("create", "--name", "a", "--image", "nginx:1.27", "--namespace", "demo", "--wait")
    assert r.code == 0, r.err
    assert clock.sleeps == [2, 2] and "HEALTHY (2/2 ready)" in r.err


def test_create_wait_stops_early_when_failing(server, run, clock):
    server.reply("POST", BASE, 201, canned.created(health="PROGRESSING", ready=0))
    server.reply("GET", f"{BASE}/{UID}", 200, canned.failing_detail())
    r = run("create", "--name", "a", "--image", "nope:1.0", "--namespace", "demo", "--wait")
    assert r.code == 1 and "FAILING" in r.err and "ImagePullBackOff" in r.err and clock.sleeps == []


def test_wait_times_out(server, run, clock):
    server.reply("POST", f"{BASE}/{UID}/restart", 202, canned.deployment())
    server.reply("GET", f"{BASE}/{UID}", 200, canned.detail(health="PROGRESSING", ready=1))
    r = run("restart", UID, "--wait", "--wait-timeout", "5")
    assert r.code == 1 and "timed out after 5 s" in r.err and sum(clock.sleeps) >= 5


def test_delete_wait_ends_when_the_deployment_is_gone(server, run):
    server.reply("GET", f"{BASE}/{UID}", 200, canned.detail())     # for the confirmation
    server.reply("GET", f"{BASE}/{UID}", 200, canned.detail())     # still there
    server.reply("GET", f"{BASE}/{UID}", 404, canned.error("not_found", "deployment not found"))
    server.reply("DELETE", f"{BASE}/{UID}", 202, canned.deleted())
    r = run("delete", UID, "--yes", "--wait")
    assert r.code == 0, r.err and "gone" in r.err


# ---- CT14: scale to 0 ----
def test_scale_to_zero_asks_first(server, run):
    server.reply("GET", f"{BASE}/{UID}", 200, canned.detail())
    server.reply("PATCH", f"{BASE}/{UID}", 200, canned.deployment(desired=0, ready=0))
    assert run("scale", UID, "--replicas", "0", tty=True, stdin="n\n").code == 1
    assert server.requests("PATCH") == []
    r = run("scale", UID, "--replicas", "0", tty=True, stdin="y\n")
    assert r.code == 0 and server.requests("PATCH")[0].body == {"replicas": 0}


def test_scale_to_zero_without_terminal_needs_yes(server, run):
    server.reply("PATCH", f"{BASE}/{UID}", 200, canned.deployment(desired=0, ready=0))
    assert run("scale", UID, "--replicas", "0").code == 2 and server.requests("PATCH") == []
    assert run("scale", UID, "--replicas", "0", "--yes").code == 0
