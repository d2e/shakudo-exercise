import os
from pydantic import BaseModel

class Settings(BaseModel):
    # OIDC Configuration
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "https://auth.shakudo.local")
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "shakudo-k8s-platform")
    oidc_discovery_url: str = os.getenv("OIDC_DISCOVERY_URL", "")
    
    # Standalone Demo / Testing Mode (enables built-in OIDC discovery & token endpoints)
    mock_auth_enabled: bool = os.getenv("MOCK_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
    
    # Target Namespace restriction (if empty, allows any namespace where RBAC allows)
    allowed_namespaces: list[str] = [
        ns.strip() for ns in os.getenv("ALLOWED_NAMESPACES", "app-dev,default").split(",") if ns.strip()
    ]
    
    # Service Settings
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))
    log_level: str = os.getenv("LOG_LEVEL", "info")

    @property
    def discovery_endpoint(self) -> str:
        if self.oidc_discovery_url:
            return self.oidc_discovery_url
        return f"{self.oidc_issuer.rstrip('/')}/.well-known/openid-configuration"

settings = Settings()
