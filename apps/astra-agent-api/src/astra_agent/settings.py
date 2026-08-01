from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTRA_", env_file=".env", extra="ignore")

    environment: str = "development"
    persistence_backend: Literal["memory", "mariadb"] = "memory"
    database_url: str | None = None
    create_schema_on_startup: bool = False
    lease_duration_seconds: int = Field(default=60, ge=10, le=900)
    auth_backend: Literal["development_headers", "mtls"] = "development_headers"
    trusted_proxy_secret: str | None = None
    user_auth_backend: Literal["development_headers", "oidc"] = "development_headers"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_tenant_claim: str = "tenant_id"
    oidc_jwks_cache_seconds: int = Field(default=300, ge=30, le=86_400)
    artifact_bucket: str | None = None
    artifact_endpoint_url: str | None = None
    artifact_access_key: str | None = None
    artifact_secret_key: str | None = None
    artifact_region: str = "us-east-1"
    model_backend: Literal["development", "openrouter"] = "development"
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-4.1-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    persona_kernel: str = (
        "Be direct, accurate, and transparent. Preserve user control. "
        "Do not claim actions or knowledge you do not have."
    )
    persona_max_tokens: int = Field(default=800, ge=100, le=2_000)
    conversation_history_messages: int = Field(default=12, ge=2, le=50)
    memory_backend: Literal["local", "qdrant"] = "local"
    memory_max_records: int = Field(default=8, ge=1, le=50)
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "astra_memories"
    memory_extractor_backend: Literal["deterministic", "local_model"] = "deterministic"
    local_model_url: str = "http://127.0.0.1:11434/v1"
    local_model_name: str = "qwen2.5:3b"
    local_model_api_key: str | None = None
    delegation_enabled: bool = True
    delegation_wait_seconds: float = Field(default=30, ge=1, le=300)
    delegation_poll_seconds: float = Field(default=0.25, ge=0.05, le=5)
