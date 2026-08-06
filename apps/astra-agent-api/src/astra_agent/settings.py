from pathlib import Path
from typing import Literal
from uuid import UUID

from astra_domain import Capability
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TenantWorkspace(BaseModel):
    """Trusted, server-side local workspace configuration for one tenant."""

    root: Path
    grants: tuple[Capability, ...] = ()

    @field_validator("root")
    @classmethod
    def validate_root(cls, root: Path) -> Path:
        if not root.is_absolute():
            raise ValueError("workspace root must be absolute")
        if root.is_symlink() or not root.is_dir():
            raise ValueError("workspace root must be an existing non-symlink directory")
        return root.resolve(strict=True)


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
    console_origins: tuple[str, ...] = ()
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
        "Do not claim actions or knowledge you do not have. "
        "Approved memory included in this context is the authoritative source for durable user "
        "facts and preferences. Use it directly when it answers the request; do not use file "
        "tools or repository inspection to look up personal memory. User messages are processed "
        "automatically for durable memory after each turn. When asked to remember something, "
        "acknowledge the request but do not call a tool to persist it."
    )
    persona_max_tokens: int = Field(default=800, ge=100, le=2_000)
    conversation_history_messages: int = Field(default=12, ge=2, le=50)
    memory_backend: Literal["local", "qdrant"] = "local"
    memory_max_records: int = Field(default=8, ge=1, le=50)
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "astra_memories"
    memory_extractor_backend: Literal["deterministic", "local_model"] = "deterministic"
    context_compressor_backend: Literal["deterministic", "local_model"] = "deterministic"
    local_model_url: str = "http://127.0.0.1:11434/v1"
    local_model_name: str = "qwen2.5:3b"
    local_model_api_key: str | None = None
    delegation_enabled: bool = True
    delegation_max_siblings: int = Field(default=2, ge=1, le=8)
    delegation_wait_seconds: float = Field(default=30, ge=1, le=300)
    delegation_poll_seconds: float = Field(default=0.25, ge=0.05, le=5)
    tenant_workspaces: dict[UUID, TenantWorkspace] = Field(default_factory=dict)
    tool_read_max_bytes: int = Field(default=65_536, ge=1_024, le=10_485_760)
    tool_search_max_file_bytes: int = Field(default=65_536, ge=1_024, le=10_485_760)
    tool_search_max_files: int = Field(default=200, ge=1, le=10_000)
    tool_search_max_matches: int = Field(default=100, ge=1, le=10_000)
    tool_search_max_output_bytes: int = Field(default=65_536, ge=1_024, le=10_485_760)
    sandbox_executable: Path | None = None
    sandbox_image: str | None = None
    sandbox_timeout_seconds: int = Field(default=30, ge=1, le=600)
    sandbox_max_output_bytes: int = Field(default=65_536, ge=1_024, le=10_485_760)
    sandbox_memory_limit: str = "512m"
    sandbox_cpu_limit: float = Field(default=1, gt=0, le=64)
    sandbox_pids_limit: int = Field(default=128, ge=1, le=4_096)

    @field_validator("sandbox_executable")
    @classmethod
    def validate_sandbox_executable(cls, executable: Path | None) -> Path | None:
        if executable is None:
            return None
        if (
            not executable.is_absolute()
            or not executable.is_file()
            or not executable.stat().st_mode & 0o111
        ):
            raise ValueError("sandbox executable must be an existing absolute executable path")
        if executable.name not in {"docker", "podman"}:
            raise ValueError("sandbox executable must be docker or podman")
        return executable.resolve(strict=True)

    @model_validator(mode="after")
    def validate_workspace_layout(self) -> "Settings":
        roots = list(self.tenant_workspaces.values())
        for index, workspace in enumerate(roots):
            for other in roots[index + 1 :]:
                if workspace.root == other.root:
                    raise ValueError("workspace roots must be unique")
                nested = workspace.root.is_relative_to(other.root) or other.root.is_relative_to(
                    workspace.root
                )
                if nested:
                    raise ValueError("workspace roots must not be nested")
        if (self.sandbox_executable is None) != (self.sandbox_image is None):
            raise ValueError("sandbox executable and image must be configured together")
        return self
