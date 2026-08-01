from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContextBriefing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    content: str
    source_memory_ids: tuple[UUID, ...] = ()
    estimated_tokens: int = Field(ge=0)


class ContextCompiler(Protocol):
    async def compile(self, tenant_id: UUID, objective: str) -> ContextBriefing: ...


class BoundedContextCompiler:
    def __init__(self, persona_kernel: str, max_tokens: int = 800) -> None:
        self._persona_kernel = persona_kernel.strip()
        self._max_tokens = max_tokens

    async def compile(self, tenant_id: UUID, objective: str) -> ContextBriefing:
        content = f"Persona:\n{self._persona_kernel}\n\nCurrent objective:\n{objective.strip()}"
        max_characters = self._max_tokens * 4
        if len(content) > max_characters:
            content = content[: max_characters - 3].rstrip() + "..."
        return ContextBriefing(
            tenant_id=tenant_id,
            content=content,
            estimated_tokens=(len(content) + 3) // 4,
        )
