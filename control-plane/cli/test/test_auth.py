"""Login, token sources, refresh, stored credentials and the affinity cookie (CT7-CT9, CT17, CT18)."""
import json
import os
import stat

import canned

BASE = "/api/v1/deployments"
TOKEN = "/api/v1/auth/token"
REFRESH = "/api/v1/auth/refresh"


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def list_ok(server):
    server.reply("GET", BASE, 200, {"items": []})


def login(server, run, tmp_path, **kw):
    secret = tmp_path / "bootstrap"
    secret.write_text("bs-secret\n")
    server.reply("POST", TOKEN, 200, canned.tokens(**kw))
    r = run("login", "--bootstrap-secret-file", str(secret), auth=False)
    assert r.code == 0, r.err
    return r


# ---- CT17: login ----
def test_login_sends_user_and_secret_and_stores_tokens_privately(server, run, home, tmp_path):
    r = login(server, run, tmp_path)
    (seen,) = server.requests("POST", TOKEN)
    assert seen.body == {"sub": "alice"} and seen.headers["x-bootstrap-secret"] == "bs-secret"
    assert "Logged in as alice" in r.out and "access-1" not in r.out + r.err and "refresh-1" not in r.out + r.err
    creds = home / "deploy" / "credentials"
    assert mode(home / "deploy") == 0o700 and mode(creds) == 0o600  # CT9
    entry = json.loads(creds.read_text())["servers"][server.url]
    assert entry["access_token"] == "access-1" and entry["expires_at"] == canned.NOW + 300


def test_login_user_flag_overrides_the_default(server, run, tmp_path):
    secret = tmp_path / "b"
    secret.write_text("x")
    server.reply("POST", TOKEN, 200, canned.tokens())
    assert run("login", "--user", "ops.team@example.com", "--bootstrap-secret-file", str(secret), auth=False).code == 0
    assert server.requests("POST", TOKEN)[0].body == {"sub": "ops.team@example.com"}


def test_login_with_an_invalid_default_user_asks_for_user_flag(server, run, tmp_path):
    secret = tmp_path / "b"
    secret.write_text("x")
    r = run("login", "--bootstrap-secret-file", str(secret), user="bad user!", auth=False)
    assert r.code == 2 and "--user" in r.err and server.seen == []


def test_login_prompts_for_the_secret_only_on_a_terminal(server, run):
    server.reply("POST", TOKEN, 200, canned.tokens())
    assert run("login", tty=True, secret="typed-secret", auth=False).code == 0
    assert server.requests("POST", TOKEN)[0].headers["x-bootstrap-secret"] == "typed-secret"
    r = run("login", tty=False, auth=False)
    assert r.code == 2 and "terminal" in r.err


def test_login_outcomes(server, run, tmp_path):
    secret = tmp_path / "b"
    secret.write_text("x")
    server.reply("POST", TOKEN, 401, canned.error("invalid_bootstrap_secret", "invalid bootstrap secret"))
    r = run("login", "--bootstrap-secret-file", str(secret), auth=False)
    assert r.code == 3 and "rejected" in r.err
    server._routes.clear()
    server.reply("POST", TOKEN, 404, canned.error("not_found", "not found"))
    r = run("login", "--bootstrap-secret-file", str(secret), auth=False)
    assert r.code == 2 and "not a dev server" in r.err and "--token-file" in r.err


def test_the_secret_is_never_a_command_line_argument(run):
    assert run("login", "--bootstrap-secret", "x", auth=False).code == 2


# ---- CT7: token sources ----
def test_token_source_order(server, run, tmp_path, token_file):
    list_ok(server)
    login(server, run, tmp_path)  # stored: access-1
    env = {"DEPLOY_TOKEN": "tok-env"}
    seen = lambda: server.requests("GET", BASE)[-1].headers["authorization"]
    assert run("list", "--token-file", str(token_file), "--token-stdin", env=env, stdin="tok-stdin").code == 0 and seen() == "Bearer tok-file-123"
    assert run("list", "--token-stdin", env=env, stdin="tok-stdin\n").code == 0 and seen() == "Bearer tok-stdin"
    assert run("list", env=env, auth=False).code == 0 and seen() == "Bearer tok-env"
    assert run("list", auth=False).code == 0 and seen() == "Bearer access-1"


def test_the_token_never_appears_in_output_even_with_debug_and_errors(server, run):
    server.reply("GET", BASE, 500, canned.error("cluster_error", "the cluster could not complete the request"))
    r = run("list", "--debug", env={"DEPLOY_TOKEN": "tok-env-secret"}, auth=False)
    assert r.code == 1
    assert "tok-env-secret" not in r.out + r.err and "tok-file-123" not in r.out + r.err
    assert "debug: GET /api/v1/deployments -> 500" in r.err
    assert "authorization" not in r.err.lower()


def test_there_is_no_token_flag(run):
    assert run("list", "--token", "secret").code == 2


def test_a_token_with_whitespace_is_refused(server, run):
    r = run("list", env={"DEPLOY_TOKEN": "a b"}, auth=False)
    assert r.code == 2 and server.seen == []


def test_not_logged_in_exits_3(server, run):
    r = run("list", auth=False)
    assert r.code == 3 and "deploy login" in r.err and server.seen == []


# ---- CT8: refresh ----
def test_a_token_with_60_seconds_or_less_is_refreshed_first(server, run, tmp_path, clock, home):
    login(server, run, tmp_path, access="access-old", refresh="refresh-old")
    list_ok(server)
    server.reply("POST", REFRESH, 200, canned.tokens(access="access-new", refresh="refresh-new"))
    clock.t += 240  # 60 s left of 300
    assert run("list", auth=False).code == 0
    assert server.requests("POST", REFRESH)[0].body == {"refresh_token": "refresh-old"}
    assert "authorization" not in server.requests("POST", REFRESH)[0].headers
    assert server.requests("GET", BASE)[-1].headers["authorization"] == "Bearer access-new"
    stored = json.loads((home / "deploy" / "credentials").read_text())["servers"][server.url]
    assert stored["refresh_token"] == "refresh-new"


def test_a_fresh_token_is_not_refreshed(server, run, tmp_path, clock):
    login(server, run, tmp_path)
    list_ok(server)
    clock.t += 100
    assert run("list", auth=False).code == 0 and server.requests("POST", REFRESH) == []


def test_failed_refresh_asks_for_login(server, run, tmp_path, clock):
    login(server, run, tmp_path)
    server.reply("POST", REFRESH, 401, canned.error("invalid_token", "invalid token"))
    clock.t += 250
    r = run("list", auth=False)
    assert r.code == 3 and "deploy login" in r.err and server.requests("GET") == []


def test_an_expired_refresh_token_does_not_even_try(server, run, tmp_path, clock):
    login(server, run, tmp_path)
    clock.t += 4000
    r = run("list", auth=False)
    assert r.code == 3 and "expired" in r.err and server.requests("POST", REFRESH) == []


def test_deploy_token_is_never_refreshed_and_a_401_says_so(server, run):
    server.reply("GET", BASE, 401, canned.error("invalid_token", "token expired"))
    r = run("list", env={"DEPLOY_TOKEN": "tok-env"}, auth=False)
    assert r.code == 3 and "rejected" in r.err and server.requests("POST", REFRESH) == []


def test_a_401_with_a_stored_token_says_login(server, run, tmp_path):
    login(server, run, tmp_path)
    server.reply("GET", BASE, 401, canned.error("invalid_token", "invalid token"))
    r = run("list", auth=False)
    assert r.code == 3 and "Run `deploy login`." in r.err


# ---- CT9: stored credentials permissions ----
def test_credentials_that_others_can_read_are_refused(server, run, tmp_path, home):
    login(server, run, tmp_path)
    os.chmod(home / "deploy" / "credentials", 0o644)
    r = run("list", auth=False)
    assert r.code == 3 and "chmod 600" in r.err and server.requests("GET") == []


# ---- CT18: affinity cookie ----
def test_the_affinity_cookie_is_stored_and_sent_only_to_its_server(server, other_server, run, home):
    server.reply("GET", BASE, 200, {"items": []}, headers={"Set-Cookie": "deploy_affinity=abc123; Path=/; HttpOnly; Secure"})
    other_server.reply("GET", BASE, 200, {"items": []})
    assert run("list").code == 0
    assert "cookie" not in server.requests("GET")[0].headers
    assert mode(home / "deploy" / "cookies") == 0o600
    assert run("list").code == 0
    assert server.requests("GET")[1].headers["cookie"] == "deploy_affinity=abc123"
    assert run("list", target=other_server).code == 0
    assert "cookie" not in other_server.requests("GET")[0].headers
