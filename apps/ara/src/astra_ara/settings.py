from pathlib import Path
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ARASettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTRA_ARA_", extra="ignore")

    agent_url: str = "http://127.0.0.1:8000"
    tenant_id: UUID
    ara_id: UUID = Field(validation_alias="ASTRA_ARA_ID")
    name: str = "repository-ara"
    repository_root: Path
    poll_seconds: float = Field(default=1, ge=0.1, le=60)
    once: bool = False
    client_certificate: Path | None = None
    client_key: Path | None = None
    ca_certificate: Path | None = None
