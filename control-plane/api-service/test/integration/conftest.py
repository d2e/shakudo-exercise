"""Integration fixtures: the real app and KubernetesAdapter against the local cluster (Q2-Q6).

Run with `make test-integration`. Skipped, not failed, when no cluster is reachable (Q2).
Every namespace is `cp-test-<random>` and is removed at the end, even on failure (Q3).
"""
import uuid

import pytest
from api_service.config import Settings
from api_service.kube import KubernetesAdapter
from itsupport import BOOTSTRAP, build_api
from oracle import Oracle

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)


@pytest.fixture(scope="session")
def bootstrap_file(tmp_path_factory):
    p = tmp_path_factory.mktemp("secrets") / "bootstrap"
    p.write_text(BOOTSTRAP)
    return str(p)


@pytest.fixture(scope="session")
def cluster_ok(bootstrap_file):
    try:
        KubernetesAdapter(Settings(bootstrap_secret_file=bootstrap_file)).ping(timeout=3)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no reachable Kubernetes cluster ({type(e).__name__}); integration tests skipped")


@pytest.fixture(scope="session")
def oracle(cluster_ok):
    return Oracle()


@pytest.fixture
def namespace(cluster_ok, oracle):
    """A unique name only. Whether it exists is up to the test; teardown always removes it (Q3)."""
    name = f"cp-test-{uuid.uuid4().hex[:8]}"
    yield name
    oracle.delete_namespace(name)


@pytest.fixture
def api(bootstrap_file, namespace):
    return build_api(Settings(bootstrap_secret_file=bootstrap_file, allowed_namespaces=[namespace],
                              rate_limit_read_per_minute=100000, rate_limit_mutate_per_minute=100000,
                              rate_limit_auth_per_minute=100000))
