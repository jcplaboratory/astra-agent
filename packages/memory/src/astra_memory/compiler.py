from typing import Protocol
from uuid import UUID

from astra_model_providers import LocalModelProvider
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


class LocalModelContextCompressor:
    """Optionally compress a deterministic briefing without relaxing its budget."""

    def __init__(
        self,
        fallback: ContextCompiler,
        local_model: LocalModelProvider,
        max_tokens: int,
    ) -> None:
        self._fallback = fallback
        self._local_model = local_model
        self._max_tokens = max_tokens

    async def compile(self, tenant_id: UUID, objective: str) -> ContextBriefing:
        briefing = await self._fallback.compile(tenant_id, objective)
        try:
            compressed = await self._local_model.process(
                "Compress this context for a language model. Preserve persona boundaries, "
                "relevant facts, and the current objective. Return context only.",
                briefing.content,
            )
            if not compressed.strip():
                raise ValueError("local model returned empty context")
        except Exception:
            return briefing
        content = self._bound(compressed)
        return ContextBriefing(
            tenant_id=tenant_id,
            content=content,
            source_memory_ids=briefing.source_memory_ids,
            estimated_tokens=(len(content) + 3) // 4,
        )

    def _bound(self, content: str) -> str:
        maximum = self._max_tokens * 4
        content = content.strip()
        if len(content) > maximum:
            return content[: maximum - 3].rstrip() + "..."
        return content
