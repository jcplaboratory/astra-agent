from typing import Protocol

from pydantic import BaseModel, ConfigDict


class ModelMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    role: str
    content: str


class MainModelProvider(Protocol):
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str: ...

    async def close(self) -> None: ...


class LocalModelProvider(Protocol):
    async def process(self, instruction: str, content: str) -> str: ...
