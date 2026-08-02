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


class MainModelProvider(Protocol):
    @overload
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str: ...

    @overload
    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion: ...

    async def close(self) -> None: ...


class LocalModelProvider(Protocol):
    async def process(self, instruction: str, content: str) -> str: ...
