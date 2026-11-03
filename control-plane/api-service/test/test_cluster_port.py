"""The port and its adapters (D27): every adapter has the same shape, and the SDK stays inside the adapter."""
import ast
import inspect
from pathlib import Path

import pytest

import api_service
from api_service.kube import KubernetesAdapter
from api_service.ports import FAILURE_KINDS, ClusterPort, KubeFailure
from support import FakeGateway

SRC = Path(api_service.__file__).parent
PORT_METHODS = sorted(n for n, v in vars(ClusterPort).items() if callable(v) and not n.startswith("_"))


def _shape(fn) -> list[tuple]:
    return [(p.name, p.default) for p in inspect.signature(fn).parameters.values() if p.name != "self"]


def _imports(path: Path) -> list[tuple[str, int]]:
    """(module, relative level) for every import in a file."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [(a.name, 0) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
    return found


def test_port_lists_the_operations_the_service_uses():
    assert "ping" in PORT_METHODS and "create_deployment" in PORT_METHODS and "stream_pod_logs" in PORT_METHODS and len(PORT_METHODS) == 13


@pytest.mark.parametrize("adapter", [KubernetesAdapter, FakeGateway], ids=["kubernetes", "fake"])
@pytest.mark.parametrize("method", PORT_METHODS)
def test_adapter_has_the_ports_signature(adapter, method):
    """Same parameter names and defaults, so a fake that passes the unit tests is a faithful stand-in."""
    assert hasattr(adapter, method), f"{adapter.__name__} lacks {method}"
    assert _shape(getattr(adapter, method)) == _shape(getattr(ClusterPort, method))


def test_fake_is_recognised_as_an_adapter():
    assert isinstance(FakeGateway(), ClusterPort)
    assert issubclass(KubernetesAdapter, ClusterPort)


def test_failure_kinds_are_the_documented_set():
    assert FAILURE_KINDS == ("not_found", "conflict", "unreachable", "forbidden", "other")
    assert KubeFailure("conflict").kind == "conflict"


def test_only_the_adapter_imports_the_kubernetes_sdk():
    users = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
             if any(m.split(".")[0] in ("kubernetes", "urllib3") for m, _ in _imports(p))}
    assert users == {"kube.py"}


def test_application_layer_depends_on_the_port_not_the_adapter():
    """app.py is the composition root and may name the adapter; nothing else may."""
    offenders = [p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
                 if p.name not in ("app.py", "kube.py")
                 and any(m.split(".")[-1] == "kube" and (lvl > 0 or m.startswith("api_service"))
                         for m, lvl in _imports(p))]
    assert offenders == []
