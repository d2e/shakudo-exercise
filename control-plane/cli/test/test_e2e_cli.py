"""End to end: the real `deploy` CLI against the real API app on a real port (D28, req-logs.md).

Only the cluster is faked (the API's in-memory FakeGateway, scripted with pod logs). Everything else is real: the mock
identity login, the Bearer pipeline, the ticket endpoint, the WebSocket handshake with the ticket subprotocol, the stream
limit, token expiry, refresh and resume. The API code is imported here only to start it; the CLI never imports it (CL37).
"""
import sys
import threading
import time
from io import StringIO
from pathlib import Path

import pytest
import requests

API = Path(__file__).resolve().parents[2] / "api-service"
sys.path[:0] = [str(API / "src"), str(API / "test")]

from deploy_cli.context import Context  # noqa: E402
from deploy_cli.main import main  # noqa: E402
from deploy_cli.store import Credentials  # noqa: E402

pytestmark = pytest.mark.e2e


@pytest.fixture
def api():
    from e2e_server import run_api
    url, gateway, stop = run_api()
    gateway.url, gateway.bootstrap_file, gateway.bootstrap = url, stop.bootstrap_secret_file, stop.bootstrap_secret
    yield gateway
    gateway.end_log_streams()
    stop()


@pytest.fixture
def cli(api, home):
    def _cli(*args, env=None, tty=False, home_dir=None, user="alice", stdout=None):
        out, err = stdout or StringIO(), StringIO()
        ctx = Context(env={"XDG_CONFIG_HOME": str(home_dir or home), "HOME": str(home_dir or home), **(env or {})},
                      stdin=StringIO(""), stdout=out, stderr=err, stdin_tty=tty, stdout_tty=False, now=time.time,
                      sleep=time.sleep, prompt_secret=lambda p: "", getuser=lambda: user)
        code = main(["--server", api.url, *args], ctx)
        return code, out.getvalue(), err.getvalue()
    return _cli


@pytest.fixture
def logged_in(cli, api):
    code, out, err = cli("login", "--bootstrap-secret-file", api.bootstrap_file)
    assert code == 0, err
    return True


def create_deployment(cli, name="web", **extra) -> str:
    code, out, err = cli("create", "--name", name, "--image", "hashicorp/http-echo:1.0.0", "--namespace", "demo", *extra.get("flags", []))
    assert code == 0, err
    return next(line.split()[1] for line in out.splitlines() if line.strip().startswith("UID:"))


def script_pods(api, uid, **pods):
    """pods = {"web-1": ["line a", ...]}: the deployment's pods and what each one logged."""
    from support import log_lines, pod
    api.pods[uid] = [pod(name) for name in pods]
    for name, lines in pods.items():
        api.logs[("demo", name)] = log_lines(*lines)


def run_in_thread(cli, *args, **kw):
    box = {}
    t = threading.Thread(target=lambda: box.update(result=cli(*args, **kw)), daemon=True)
    t.start()
    return t, box


def wait_for(predicate, what, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


# ---- the whole CLI against the real API ----
def test_lifecycle_through_the_real_api(cli, api, logged_in):
    code, out, err = cli("create", "--name", "web", "--image", "hashicorp/http-echo:1.0.0", "--namespace", "demo", "--replicas", "2")
    assert code == 0, err
    assert "Created deployment web in demo" in out and "default resources applied" in out and "namespace demo was created" in out
    uid = next(l.split()[1] for l in out.splitlines() if l.strip().startswith("UID:"))

    code, out, err = cli("list")
    assert code == 0 and uid in out and "web" in out and "0/2" in out

    code, out, err = cli("status", uid[:8])  # a unique prefix
    assert code == 0 and "Name:       web" in out and "Replicas" in out and "Pods (0)" in out

    assert cli("scale", uid, "--replicas", "3")[0] == 0
    assert api.deployments[("demo", "web")].desired == 3
    assert cli("restart", uid)[0] == 0
    code, out, err = cli("delete", uid)  # no terminal, no --yes
    assert code == 2 and ("demo", "web") in api.deployments
    code, out, err = cli("delete", uid, "--yes", "--wait")
    assert code == 0 and "Deleting web in demo" in out and ("demo", "web") not in api.deployments


def test_the_real_api_enforces_login_and_the_cli_explains(cli, api, home):
    code, out, err = cli("list")
    assert code == 3 and "deploy login" in err
    code, out, err = cli("login", "--bootstrap-secret-file", api.bootstrap_file)
    assert code == 0
    assert "access_token" not in out + err
    code, out, err = cli("list")
    assert code == 0


def test_login_with_a_wrong_bootstrap_secret_is_rejected(cli, api, tmp_path):
    wrong = tmp_path / "wrong"
    wrong.write_text("not-the-secret")
    code, out, err = cli("login", "--bootstrap-secret-file", str(wrong))
    assert code == 3 and "rejected" in err and "not-the-secret" not in out + err


def test_server_validation_errors_reach_the_user_readably(cli, api, logged_in):
    code, out, err = cli("create", "--name", "Bad_Name", "--image", "nginx", "--namespace", "demo")
    assert code == 1 and "request validation failed" in err
    assert "- name:" in err and "- image:" in err and "request id:" in err and "{" not in err
    assert api.calls == []  # rejected before any cluster call


def test_a_namespace_outside_the_allowlist_is_refused(cli, api, logged_in):
    code, out, err = cli("create", "--name", "web", "--image", "nginx:1.27", "--namespace", "kube-system")
    assert code == 1 and "not allowed" in err.lower() or "reserved" in err.lower()


# ---- D28: logs through the ticket and the WebSocket ----
def test_logs_from_all_pods_are_merged_with_a_prefix_and_end_cleanly(cli, api, logged_in):
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["a1", "a2"], "web-2": ["b1"]})
    code, out, err = cli("logs", uid)
    assert code == 0, err
    lines = out.splitlines()
    assert sorted(lines) == ["[web-1] a1", "[web-1] a2", "[web-2] b1"]
    assert lines.index("[web-1] a1") < lines.index("[web-1] a2")  # one pod's lines stay in order
    assert "Stream ended." in err and "ticket" not in out + err.lower().replace("ticket expired", "")


def test_logs_for_one_pod_have_no_prefix(cli, api, logged_in):
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["a1", "a2"], "web-2": ["b1"]})
    code, out, err = cli("logs", uid, "--pod", "web-1")
    assert code == 0 and out.splitlines() == ["a1", "a2"]


def test_logs_with_a_prefix_id_and_json_output(cli, api, logged_in):
    import json
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["a1"]})
    code, out, err = cli("logs", uid[:8], "-o", "json")
    assert code == 0 and [json.loads(l)["line"] for l in out.splitlines()] == ["a1"]
    assert all(set(json.loads(l)) == {"pod", "time", "line"} for l in out.splitlines())


def test_a_pod_of_another_deployment_is_refused_at_the_ticket(cli, api, logged_in):
    uid = create_deployment(cli, "web")
    other = create_deployment(cli, "other")
    script_pods(api, uid, **{"web-1": ["a1"]})
    script_pods(api, other, **{"other-1": ["x"]})
    code, out, err = cli("logs", uid, "--pod", "other-1")
    assert code == 1 and "not found" in err.lower() and out == ""


def test_logs_for_an_unknown_deployment(cli, api, logged_in):
    code, out, err = cli("logs", "00000000-0000-4000-8000-000000000000")
    assert code == 1 and "not found" in err.lower()


def test_logs_need_a_login(cli, api):
    code, out, err = cli("logs", "00000000-0000-4000-8000-000000000000")
    assert code == 3


def test_a_cluster_failure_mid_stream_ends_with_a_fixed_message(cli, api, logged_in):
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["a1"]})
    api.log_failure = "unreachable"
    code, out, err = cli("logs", uid)
    assert code == 1 and "a1" in out and "cluster failed" in err
    assert "unreachable" not in err and "127.0.0.1" not in err  # no internals (R24)


def test_log_text_cannot_inject_terminal_escapes(cli, api, logged_in):
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["boom \x1b[2J\x1b]0;pwned\x07 done"]})
    code, out, err = cli("logs", uid, "--pod", "web-1")
    assert code == 0 and "\x1b" not in out and "\x07" not in out and "\\x1b[2J" in out


# ---- R21: at most 3 open streams per user ----
def test_the_fourth_open_stream_is_refused_and_the_others_end_cleanly(cli, api, logged_in):
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["a1"]})
    api.keep_open = True  # streams stay open after their lines
    threads = [run_in_thread(cli, "logs", uid) for _ in range(3)]
    wait_for(lambda: len(api.log_streams) == 3, "three open log streams")
    code, out, err = cli("logs", uid)
    assert code == 1 and "too many" in err.lower() and out == ""
    assert len(api.log_streams) == 3  # the fourth never reached the cluster
    api.end_log_streams()
    for t, box in threads:
        t.join(15)
        assert not t.is_alive()
        assert box["result"][0] == 0 and "a1" in box["result"][1]
    api.keep_open = False  # the next stream should end by itself
    code, out, err = cli("logs", uid)  # slots are free again
    assert code == 0 and "a1" in out


# ---- T8: the token expires mid-stream ----
def test_a_stream_survives_token_expiry_refresh_and_resume_without_repeats(cli, api, home):
    uid = create_deployment_with_token(cli, api, home)
    script_pods(api, uid, **{"web-1": ["l0", "l1", "l2"]})
    api.keep_open = True
    before = Credentials(home / "deploy" / "credentials").get(api.url)["access_token"]
    live = StringIO()  # what the CLI has printed so far
    t, box = run_in_thread(cli, "logs", uid, "--pod", "web-1", stdout=live)
    wait_for(lambda: live.getvalue().count("\n") >= 3, "the first three lines")
    from support import log_lines
    api.logs[("demo", "web-1")] += log_lines("l3", "l4", start=3)  # logged while the stream is open
    wait_for(lambda: len(api.log_streams) >= 2, "the reconnect after token expiry", timeout=20)
    api.end_log_streams()
    t.join(20)
    assert not t.is_alive()
    code, out, err = box["result"]
    assert code == 0, err
    assert "Token expired; reconnecting" in err
    assert out.splitlines() == ["l0", "l1", "l2", "l3", "l4"]  # every line once, in order, across two connections
    after = Credentials(home / "deploy" / "credentials").get(api.url)["access_token"]
    assert after != before  # the CLI refreshed the stored login


def create_deployment_with_token(cli, api, home) -> str:
    """Log in, create a deployment, then swap in an access token that really expires in 3 s while the stored expiry
    says 62 s: the CLI starts the stream without refreshing, and must refresh when the API closes it with 4001."""
    code, out, err = cli("login", "--bootstrap-secret-file", api.bootstrap_file)
    assert code == 0, err
    uid = create_deployment(cli)
    short = requests.post(f"{api.url}/api/v1/auth/token", json={"sub": "alice", "expires_in_seconds": 3},
                          headers={"X-Bootstrap-Secret": api.bootstrap}, timeout=5).json()
    now = time.time()
    Credentials(home / "deploy" / "credentials").put(api.url, {
        "user": "alice", "access_token": short["access_token"], "refresh_token": short["refresh_token"],
        "expires_at": now + 62, "refresh_expires_at": now + 3600})
    return uid


def test_token_expiry_with_a_supplied_token_ends_with_exit_3(cli, api, home):
    code, out, err = cli("login", "--bootstrap-secret-file", api.bootstrap_file)
    uid = create_deployment(cli)
    script_pods(api, uid, **{"web-1": ["l0"]})
    api.keep_open = True
    short = requests.post(f"{api.url}/api/v1/auth/token", json={"sub": "alice", "expires_in_seconds": 3},
                          headers={"X-Bootstrap-Secret": api.bootstrap}, timeout=5).json()
    t, box = run_in_thread(cli, "logs", uid, env={"DEPLOY_TOKEN": short["access_token"]})
    t.join(20)
    assert not t.is_alive()
    code, out, err = box["result"]
    assert code == 3 and "cannot be refreshed" in err and out == "[web-1] l0\n"


# ---- the installed `deploy` command as a real process ----
def _deploy_env(home) -> dict:
    import os
    return {**os.environ, "XDG_CONFIG_HOME": str(home), "HOME": str(home), "NO_PROXY": "127.0.0.1,localhost"}


def _lines(proc):
    """Lines from the process's stdout as they arrive. A thread reads, because select() on a buffered pipe can say
    "nothing" while the next line is already in Python's buffer."""
    import queue
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [q.put(line) for line in proc.stdout] or q.put(None), daemon=True).start()
    def get(timeout=15.0) -> str:
        try:
            line = q.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError("the deploy process printed nothing in time (output not flushed?)") from None
        assert line is not None, "the deploy process ended before printing the line"
        return line
    return get


@pytest.fixture
def deploy(api, home):
    import subprocess
    exe = Path(sys.executable).parent / "deploy"
    assert exe.exists(), "install the CLI first: uv pip install -e control-plane/cli"
    env = _deploy_env(home)
    def run(*args):
        return subprocess.run([str(exe), "--server", api.url, *args], env=env, capture_output=True, text=True, timeout=30)
    def popen(*args):
        return subprocess.Popen([str(exe), "--server", api.url, *args], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
    run.popen = popen
    return run


def test_the_installed_command_streams_logs_live_and_ctrl_c_ends_it_cleanly(deploy, api):
    import signal
    assert deploy("login", "--bootstrap-secret-file", api.bootstrap_file).returncode == 0
    created = deploy("create", "--name", "web", "--image", "hashicorp/http-echo:1.0.0", "--namespace", "demo")
    uid = next(l.split()[1] for l in created.stdout.splitlines() if l.strip().startswith("UID:"))
    script_pods(api, uid, **{"web-1": ["first", "second"]})
    api.keep_open = True
    proc = deploy.popen("logs", uid)
    next_line = _lines(proc)
    try:
        assert next_line().strip() == "[web-1] first"       # arrives while the stream is still open: live, flushed
        assert next_line().strip() == "[web-1] second"
        assert proc.poll() is None                          # still streaming
        proc.send_signal(signal.SIGINT)
        assert proc.wait(15) == 0                           # CL30: Ctrl-C ends the stream with exit 0
        assert "Traceback" not in proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_the_installed_command_ends_by_itself_when_the_pods_log_ends(deploy, api):
    assert deploy("login", "--bootstrap-secret-file", api.bootstrap_file).returncode == 0
    created = deploy("create", "--name", "web", "--image", "hashicorp/http-echo:1.0.0", "--namespace", "demo")
    uid = next(l.split()[1] for l in created.stdout.splitlines() if l.strip().startswith("UID:"))
    script_pods(api, uid, **{"web-1": ["only"]})
    done = deploy("logs", uid)
    assert done.returncode == 0 and done.stdout == "[web-1] only\n" and "Stream ended." in done.stderr
