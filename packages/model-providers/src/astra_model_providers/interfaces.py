from collections.abc import AsyncIterator
from typing import Any, Protocol, overload

from pydantic import BaseModel, ConfigDict


class ModelMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    role: str
    content: str


class ToolDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: str
    name: str
    arguments: dict[str, Any]


class ModelCompletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


class ModelStreamEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: str
    content: str = ""
    tool_call: ToolCall | None = None


class PlannerTask(BaseModel):
    """One bounded, read-only ARA operation proposed by the main model."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    objective: str
    context: str = ""
    required_capabilities: tuple[dict[str, str], ...]
    deliverable_contract: str


class PlannerDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tasks: tuple[PlannerTask, ...] = ()


class MainModelProvider(Protocol):
    async def plan(
        self, messages: tuple[ModelMessage, ...], max_siblings: int
    ) -> PlannerDecision: ...
    @overload
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str: ...

    @overload
    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion: ...

    def stream(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...] = ()
    ) -> AsyncIterator[ModelStreamEvent]: ...

    async def close(self) -> None: ...


class LocalModelProvider(Protocol):
    async def process(self, instruction: str, content: str) -> str: ...
