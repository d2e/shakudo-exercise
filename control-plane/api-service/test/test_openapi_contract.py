"""OpenAPI contract (F8-F15): committed file matches the code, errors documented, no default 422."""
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
COMMITTED = Path(__file__).resolve().parents[2] / "spec" / "openapi.yaml"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPTS / "export_openapi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def schema():
    return yaml.safe_load(load_exporter().render())


def test_committed_file_is_not_stale():
    assert COMMITTED.read_text() == load_exporter().render(), "run `make openapi` to regenerate"


def test_version_and_security_scheme(schema):
    assert schema["openapi"].startswith("3.1") and schema["info"]["version"] == "1.0.0"
    assert schema["components"]["securitySchemes"]["bearerAuth"] == {
        "type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


def test_no_422_and_errors_use_shared_schema(schema):
    for path, item in schema["paths"].items():
        for method, op in item.items():
            assert "422" not in op["responses"], (method, path)
            assert op["operationId"] and op["summary"] and op["tags"]
            if path.startswith("/api/v1/deployments"):
                assert op["security"] == [{"bearerAuth": []}]
                for status in ("401", "429", "500"):
                    ref = op["responses"][status]["content"]["application/json"]["schema"]["$ref"]
                    assert ref == "#/components/schemas/Error"


def test_operation_ids_are_unique(schema):
    ids = [op["operationId"] for item in schema["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))


def test_health_enum_values_are_documented(schema):
    desc = schema["components"]["schemas"]["DeploymentOut"]["properties"]["health"]["description"]
    for value in ("HEALTHY", "DEGRADED", "FAILING", "PROGRESSING", "UNKNOWN"):
        assert value in desc


def test_passes_spec_validator(schema):
    validator = pytest.importorskip("openapi_spec_validator", reason="openapi-spec-validator not installed")
    validator.validate(schema)
