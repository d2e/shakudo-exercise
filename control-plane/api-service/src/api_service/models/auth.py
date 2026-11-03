"""Mock identity request and response models."""
from typing import Optional

from pydantic import BaseModel, Field

from .common import STRICT


class TokenRequest(BaseModel):
    model_config = STRICT
    sub: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@:-]+$", examples=["sre-engineer"])
    email: Optional[str] = Field(None, max_length=254)
    roles: list[str] = Field(default_factory=list, max_length=20)
    expires_in_seconds: Optional[int] = Field(None, ge=1)


class RefreshRequest(BaseModel):
    model_config = STRICT
    refresh_token: str = Field(..., min_length=1, max_length=8192)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_expires_in: int
