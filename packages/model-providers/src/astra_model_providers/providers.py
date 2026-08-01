from typing import Any

import httpx

from astra_model_providers.interfaces import ModelMessage


class ModelProviderError(Exception):
    pass


class DevelopmentModelProvider:
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str:
        ara_findings = next(
            (
                message.content.split("\n\n", 1)[-1]
                for message in reversed(messages)
                if message.role == "system" and message.content.startswith("ARA findings follow")
            ),
            None,
        )
        if ara_findings:
            return f"Repository inspection completed:\n\n{ara_findings}"
        pending = next(
            (
                message.content
                for message in reversed(messages)
                if message.role == "system" and message.content.startswith("Repository task")
            ),
            None,
        )
        if pending:
            return pending
        user_message = next(
            (message.content for message in reversed(messages) if message.role == "user"), ""
        )
        return f"Development model received: {user_message}"

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

    async def complete(self, messages: tuple[ModelMessage, ...]) -> str:
        try:
            response = await self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [message.model_dump() for message in messages],
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content:
                raise ValueError("model returned empty content")
            return content
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise ModelProviderError("OpenRouter request failed") from error

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
