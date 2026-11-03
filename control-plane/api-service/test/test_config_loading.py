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


# ---- the signing key outside dev, with or without mock identity (LG4, K1) ----
def real_idp(**extra):
    return {"ENVIRONMENT": "prod", "MOCK_AUTH_ENABLED": "false", "ALLOWED_REGISTRIES": "registry.example.com",
            "OIDC_ISSUER": "https://auth.example.com", **extra}


def test_a_real_identity_provider_outside_dev_still_needs_a_signing_key():
    with pytest.raises(ConfigError, match="SIGNING_KEY_FILE: required outside dev"):
        load_settings(real_idp())


def test_a_real_identity_provider_with_a_signing_key_is_accepted(tmp_path):
    key = tmp_path / "signing-key"
    key.write_text("x")
    s = load_settings(real_idp(SIGNING_KEY_FILE=str(key)))
    assert s.environment == "prod" and s.signing_key_file == str(key)


def test_a_missing_signing_key_file_is_reported(tmp_path):
    with pytest.raises(ConfigError, match="SIGNING_KEY_FILE: file does not exist"):
        load_settings(real_idp(SIGNING_KEY_FILE=str(tmp_path / "nope")))


def test_dev_still_works_without_a_signing_key(secret):
    assert load_settings({"BOOTSTRAP_SECRET_FILE": secret}).signing_key_file == ""
