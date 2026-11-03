"""Request and response models for log tickets (LG2, LG3)."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import validation as v
from ..domain import log_time_key


class LogTicketRequest(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid",
        json_schema_extra={"examples": [{"pod": "http-echo-6d4cf56db6-abcde", "tailLines": 100,
                                         "sinceTime": "2026-11-03T12:00:00Z"}]},
    )
    pod: Optional[str] = Field(None, description="A pod of this deployment. Omitted: all its pods, merged (D30).")
    tailLines: int = Field(100, ge=1, le=1000, description="Lines to start from, newest first")
    sinceTime: Optional[str] = Field(None, description="RFC 3339. Only lines strictly after this time.")

    @field_validator("pod")
    @classmethod
    def _pod(cls, value: Optional[str]):
        return v.check_dns_subdomain(value, "pod") if value is not None else value

    @field_validator("sinceTime")
    @classmethod
    def _since(cls, value: Optional[str]):
        if value is not None and log_time_key(value) is None:
            raise ValueError("sinceTime must be an RFC 3339 time such as 2026-11-03T12:00:00Z")
        return value


class LogTicketOut(BaseModel):
    ticket: str = Field(..., description="Short-lived, single use. Offer it as the `ticket.<value>` subprotocol.")
    streamPath: str = Field(..., description="WebSocket path; use the server address with the scheme swapped (https to wss)")
    expiresIn: int = Field(..., description="Seconds the ticket stays valid")
