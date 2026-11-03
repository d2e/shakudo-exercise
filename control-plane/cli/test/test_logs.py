"""`deploy logs` against a fake log API (CT15): ticket flow, handshake, resume, close codes, safety."""
import json
import os

import pytest

import canned
from canned import UID
from deploy_cli import logs as logs_module
from deploy_cli.logs import Resume, clean, parse_since, ts_key, ws_url
from deploy_cli.store import Credentials
from logs_server import FakeLogsApi, frame

T1, T2, T3 = "2026-11-03T10:00:01.5Z", "2026-11-03T10:00:02Z", "2026-11-03T10:00:03.250Z"


@pytest.fixture
def api():
    fake = FakeLogsApi()
    yield fake
    fake.stop()


def logs(run, api, *args, **kw):
    return run("logs", UID, *args, target=api, **kw)


# ---- the flow ----
def test_ticket_then_websocket_with_only_the_ticket(api, run):
    api.scripts = [[("frame", frame("p-1", T1, "hello")), ("frame", frame("p-2", T2, "world")), ("close", 1000)]]
    r = logs(run, api)
    assert r.code == 0, r.err
    assert r.out.splitlines() == ["[p-1] hello", "[p-2] world"] and "Stream ended." in r.err
    (ticket,) = api.tickets
    assert ticket["uid"] == UID and ticket["body"] == {"tailLines": 100}
    assert ticket["headers"]["authorization"] == "Bearer tok-file-123"
    (conn,) = api.connections
    protocols = [p.strip() for p in conn["headers"]["sec-websocket-protocol"].split(",")]
    assert protocols == ["logs.v1", "ticket.tkt-1"]
    assert conn["query"] == "" and "tkt-1" not in conn["path"]  # never in the URL (LG6)
    assert "origin" not in conn["headers"] and "authorization" not in conn["headers"]  # LG8, LG6
    assert "tkt-1" not in r.out + r.err


def test_pod_flag_is_sent_and_lines_are_not_prefixed(api, run):
    api.scripts = [[("frame", frame("p-1", T1, "only mine")), ("close", 1000)]]
    r = logs(run, api, "--pod", "p-1")
    assert r.out == "only mine\n" and api.tickets[0]["body"] == {"tailLines": 100, "pod": "p-1"}


def test_tail_and_since_are_sent(api, run):
    r = logs(run, api, "--tail", "5", "--since", "10m")
    assert r.code == 0 and api.tickets[0]["body"] == {"tailLines": 5, "sinceTime": canned.iso(600)}


@pytest.mark.parametrize("bad", ["10", "m", "0s", "1w", "-5m", ""])
def test_bad_since_is_a_usage_error_with_no_request(api, run, bad):
    assert logs(run, api, "--since", bad).code == 2 and api.tickets == []


def test_json_output_is_the_frames_unchanged(api, run):
    f = frame("p-1", T1, "hi")
    api.scripts = [[("frame", f), ("close", 1000)]]
    assert [json.loads(l) for l in logs(run, api, "-o", "json").out.splitlines()] == [f]


def test_the_affinity_cookie_is_sent_on_the_handshake(api, run):
    api.cookie = "deploy_affinity=abc123; Path=/; HttpOnly"
    assert logs(run, api).code == 0
    assert api.connections[0]["headers"]["cookie"] == "deploy_affinity=abc123"


# ---- close codes ----
def test_token_expiry_refreshes_reconnects_and_never_repeats_a_line(api, run, home, clock):
    Credentials(home / "deploy" / "credentials").put(api.url, {
        "user": "alice", "access_token": "access-1", "refresh_token": "refresh-1",
        "expires_at": canned.NOW + 90, "refresh_expires_at": canned.NOW + 3600})
    api.scripts = [
        [("frame", frame("p-1", T1, "one")), ("frame", frame("p-1", T2, "two")), ("call", lambda: setattr(clock, "t", clock.t + 80)), ("close", 4001)],
        [("frame", frame("p-1", T2, "two")), ("frame", frame("p-1", T3, "three")), ("close", 1000)]]
    r = run("logs", UID, target=api, auth=False)
    assert r.code == 0, r.err
    assert r.out.splitlines() == ["[p-1] one", "[p-1] two", "[p-1] three"]  # "two" came again but was dropped
    first, second = api.tickets
    assert first["headers"]["authorization"] == "Bearer access-1"
    assert second["headers"]["authorization"] == "Bearer access-2" and api.refreshes == [{"refresh_token": "refresh-1"}]
    assert second["body"] == {"tailLines": 1000, "sinceTime": T2}
    assert len(api.connections) == 2 and "reconnecting" in r.err


def test_token_expiry_with_a_supplied_token_cannot_refresh(api, run):
    api.scripts = [[("frame", frame("p-1", T1, "one")), ("close", 4001)]]
    r = run("logs", UID, target=api, env={"DEPLOY_TOKEN": "tok-env"}, auth=False)
    assert r.code == 3 and "cannot be refreshed" in r.err and len(api.tickets) == 1
    assert r.out == "[p-1] one\n"


def test_server_restart_reconnects_with_the_same_token(api, run):
    api.scripts = [[("frame", frame("p-1", T1, "one")), ("close", 1001)],
                   [("frame", frame("p-1", T1, "one")), ("frame", frame("p-1", T2, "two")), ("close", 1000)]]
    r = logs(run, api)
    assert r.code == 0 and r.out.splitlines() == ["[p-1] one", "[p-1] two"]
    assert [t["headers"]["authorization"] for t in api.tickets] == ["Bearer tok-file-123"] * 2 and api.refreshes == []


@pytest.mark.parametrize("code,fragment", [(1013, "keep up"), (1011, "cluster failed"), (4999, "code 4999")])
def test_other_close_codes_end_with_a_fixed_message(api, run, code, fragment):
    api.scripts = [[("close", code)]]
    r = logs(run, api)
    assert r.code == 1 and fragment in r.err and len(api.tickets) == 1


def test_a_refused_handshake_is_reported_by_status_only(api, run):
    api.scripts = [[("refuse",)]]
    r = logs(run, api)
    assert r.code == 1 and "refused the log stream (HTTP 403)" in r.err


def test_the_fourth_stream_is_refused_at_the_ticket(api, run):
    api.ticket_replies = [(429, canned.error("too_many_streams", "too many open log streams"), {"Retry-After": "5"})]
    r = logs(run, api)
    assert r.code == 1 and "too many open log streams" in r.err and api.connections == []


def test_ticket_401_exits_3(api, run):
    api.ticket_replies = [(401, canned.error("invalid_token", "invalid token"), {})]
    assert logs(run, api).code == 3


# ---- safety and robustness ----
def test_terminal_escapes_in_log_text_are_made_visible(api, run):
    hostile = "ok \x1b[31mred\x1b]0;pwned\x07 \x9b2J end\ttab"
    api.scripts = [[("frame", frame("p\x1b[1m", T1, hostile)), ("close", 1000)]]
    out = logs(run, api).out
    assert "\x1b" not in out and "\x07" not in out and "\x9b" not in out
    assert "\\x1b[31mred" in out and "\\x07" in out and "end\ttab" in out and out.startswith("[p\\x1b[1m]")


def test_malformed_frames_are_skipped(api, run):
    api.scripts = [[("raw", "not json"), ("raw", '{"pod": "p"}'), ("raw", "[1,2]"), ("frame", frame("p-1", T1, "fine")), ("close", 1000)]]
    r = logs(run, api)
    assert r.code == 0 and r.out == "[p-1] fine\n"


def test_ctrl_c_ends_the_stream_cleanly(api, run, monkeypatch):
    api.scripts = [[("frame", frame("p-1", T1, "one")), ("hold",)]]
    real = logs_module.show
    def interrupt_after_first(*a, **k):
        real(*a, **k)
        raise KeyboardInterrupt
    monkeypatch.setattr(logs_module, "show", interrupt_after_first)
    r = logs(run, api)
    assert r.code == 0 and r.out == "[p-1] one\n"


# ---- helpers ----
def test_timestamps_sort_even_when_zeros_are_trimmed():
    assert ts_key("2026-01-01T00:00:00.5Z") > ts_key("2026-01-01T00:00:00.25Z")
    assert ts_key("2026-01-01T00:00:00.123456789Z") > ts_key("2026-01-01T00:00:00Z")
    assert ts_key("2026-01-01T00:00:01Z") > ts_key("2026-01-01T00:00:00.999999999Z")


def test_resume_drops_only_lines_it_has_shown():
    r = Resume(None)
    r.record(T2, "p", "a")
    assert not r.is_new(T2, "p", "a") and r.is_new(T2, "p", "b") and r.is_new(T2, "q", "a")
    assert r.is_new(T3, "p", "a")           # newer: always shown
    assert not r.is_new(T1, "p", "never-seen")  # older than where we got to: dropped


def test_resume_is_per_pod_so_an_older_line_from_another_pod_is_not_dropped():
    r = Resume(None)
    r.record(T3, "web-1", "late line of pod 1")
    assert r.is_new(T1, "web-2", "early line of pod 2")  # older than pod 1's newest, but pod 2 has shown nothing yet
    r.record(T1, "web-2", "early line of pod 2")
    assert r.since == T1                                  # a resume restarts from the oldest pod position
    assert not r.is_new(T1, "web-2", "early line of pod 2") and not r.is_new(T3, "web-1", "late line of pod 1")


def test_a_merged_stream_prints_every_pods_lines_whatever_the_arrival_order(api, run):
    api.scripts = [[("frame", frame("web-1", T2, "a")), ("frame", frame("web-2", T1, "b")),
                    ("frame", frame("web-1", T3, "c")), ("frame", frame("web-2", T2, "d")), ("close", 1000)]]
    assert logs(run, api).out.splitlines() == ["[web-1] a", "[web-2] b", "[web-1] c", "[web-2] d"]


def test_ws_url_swaps_the_scheme_and_keeps_a_base_path():
    assert ws_url("http://localhost:8000", "/api/v1/logs/stream") == "ws://localhost:8000/api/v1/logs/stream"
    assert ws_url("https://api.example.com/base", "/api/v1/logs/stream") == "wss://api.example.com/base/api/v1/logs/stream"


def test_clean_keeps_tabs_and_normal_text():
    assert clean("plain\ttext é ✓") == "plain\ttext é ✓" and clean("\x00") == "\\x00"
    assert parse_since("2h", canned.NOW) == canned.iso(7200)
