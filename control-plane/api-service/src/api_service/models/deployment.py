"""Deployment request models. Strict types, unknown fields rejected (V4, V5, V8)."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .. import validation as v
from .common import STRICT


class QuantityPair(BaseModel):
    model_config = STRICT
    cpu: str = Field(..., examples=["50m"])
    memory: str = Field(..., examples=["64Mi"])

    @field_validator("cpu", "memory")
    @classmethod
    def _quantity(cls, value: str) -> str:
        v.parse_quantity(value)
        return value


class Resources(BaseModel):
    model_config = STRICT
    requests: QuantityPair
    limits: QuantityPair

    @model_validator(mode="after")
    def _limits_cover_requests(self):
        for res in ("cpu", "memory"):
            if v.parse_quantity(getattr(self.limits, res)) < v.parse_quantity(getattr(self.requests, res)):
                raise ValueError(f"limits.{res} must be at least requests.{res}")
        return self


class SecretRef(BaseModel):
    model_config = STRICT
    name: str
    key: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return v.check_dns_subdomain(value, "secret name")

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return v.check_secret_key(value)


class EnvVar(BaseModel):
    model_config = STRICT
    name: str
    value: Optional[str] = Field(None, max_length=4096)
    secretRef: Optional[SecretRef] = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return v.check_env_name(value)

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.value is None) == (self.secretRef is None):
            raise ValueError("exactly one of value or secretRef is required")
        return self


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid",
        json_schema_extra={"examples": [{
            "name": "http-echo", "namespace": "demo",
            "image": "hashicorp/http-echo:1.0.0", "replicas": 2, "port": 5678,
            "resources": {"requests": {"cpu": "50m", "memory": "64Mi"},
                          "limits": {"cpu": "200m", "memory": "128Mi"}},
            "env": [{"name": "LOG_LEVEL", "value": "info"}],
            "labels": {"team": "platform"},
            "command": ["/http-echo"], "args": ["-text=hello", "-listen=:5678"],
        }]},
    )
    name: str
    namespace: str
    image: str
    replicas: int = Field(1, ge=0)
    port: Optional[int] = Field(None, ge=1, le=65535)
    resources: Optional[Resources] = Field(
        None, description="Optional (D31): omitted means the configured defaults. When given it must be complete.")
    env: list[EnvVar] = Field(default_factory=list, max_length=50)
    labels: dict[str, str] = Field(default_factory=dict, max_length=20)
    command: Optional[list[str]] = Field(None, max_length=50)
    args: Optional[list[str]] = Field(None, max_length=50)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return v.check_dns_label(value, "name")

    @field_validator("namespace")
    @classmethod
    def _namespace(cls, value: str) -> str:
        v.check_dns_label(value, "namespace")
        if value in v.RESERVED_NAMESPACES:
            raise ValueError("this namespace is reserved")
        return value

    @field_validator("image")
    @classmethod
    def _image(cls, value: str) -> str:
        v.parse_image(value)
        return value

    @field_validator("labels")
    @classmethod
    def _labels(cls, value: dict) -> dict:
        for key, val in value.items():
            v.check_label_key(key)
            v.check_label_value(val)
        return value

    @field_validator("command", "args")
    @classmethod
    def _strings(cls, value: Optional[list]):
        if value is not None and any(len(s) > 4096 for s in value):
            raise ValueError("each item must be at most 4096 characters")
        return value

    @field_validator("command")
    @classmethod
    def _command_not_empty(cls, value: Optional[list]):
        if value is not None and any(s == "" for s in value):
            raise ValueError("command entries must not be empty")
        return value

    @field_validator("env")
    @classmethod
    def _unique_env(cls, value: list) -> list:
        names = [e.name for e in value]
        if len(names) != len(set(names)):
            raise ValueError("environment variable names must be unique")
        return value


class DeploymentPatch(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid",
        json_schema_extra={"examples": [{"replicas": 3, "image": "hashicorp/http-echo:1.0.1"}]},
    )
    replicas: Optional[int] = Field(None, ge=0)
    image: Optional[str] = None

    @field_validator("image")
    @classmethod
    def _image(cls, value: Optional[str]):
        if value is not None:
            v.parse_image(value)
        return value

    @model_validator(mode="after")
    def _at_least_one(self):
        if self.replicas is None and self.image is None:
            raise ValueError("provide at least one of replicas or image")
        return self
