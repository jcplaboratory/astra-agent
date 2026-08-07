from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator
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


class HostARASettings(BaseSettings):
    """Explicitly configured privileged host-command worker."""

    model_config = SettingsConfigDict(
        env_prefix="ASTRA_HOST_ARA_", extra="ignore", populate_by_name=True
    )

    agent_url: str = "http://127.0.0.1:8000"
    tenant_id: UUID
    ara_id: UUID = Field(validation_alias="ASTRA_HOST_ARA_ID")
    name: str = "host-ara"
    enabled: bool = False
    execution_mode: Literal[
        "approval_required", "autonomous_allowlist", "unrestricted_autonomous"
    ] = "approval_required"
    confirm_unrestricted_autonomy: bool = False
    command_allowlist: tuple[Path, ...] = ()
    working_directory_allowlist: tuple[Path, ...] = ()
    command_timeout_seconds: int = Field(default=120, ge=1, le=3600)
    command_max_output_bytes: int = Field(default=65_536, ge=1_024, le=10_485_760)
    poll_seconds: float = Field(default=1, ge=0.1, le=60)
    once: bool = False
    client_certificate: Path | None = None
    client_key: Path | None = None
    ca_certificate: Path | None = None

    @model_validator(mode="after")
    def validate_privileged_mode(self) -> "HostARASettings":
        if not self.enabled:
            raise ValueError("ASTRA_HOST_ARA_ENABLED=true is required")
        if (
            self.execution_mode == "unrestricted_autonomous"
            and not self.confirm_unrestricted_autonomy
        ):
            raise ValueError(
                "ASTRA_HOST_ARA_CONFIRM_UNRESTRICTED_AUTONOMY=true is required"
            )
        if self.execution_mode == "autonomous_allowlist" and not self.command_allowlist:
            raise ValueError("ASTRA_HOST_ARA_COMMAND_ALLOWLIST is required for allowlist mode")
        return self
