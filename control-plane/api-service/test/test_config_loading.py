"""Settings loaded from environment variables (C1-C9). Goes through load_settings, not Settings()."""
import pytest

from api_service.config import ConfigError, load_settings


@pytest.fixture
def secret(tmp_path):
    p = tmp_path / "bootstrap"
    p.write_text("s")
    return str(p)


def test_list_int_and_bool_variables_are_parsed(secret):
    s = load_settings({"BOOTSTRAP_SECRET_FILE": secret, "ALLOWED_NAMESPACES": "a, b ,c", "PORT": "9000",
                       "KUBE_VERIFY_TLS": "false", "CORS_ALLOWED_ORIGINS": "https://ui.example"})
    assert s.allowed_namespaces == ["a", "b", "c"] and s.port == 9000
    assert s.kube_verify_tls is False and s.cors_allowed_origins == ["https://ui.example"]


def test_empty_allowlist_by_default(secret):
    assert load_settings({"BOOTSTRAP_SECRET_FILE": secret}).allowed_namespaces == []


@pytest.mark.parametrize("env,fragment", [
    ({"PORT": "abc"}, "PORT"),
    ({"MOCK_AUTH_ENABLED": "maybe"}, "MOCK_AUTH_ENABLED"),
    ({"CORS_ALLOWED_ORIGINS": "*"}, "CORS_ALLOWED_ORIGINS"),
    ({"ENVIRONMENT": "prod"}, "MOCK_AUTH_ENABLED"),
])
def test_invalid_settings_fail_fast(secret, env, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_settings({"BOOTSTRAP_SECRET_FILE": secret, **env})
