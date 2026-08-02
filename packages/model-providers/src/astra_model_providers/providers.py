import json
from typing import Any, overload

import httpx

from astra_model_providers.interfaces import (
    ModelCompletion,
    ModelMessage,
    PlannerDecision,
    PlannerTask,
    ToolCall,
    ToolDefinition,
)


class ModelProviderError(Exception):
    pass


class DevelopmentModelProvider:
    def __init__(self, planner_decision: PlannerDecision | None = None) -> None:
        self._planner_decision = planner_decision

    async def plan(self, messages: tuple[ModelMessage, ...], max_siblings: int) -> PlannerDecision:
        if self._planner_decision is not None:
            return self._planner_decision
        request = next((item.content for item in reversed(messages) if item.role == "user"), "")
        if "inspect" not in request.lower() and "repository" not in request.lower():
            return PlannerDecision()
        return PlannerDecision(
            tasks=(
                PlannerTask(
                    objective=request,
                    required_capabilities=({"kind": "file.read", "scope": "repository"},),
                    deliverable_contract=(
                        "Return bounded repository findings with file and line evidence."
                    ),
                ),
            )
        )

    @overload
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str: ...

    @overload
    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion: ...

    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...] | None = None
    ) -> str | ModelCompletion:
        ara_findings = next(
            (
                message.content.split("\n\n", 1)[-1]
                for message in reversed(messages)
                if message.role == "system" and message.content.startswith("ARA findings follow")
            ),
            None,
        )
        if ara_findings:
            content = f"Repository inspection completed:\n\n{ara_findings}"
            return ModelCompletion(content=content) if tools is not None else content
        pending = next(
            (
                message.content
                for message in reversed(messages)
                if message.role == "system" and message.content.startswith("Repository task")
            ),
            None,
        )
        if pending:
            return ModelCompletion(content=pending) if tools is not None else pending
        user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"), ""
        )
        content = f"Development model received: {user_message}"
        return ModelCompletion(content=content) if tools is not None else content

    async def close(self) -> None:
        return None


class OpenRouterModelProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        self._owns_client = client is None

    @overload
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str: ...

    @overload
    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion: ...

    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...] | None = None
    ) -> str | ModelCompletion:
        try:
            request: dict[str, Any] = {
                "model": self.model,
                "messages": [message.model_dump() for message in messages],
            }
            if tools is not None:
                request["tools"] = [
                    {
                        "type": "function",
                        "function": tool.model_dump(),
                    }
                    for tool in tools
                ]
            response = await self._client.post(
                "/chat/completions",
                json=request,
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            message = payload["choices"][0]["message"]
            content = message.get("content")
            if tools is None:
                if not isinstance(content, str) or not content:
                    raise ValueError("model returned empty content")
                return content
            if content is not None and not isinstance(content, str):
                raise ValueError("model returned invalid content")
            tool_calls = tuple(
                ToolCall(
                    id=call["id"],
                    name=call["function"]["name"],
                    arguments=json.loads(call["function"]["arguments"]),
                )
                for call in message.get("tool_calls", [])
            )
            if content is None and not tool_calls:
                raise ValueError("model returned empty content")
            return ModelCompletion(content=content, tool_calls=tool_calls)
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise ModelProviderError("OpenRouter request failed") from error

    async def plan(self, messages: tuple[ModelMessage, ...], max_siblings: int) -> PlannerDecision:
        schema = PlannerDecision.model_json_schema()
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only the planner_decision function call. Propose at most "
                        f"{max_siblings} independent read-only repository inspections."
                    ),
                },
                *(message.model_dump() for message in messages),
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "planner_decision",
                        "description": "Return a bounded ARA delegation plan.",
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "planner_decision"}},
        }
        try:
            response = await self._client.post("/chat/completions", json=request)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            calls = payload["choices"][0]["message"]["tool_calls"]
            if not isinstance(calls, list) or len(calls) != 1:
                raise ValueError("planner returned an invalid function call count")
            call = calls[0]
            if call["function"]["name"] != "planner_decision":
                raise ValueError("planner returned an unexpected function")
            arguments = json.loads(call["function"]["arguments"])
            return PlannerDecision.model_validate(arguments)
        except (
            httpx.HTTPError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as error:
            raise ModelProviderError("OpenRouter planner request failed") from error

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class OpenAICompatibleLocalModelProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), headers=headers, timeout=30
        )
        self._owns_client = client is None
        self._model = model

    async def process(self, instruction: str, content: str) -> str:
        response = await self._client.post(
            "/chat/completions",
            json={
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": content},
                ],
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        result = payload["choices"][0]["message"]["content"]
        if not isinstance(result, str):
            raise ValueError("local model returned invalid content")
        return result

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
