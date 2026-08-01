from uuid import uuid4

import httpx
from astra_agent import create_app
from astra_memory import BoundedContextCompiler
from astra_model_providers import ModelMessage, ModelProviderError, OpenRouterModelProvider
from fastapi.testclient import TestClient


class FailingModelProvider:
    async def complete(self, messages: tuple[ModelMessage, ...]) -> str:
        raise ModelProviderError("failed")

    async def close(self) -> None:
        return None


def _headers(tenant_id: object) -> dict[str, str]:
    return {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(uuid4())}


def test_conversation_turn_is_durable_and_tenant_scoped() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id), "title": "Test conversation"},
        )
        assert created.status_code == 201
        conversation_id = created.json()["conversation"]["id"]
        turn = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"tenant_id": str(tenant_id), "content": "Hello agent"},
        )
        assert turn.status_code == 200
        assert turn.json()["assistant_message"]["content"] == (
            "Development model received: Hello agent"
        )
        loaded = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        assert [message["role"] for message in loaded.json()["messages"]] == [
            "user",
            "assistant",
        ]
        other_headers = _headers(uuid4())
        assert (
            client.get(
                f"/api/v1/conversations/{conversation_id}", headers=other_headers
            ).status_code
            == 404
        )


def test_user_message_survives_model_failure() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app(model_provider=FailingModelProvider())) as client:
        created = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        )
        conversation_id = created.json()["conversation"]["id"]
        response = client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            headers=headers,
            json={"tenant_id": str(tenant_id), "content": "Keep this"},
        )
        assert response.status_code == 502
        loaded = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        assert [message["content"] for message in loaded.json()["messages"]] == ["Keep this"]
        events = client.get(f"/api/v1/tenants/{tenant_id}/events", headers=headers).json()
        assert events[-1]["event_type"] == "model.failed"


async def test_context_compiler_enforces_budget() -> None:
    compiler = BoundedContextCompiler("persona " * 1_000, max_tokens=100)
    briefing = await compiler.compile(uuid4(), "objective " * 1_000)
    assert briefing.estimated_tokens <= 100
    assert len(briefing.content) <= 400


async def test_openrouter_adapter_sends_provider_neutral_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        payload = __import__("json").loads(request.content)
        assert payload == {
            "model": "test/model",
            "messages": [{"role": "user", "content": "hello"}],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": "reply"}}]})

    client = httpx.AsyncClient(
        base_url="https://openrouter.example",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer secret"},
    )
    provider = OpenRouterModelProvider("secret", "test/model", client=client)
    response = await provider.complete((ModelMessage(role="user", content="hello"),))
    assert response == "reply"
    await client.aclose()
