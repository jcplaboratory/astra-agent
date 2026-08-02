import asyncio
from uuid import UUID, uuid4

import httpx
from astra_agent import create_app
from astra_memory import BoundedContextCompiler, LocalModelContextCompressor
from astra_model_providers import (
    ModelCompletion,
    ModelMessage,
    ModelProviderError,
    OpenRouterModelProvider,
    ToolDefinition,
)
from fastapi.testclient import TestClient


class FailingModelProvider:
    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion:
        raise ModelProviderError("failed")

    async def close(self) -> None:
        return None


class IdentityAwareModelProvider:
    def __init__(self) -> None:
        self.system_message = ""

    async def complete(
        self, messages: tuple[ModelMessage, ...], tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion:
        self.system_message = messages[0].content
        assert tools
        if "Identity: You are Astra" in self.system_message and "provider" in self.system_message:
            return ModelCompletion(content="I am Astra, your personal agent.")
        return ModelCompletion(content="I am Claude.")

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
            json={
                "tenant_id": str(tenant_id),
                "client_request_id": str(uuid4()),
                "content": "Hello agent",
            },
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


def test_conversation_message_streams_and_persists_response() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id), "title": "Stream test"},
        )
        conversation_id = created.json()["conversation"]["id"]
        with client.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "client_request_id": str(uuid4()),
                "content": "Stream this",
            },
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
        assert "event: content" in body
        assert "Development model received: Stream this" in body
        assert "event: done" in body
        loaded = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        assert loaded.json()["messages"][-1]["content"] == "Development model received: Stream this"


def test_stream_claims_its_own_turn_when_another_turn_is_pending() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        )
        conversation_id = created.json()["conversation"]["id"]
        asyncio.run(
            client.app.state.orchestrator.start_turn(
                tenant_id,
                UUID(headers["X-Astra-User-ID"]),
                UUID(conversation_id),
                uuid4(),
                "Pending turn",
            )
        )
        with client.stream(
            "POST",
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "client_request_id": str(uuid4()),
                "content": "Current turn",
            },
        ) as response:
            assert response.status_code == 200


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
            json={
                "tenant_id": str(tenant_id),
                "client_request_id": str(uuid4()),
                "content": "Keep this",
            },
        )
        assert response.status_code == 502
        loaded = client.get(f"/api/v1/conversations/{conversation_id}", headers=headers)
        assert [message["content"] for message in loaded.json()["messages"]] == ["Keep this"]
        events = client.get(f"/api/v1/tenants/{tenant_id}/events", headers=headers).json()
        assert events[-1]["event_type"] == "model.failed"


def test_model_receives_astra_identity_and_does_not_claim_provider_name() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    provider = IdentityAwareModelProvider()
    with TestClient(create_app(model_provider=provider)) as client:
        persona = client.put(
            f"/api/v1/tenants/{tenant_id}/persona",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "authored_core": {
                    "identity": "You are Astra, a trusted personal agent.",
                    "values": "Help users",
                    "boundaries": "Be honest",
                    "tone": "Direct",
                    "initiative": "Offer next steps",
                    "emotional_range": "Measured",
                    "disagreement": "Be respectful",
                },
            },
        )
        assert persona.status_code == 200
        conversation = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        ).json()["conversation"]
        response = client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "client_request_id": str(uuid4()),
                "content": "Are you Claude?",
            },
        )

    assert response.status_code == 200
    assert response.json()["assistant_message"]["content"] == "I am Astra, your personal agent."
    assert provider.system_message.startswith("Identity: You are Astra, a trusted personal agent.")
    assert "Never claim to be Claude, Anthropic, OpenAI" in provider.system_message


async def test_context_compiler_enforces_budget() -> None:
    compiler = BoundedContextCompiler("persona " * 1_000, max_tokens=100)
    briefing = await compiler.compile(uuid4(), "objective " * 1_000)
    assert briefing.content.startswith("Identity: You are Astra.")
    assert "underlying model or provider" in briefing.content
    assert briefing.estimated_tokens <= 100
    assert len(briefing.content) <= 400


class ContextLocalModel:
    def __init__(self, response: str | Exception) -> None:
        self.response = response

    async def process(self, instruction: str, content: str) -> str:
        assert "Compress" in instruction
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def test_local_context_compression_is_bounded_and_falls_back() -> None:
    fallback = BoundedContextCompiler("persona", max_tokens=50)
    compressed = LocalModelContextCompressor(fallback, ContextLocalModel("x" * 500), 50)
    briefing = await compressed.compile(uuid4(), "objective")
    assert briefing.content.startswith("Identity: You are Astra.")
    assert "underlying model or provider" in briefing.content
    assert briefing.content.endswith("...")
    assert briefing.estimated_tokens <= 50

    unavailable = LocalModelContextCompressor(fallback, ContextLocalModel(RuntimeError()), 50)
    tenant_id = uuid4()
    assert await unavailable.compile(tenant_id, "objective") == await fallback.compile(
        tenant_id, "objective"
    )


async def test_openrouter_adapter_sends_provider_neutral_messages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        payload = __import__("json").loads(request.content)
        assert payload == {
            "model": "test/model",
            "messages": [{"role": "user", "content": "hello"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": "reply"}}]})

    client = httpx.AsyncClient(
        base_url="https://openrouter.example",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer secret"},
    )
    provider = OpenRouterModelProvider("secret", "test/model", client=client)
    response = await provider.complete(
        (ModelMessage(role="user", content="hello"),),
        (
            ToolDefinition(
                name="read_file", description="Read a file", parameters={"type": "object"}
            ),
        ),
    )
    assert response == ModelCompletion(content="reply")
    await client.aclose()
