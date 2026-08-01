from uuid import UUID

from pydantic_settings import BaseSettings, SettingsConfigDict


class TUISettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTRA_", env_file=".env", extra="ignore")

    agent_url: str = "http://127.0.0.1:8000"
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    access_token: str | None = None
    conversation_id: UUID | None = None
    tui_dev_login: bool = False
    tui_oidc_token_url: str = (
        "http://localhost:8080/realms/astra-agent/protocol/openid-connect/token"
    )
    tui_oidc_client_id: str = "astra-tui"
    tui_oidc_username: str = "astra-dev"
    tui_oidc_password: str = "development-only"
