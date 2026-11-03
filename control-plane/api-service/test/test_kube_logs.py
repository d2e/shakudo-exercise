"""The Kubernetes adapter's log reader (D28): parsing, resuming, errors, and closing from another thread."""
import threading

import pytest
import urllib3
from kubernetes import client
from kubernetes.client.exceptions import ApiException

from api_service.domain import log_time_key
from api_service.kube import LOG_READ_TIMEOUT_SECONDS, KubeFailure, KubernetesAdapter, KubeLogStream, parse_log_line
from api_service.ports import KubeFailure
from support import make_settings


class FakeResponse:
    """What read_namespaced_pod_log returns with _preload_content=False: a chunked body that can be shut down."""

    def __init__(self, chunks, then_block=False, fail=None):
        self._chunks, self._block, self._fail = chunks, then_block, fail
        self.gate = threading.Event()
        self.calls = []

    def stream(self, amt):
        for c in self._chunks:
            yield c
        if self._fail:
            raise self._fail
        if self._block:
            self.gate.wait(5)
            raise urllib3.exceptions.ProtocolError("connection aborted by shutdown")

    def shutdown(self):
        self.calls.append("shutdown")
        self.gate.set()

    def close(self):
        self.calls.append("close")

    def release_conn(self):
        self.calls.append("release_conn")


def lines_of(stream):
    return [(x.time, x.line) for x in stream]


def test_lines_are_rebuilt_across_chunk_boundaries():
    body = [b"2026-01-01T00:00:01Z first li", b"ne\n2026-01-01T00:00:02.5Z sec", b"ond\r\n2026-01-01T00:00:03Z last"]
    assert lines_of(KubeLogStream(FakeResponse(body), None)) == [
        ("2026-01-01T00:00:01Z", "first line"), ("2026-01-01T00:00:02.5Z", "second"), ("2026-01-01T00:00:03Z", "last")]


def test_resume_drops_lines_at_or_before_the_time_even_when_zeros_are_trimmed():
    since = log_time_key("2026-01-01T00:00:02.500Z")
    body = [b"2026-01-01T00:00:02.5Z a\n2026-01-01T00:00:02.500000001Z b\n2026-01-01T00:00:03Z c\n"]
    assert [x for _, x in lines_of(KubeLogStream(FakeResponse(body), since))] == ["b", "c"]


def test_a_line_without_a_timestamp_is_kept_with_unknown_time():
    assert parse_log_line(b"no timestamp here", None).time == "" and parse_log_line(b"no timestamp here", None).line == "no timestamp here"
    assert parse_log_line("2026-01-01T00:00:00Z héllo".encode(), None).line == "héllo"
    assert parse_log_line(b"2026-01-01T00:00:00Z \xff\xfe", None).line  # invalid bytes never raise


def test_read_errors_become_a_kubefailure_kind_without_raw_text():
    with pytest.raises(KubeFailure) as e:
        list(KubeLogStream(FakeResponse([b"2026-01-01T00:00:00Z a\n"], fail=urllib3.exceptions.ProtocolError("10.0.0.5 reset")), None))
    assert e.value.kind == "unreachable" and "10.0.0.5" not in str(e.value)


def test_close_from_another_thread_unblocks_a_waiting_reader_without_an_error():
    resp = FakeResponse([b"2026-01-01T00:00:00Z a\n"], then_block=True)
    stream, seen, done = KubeLogStream(resp, None), [], threading.Event()

    def read():
        seen.extend(lines_of(stream))  # must return, not raise, once closed on purpose
        done.set()

    threading.Thread(target=read, daemon=True).start()
    assert not done.wait(0.3)  # waiting for the next line
    stream.close()
    assert done.wait(3) and seen == [("2026-01-01T00:00:00Z", "a")]
    assert resp.calls[0] == "shutdown" and "close" in resp.calls  # shutdown is what wakes the blocked read


class Recorder:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.args = result, error, None

    def __call__(self, name, namespace, **kwargs):
        self.args = (name, namespace, kwargs)
        if self.error:
            raise self.error
        return self.result


def adapter(recorder) -> KubernetesAdapter:
    a = KubernetesAdapter(make_settings(kube_request_timeout_seconds=7), api_client=client.ApiClient())
    a._core.read_namespaced_pod_log = recorder
    return a


def test_open_follows_with_timestamps_and_no_read_timeout():
    rec = Recorder(FakeResponse([]))
    adapter(rec).stream_pod_logs("demo", "p1", tail_lines=25)
    name, namespace, kw = rec.args
    assert (name, namespace) == ("p1", "demo")
    assert kw["follow"] is True and kw["timestamps"] is True and kw["_preload_content"] is False
    assert kw["tail_lines"] == 25 and "since_seconds" not in kw
    assert kw["_request_timeout"] == (7, LOG_READ_TIMEOUT_SECONDS)  # long read timeout: a quiet log must not end the stream


def test_resume_asks_for_a_coarse_window_and_leaves_the_exact_cut_to_the_reader():
    rec = Recorder(FakeResponse([]))
    adapter(rec).stream_pod_logs("demo", "p1", since_time="2020-01-01T00:00:00Z")
    assert rec.args[2]["since_seconds"] > 60  # a positive window; the exact "strictly after" is parse_log_line's job


@pytest.mark.parametrize("status,kind", [(404, "not_found"), (403, "forbidden"), (400, "other")])
def test_open_errors_are_classified(status, kind):
    with pytest.raises(KubeFailure) as e:
        adapter(Recorder(error=ApiException(status=status, reason="x"))).stream_pod_logs("demo", "p1")
    assert e.value.kind == kind


def test_log_request_arguments_pass_the_real_client_validation():
    """Regression (found live): the client validates _request_timeout and rejected (connect, None), so every stream
    failed with ValidationError. Our exact arguments go through the real client against a dead port: they must get
    as far as a connection attempt, never fail validation."""
    from kubernetes.client import Configuration
    cfg = Configuration()
    cfg.host = "http://127.0.0.1:9"
    a = KubernetesAdapter(make_settings(kube_request_timeout_seconds=1), api_client=client.ApiClient(cfg))
    with pytest.raises(KubeFailure) as e:
        a.stream_pod_logs("demo", "pod-1", tail_lines=10, since_time="2026-01-01T00:00:00Z")
    assert e.value.kind == "unreachable"
