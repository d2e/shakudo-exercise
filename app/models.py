from typing import Optional, List
from pydantic import BaseModel, Field

class TokenClaims(BaseModel):
    sub: str = Field(..., description="Subject identifier")
    iss: str = Field(..., description="Issuer identifier")
    aud: str = Field(..., description="Audience")
    exp: int = Field(..., description="Expiration timestamp")
    nbf: Optional[int] = Field(None, description="Not before timestamp")
    email: Optional[str] = Field(None, description="User email")
    roles: List[str] = Field(default_factory=list, description="Assigned roles")

class PodSummary(BaseModel):
    name: str = Field(..., description="Pod name")
    namespace: str = Field(..., description="Pod namespace")
    phase: str = Field(..., description="Pod phase (Running, Pending, Succeeded, Failed)")
    ready: str = Field(..., description="Ready containers ratio (e.g. 1/1)")
    ip: Optional[str] = Field(None, description="Pod IP address")
    node: Optional[str] = Field(None, description="Assigned cluster node")
    restart_count: int = Field(0, description="Total restarts across containers")
    created_at: Optional[str] = Field(None, description="Creation timestamp")

class PodListResponse(BaseModel):
    namespace: str
    pod_count: int
    pods: List[PodSummary]

class TokenRequest(BaseModel):
    sub: str = "test-engineer"
    email: str = "engineer@shakudo.local"
    roles: List[str] = ["platform-engineer"]
    expires_in_seconds: int = 3600

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
