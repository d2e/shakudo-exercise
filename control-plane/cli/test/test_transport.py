"""Transport rules and error rendering (CT10, CT11)."""
import pytest

import canned

BASE = "/api/v1/deployments"


# ---- CT10: transport (R16) ----
def test_plain_http_to_a_non_local_server_is_refused(spec, run):
    from conftest import FakeServer
    remote = FakeServer(spec, host="127.0.0.2")  # not localhost, 127.0.0.1 or ::1
    try:
        remote.reply("GET", BASE, 200, {"items": []})
        r = run("list", target=remote)
        assert r.code == 2 and "plain http" in r.err and "--insecure" in r.err
        assert remote.seen == []
        r = run("list", "--insecure", target=remote)
        assert r.code == 0, r.err
        assert "WARNING" in r.err and "plain http" in r.err and len(remote.seen) == 1
    finally:
        remote.stop()


@pytest.mark.parametrize("raw", ["ftp://host", "localhost:8000", "http://", ""])
def test_bad_server_urls_are_usage_errors(run, raw):
    assert run("list", "--server", raw).code == 2


def test_a_redirect_is_not_followed_so_the_token_stays_home(server, other_server, run):
    other_server.reply("GET", BASE, 200, {"items": []})
    server.reply("GET", BASE, 302, None, headers={"Location": other_server.url + BASE})
    r = run("list")
    assert r.code == 1 and "redirected" in r.err and "never sent elsewhere" in r.err
    assert other_server.seen == []


def test_https_with_an_untrusted_certificate_is_a_clear_failure(run, server):
    # a plain-http server answering a TLS handshake is the quickest way to a TLS failure
    r = run("list", "--server", server.url.replace("http://", "https://"))
    assert r.code == 1 and ("TLS" in r.err or "cannot reach" in r.err) and "Traceback" not in r.err


# ---- CT11: errors ----
@pytest.mark.parametrize("status,code,message,exit_code,fragments", [
    (401, "invalid_token", "invalid token", 3, ["invalid token", "authentication", "rejected"]),
    (403, "namespace_not_allowed", "namespace is not allowed", 1, ["namespace is not allowed", "request id: req-123"]),
    (404, "not_found", "deployment not found", 1, ["deployment not found"]),
    (409, "conflict", "name already in use", 1, ["name already in use"]),
    (500, "cluster_error", "the cluster could not complete the request", 1, ["the cluster could not complete the request"]),
])
def test_api_errors_are_readable_with_the_right_exit_code(server, run, status, code, message, exit_code, fragments):
    server.reply("GET", BASE, status, canned.error(code, message))
    r = run("list", env={"DEPLOY_TOKEN": "t"}, auth=False)
    assert r.code == exit_code
    text = r.err.lower()
    assert message.lower() in text
    assert "{" not in r.err and "traceback" not in text and r.out == ""


def test_429_shows_retry_after(server, run):
    server.reply("GET", BASE, 429, canned.error("rate_limited", "too many requests"), headers={"Retry-After": "17"})
    r = run("list")
    assert r.code == 1 and "too many requests" in r.err and "Retry in 17 s" in r.err


def test_non_json_error_bodies_are_never_echoed(server, run):
    server.reply("GET", BASE, 502, b"<html><body>Bad gateway from proxy 10.0.0.7 secret=xyz</body></html>")
    r = run("list")
    assert r.code == 1 and "502" in r.err
    assert "<html>" not in r.err and "10.0.0.7" not in r.err and "secret=xyz" not in r.err


def test_a_response_that_is_not_json_is_reported_not_crashed(server, run):
    server.reply("GET", BASE, 200, b"not json")
    r = run("list")
    assert r.code == 1 and "unexpected response" in r.err and "Traceback" not in r.err


def test_the_contract_check_really_bites(server):
    import pytest as _pytest
    with _pytest.raises(AssertionError, match="breaks the OpenAPI contract"):
        server.reply("GET", BASE, 200, {"items": [{"uid": "x"}]})  # missing required fields
