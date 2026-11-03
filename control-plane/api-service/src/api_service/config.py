"""Typed settings read from environment variables (C1-C9, R3, R6, R17-R20).

Secrets are never read from values: only file paths are configured (K1, C4).
"""
import os
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse

from pydantic import BaseModel

DEV = "dev"


class ConfigError(Exception):
    """Raised at startup when settings are invalid. Names settings, never secret values."""


class Settings(BaseModel):
    environment: str = DEV
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"

    # identity
    mock_auth_enabled: bool = True
    oidc_issuer: str = "https://auth.shakudo.local"
    oidc_audience: str = "api-service"
    oidc_discovery_url: str = ""
    bootstrap_secret_file: str = ""
    signing_key_file: str = ""
    access_token_ttl_seconds: int = 300
    refresh_token_ttl_seconds: int = 3600
    clock_skew_seconds: int = 30
    jwks_cache_ttl_seconds: int = 600
    jwks_refetch_cooldown_seconds: int = 30
    jwks_max_stale_seconds: int = 3600

    # scope and limits
    allowed_namespaces: list[str] = []
    allowed_registries: list[str] = []
    max_replicas: int = 20
    max_cpu_limit: str = "2"
    max_memory_limit: str = "2Gi"
    # applied when create omits `resources` (D31); must satisfy V14 and the R19 caps
    default_cpu_request: str = "50m"
    default_memory_request: str = "64Mi"
    default_cpu_limit: str = "200m"
    default_memory_limit: str = "128Mi"
    progress_deadline_seconds: int = 60
    max_body_bytes: int = 1024 * 1024
    rate_limit_auth_per_minute: int = 10
    rate_limit_mutate_per_minute: int = 30
    rate_limit_read_per_minute: int = 300
    cors_allowed_origins: list[str] = []

    # kubernetes connection
    kube_api_server: str = ""
    kube_ca_file: str = ""
    kube_token_file: str = ""
    kube_verify_tls: bool = True
    kube_request_timeout_seconds: int = 10
    kubeconfig: str = ""
    kube_context: str = ""

    @property
    def docs_enabled(self) -> bool:
        """OpenAPI and docs are served only in dev (R23)."""
        return self.environment == DEV

    @property
    def discovery_endpoint(self) -> str:
        return self.oidc_discovery_url or f"{self.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"


def _readable(path: str) -> bool:
    p = Path(path)
    return p.is_file() and os.access(p, os.R_OK)


def validate_settings(s: Settings) -> list[str]:
    """Return a list of 'SETTING: problem' strings. Empty means valid (C6)."""
    errors: list[str] = []
    if s.mock_auth_enabled and s.environment != DEV:
        errors.append("MOCK_AUTH_ENABLED: must be false outside the dev environment")
    if s.mock_auth_enabled:
        if not s.bootstrap_secret_file:
            errors.append("BOOTSTRAP_SECRET_FILE: required when MOCK_AUTH_ENABLED is true")
        elif not _readable(s.bootstrap_secret_file):
            errors.append("BOOTSTRAP_SECRET_FILE: file does not exist or is not readable")
        if s.environment != DEV and not s.signing_key_file:
            errors.append("SIGNING_KEY_FILE: required outside dev")
    if s.signing_key_file and not _readable(s.signing_key_file):
        errors.append("SIGNING_KEY_FILE: file does not exist or is not readable")
    if not 0 < s.access_token_ttl_seconds <= 300:
        errors.append("ACCESS_TOKEN_TTL_SECONDS: must be between 1 and 300")
    if s.refresh_token_ttl_seconds <= s.access_token_ttl_seconds:
        errors.append("REFRESH_TOKEN_TTL_SECONDS: must be longer than the access token lifetime")
    for name, value in (
        ("MAX_REPLICAS", s.max_replicas),
        ("PROGRESS_DEADLINE_SECONDS", s.progress_deadline_seconds),
        ("KUBE_REQUEST_TIMEOUT_SECONDS", s.kube_request_timeout_seconds),
        ("RATE_LIMIT_AUTH_PER_MINUTE", s.rate_limit_auth_per_minute),
        ("RATE_LIMIT_MUTATE_PER_MINUTE", s.rate_limit_mutate_per_minute),
        ("RATE_LIMIT_READ_PER_MINUTE", s.rate_limit_read_per_minute),
    ):
        if value <= 0:
            errors.append(f"{name}: must be greater than 0")
    if not 0 < s.port < 65536:
        errors.append("PORT: must be between 1 and 65535")
    if s.environment != DEV and not s.allowed_registries:
        errors.append("ALLOWED_REGISTRIES: required outside dev")
    if "*" in s.cors_allowed_origins:
        errors.append("CORS_ALLOWED_ORIGINS: a wildcard is not allowed (credentials are used)")
    if s.kube_api_server:
        u = urlparse(s.kube_api_server)
        if u.scheme not in ("http", "https") or not u.netloc:
            errors.append("KUBE_API_SERVER: must be a URL such as https://host:6443")
        if not s.kube_token_file or not _readable(s.kube_token_file):
            errors.append("KUBE_TOKEN_FILE: required with KUBE_API_SERVER and must be readable")
        if s.kube_verify_tls and (not s.kube_ca_file or not _readable(s.kube_ca_file)):
            errors.append("KUBE_CA_FILE: required with KUBE_API_SERVER when TLS verification is on")
    from .validation import parse_quantity  # local import: avoid cycle at module load

    for name, value in (("MAX_CPU_LIMIT", s.max_cpu_limit), ("MAX_MEMORY_LIMIT", s.max_memory_limit)):
        try:
            parse_quantity(value)
        except ValueError:
            errors.append(f"{name}: not a valid quantity")
    errors += _default_resource_errors(s, parse_quantity)
    return errors


def _default_resource_errors(s: Settings, parse_quantity) -> list[str]:
    """D31: the defaults are applied to every create that omits `resources`, so they must be valid themselves."""
    errors: list[str] = []
    parsed = {}
    for name, value in (("DEFAULT_CPU_REQUEST", s.default_cpu_request), ("DEFAULT_MEMORY_REQUEST", s.default_memory_request),
                        ("DEFAULT_CPU_LIMIT", s.default_cpu_limit), ("DEFAULT_MEMORY_LIMIT", s.default_memory_limit)):
        try:
            parsed[name] = parse_quantity(value)
        except ValueError:
            errors.append(f"{name}: not a valid quantity")
    if len(parsed) == 4:
        for res, caps in (("CPU", s.max_cpu_limit), ("MEMORY", s.max_memory_limit)):
            if parsed[f"DEFAULT_{res}_LIMIT"] < parsed[f"DEFAULT_{res}_REQUEST"]:
                errors.append(f"DEFAULT_{res}_LIMIT: must be at least DEFAULT_{res}_REQUEST")
            try:
                if parsed[f"DEFAULT_{res}_LIMIT"] > parse_quantity(caps):
                    errors.append(f"DEFAULT_{res}_LIMIT: must not exceed MAX_{res}_LIMIT")
            except ValueError:
                pass  # the cap itself is reported above
    return errors


def _bool(name: str, raw: str) -> bool:
    v = raw.strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    raise ConfigError(f"{name}: expected true or false")


def _int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name}: expected an integer") from None


def _list(name: str, raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


_ENV_FIELDS = {
    # env name: (field, converter)
    "ENVIRONMENT": ("environment", str),
    "HOST": ("host", str),
    "PORT": ("port", _int),
    "LOG_LEVEL": ("log_level", str),
    "MOCK_AUTH_ENABLED": ("mock_auth_enabled", _bool),
    "OIDC_ISSUER": ("oidc_issuer", str),
    "OIDC_AUDIENCE": ("oidc_audience", str),
    "OIDC_DISCOVERY_URL": ("oidc_discovery_url", str),
    "BOOTSTRAP_SECRET_FILE": ("bootstrap_secret_file", str),
    "SIGNING_KEY_FILE": ("signing_key_file", str),
    "ACCESS_TOKEN_TTL_SECONDS": ("access_token_ttl_seconds", _int),
    "REFRESH_TOKEN_TTL_SECONDS": ("refresh_token_ttl_seconds", _int),
    "CLOCK_SKEW_SECONDS": ("clock_skew_seconds", _int),
    "JWKS_CACHE_TTL_SECONDS": ("jwks_cache_ttl_seconds", _int),
    "JWKS_REFETCH_COOLDOWN_SECONDS": ("jwks_refetch_cooldown_seconds", _int),
    "JWKS_MAX_STALE_SECONDS": ("jwks_max_stale_seconds", _int),
    "ALLOWED_NAMESPACES": ("allowed_namespaces", _list),
    "ALLOWED_REGISTRIES": ("allowed_registries", _list),
    "MAX_REPLICAS": ("max_replicas", _int),
    "MAX_CPU_LIMIT": ("max_cpu_limit", str),
    "MAX_MEMORY_LIMIT": ("max_memory_limit", str),
    "DEFAULT_CPU_REQUEST": ("default_cpu_request", str),
    "DEFAULT_MEMORY_REQUEST": ("default_memory_request", str),
    "DEFAULT_CPU_LIMIT": ("default_cpu_limit", str),
    "DEFAULT_MEMORY_LIMIT": ("default_memory_limit", str),
    "PROGRESS_DEADLINE_SECONDS": ("progress_deadline_seconds", _int),
    "MAX_BODY_BYTES": ("max_body_bytes", _int),
    "RATE_LIMIT_AUTH_PER_MINUTE": ("rate_limit_auth_per_minute", _int),
    "RATE_LIMIT_MUTATE_PER_MINUTE": ("rate_limit_mutate_per_minute", _int),
    "RATE_LIMIT_READ_PER_MINUTE": ("rate_limit_read_per_minute", _int),
    "CORS_ALLOWED_ORIGINS": ("cors_allowed_origins", _list),
    "KUBE_API_SERVER": ("kube_api_server", str),
    "KUBE_CA_FILE": ("kube_ca_file", str),
    "KUBE_TOKEN_FILE": ("kube_token_file", str),
    "KUBE_VERIFY_TLS": ("kube_verify_tls", _bool),
    "KUBE_REQUEST_TIMEOUT_SECONDS": ("kube_request_timeout_seconds", _int),
    "KUBECONFIG": ("kubeconfig", str),
    "KUBE_CONTEXT": ("kube_context", str),
}


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Read settings from the environment and fail fast on any problem (C6)."""
    env = os.environ if env is None else env
    values: dict = {}
    for var, (field, conv) in _ENV_FIELDS.items():
        if var in env and env[var] != "":
            values[field] = conv(var, env[var]) if conv is not str else env[var]
    settings = Settings(**values)
    errors = validate_settings(settings)
    if errors:
        raise ConfigError("invalid configuration: " + "; ".join(errors))
    return settings
