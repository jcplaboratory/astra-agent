from uuid import UUID, uuid4

import httpx
from astra_agent import create_app
from astra_domain import MemoryRecord, MemoryState
from astra_memory import MemoryContextCompiler, NullVectorIndex, QdrantVectorIndex
from astra_model_providers import ModelCompletion, ModelMessage, ToolDefinition
from astra_runtime import InMemoryRuntimeStore
from fastapi.testclient import TestClient


class CapturingModelProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[ModelMessage, ...]] = []

    async def complete(
        self, messages: tuple[ModelMessage, ...], _tools: tuple[ToolDefinition, ...]
    ) -> ModelCompletion:
        self.requests.append(messages)
        return ModelCompletion(content="captured")

    async def close(self) -> None:
        return None


class MaliciousVectorIndex(NullVectorIndex):
    def __init__(self, returned_id: UUID) -> None:
        self.returned_id = returned_id

    async def rank(
        self,
        tenant_id: UUID,
        visibility: str,
        allowed_ids: tuple[UUID, ...],
        vector: tuple[float, ...],
        limit: int,
    ) -> tuple[UUID, ...]:
        return (self.returned_id,)


def _headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(uuid4())}


def test_memory_is_extracted_recalled_deduplicated_and_deleted() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    model = CapturingModelProvider()
    with TestClient(create_app(model_provider=model)) as client:
        conversation = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        ).json()["conversation"]
        path = f"/api/v1/conversations/{conversation['id']}/messages"
        assert (
            client.post(
                path,
                headers=headers,
                json={
                    "tenant_id": str(tenant_id),
                    "content": "I prefer concise answers.",
                    "client_request_id": str(uuid4()),
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                path,
                headers=headers,
                json={
                    "tenant_id": str(tenant_id),
                    "content": "I prefer concise answers.",
                    "client_request_id": str(uuid4()),
                },
            ).status_code
            == 200
        )
        assert (
            client.post(
                path,
                headers=headers,
                json={
                    "tenant_id": str(tenant_id),
                    "content": "How should you respond?",
                    "client_request_id": str(uuid4()),
                },
            ).status_code
            == 200
        )
        assert "concise answers" in model.requests[-1][0].content

        memories = client.get(f"/api/v1/tenants/{tenant_id}/memories", headers=headers).json()[
            "memories"
        ]
        assert len(memories) == 1
        memory_id = memories[0]["id"]
        explanation = client.get(f"/api/v1/memories/{memory_id}", headers=headers)
        assert explanation.status_code == 200
        assert "Extracted from message" in explanation.json()["explanation"]
        assert client.delete(f"/api/v1/memories/{memory_id}", headers=headers).status_code == 200
        assert (
            client.get(f"/api/v1/tenants/{tenant_id}/memories", headers=headers).json()["memories"]
            == []
        )


def test_candidate_memory_is_inspectable_but_not_recalled() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    model = CapturingModelProvider()
    with TestClient(create_app(model_provider=model)) as client:
        conversation_id = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        ).json()["conversation"]["id"]
        path = f"/api/v1/conversations/{conversation_id}/messages"
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I use a private experimental tool.",
                "client_request_id": str(uuid4()),
            },
        )
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "What tools do I use?",
                "client_request_id": str(uuid4()),
            },
        )
        assert "experimental tool" not in model.requests[-1][0].content
        memories = client.get(f"/api/v1/tenants/{tenant_id}/memories", headers=headers).json()[
            "memories"
        ]
        assert memories[0]["state"] == "candidate"


def test_candidate_can_be_promoted_or_rejected() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app()) as client:
        conversation_id = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        ).json()["conversation"]["id"]
        path = f"/api/v1/conversations/{conversation_id}/messages"
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I use candidate one.",
                "client_request_id": str(uuid4()),
            },
        )
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I use candidate two.",
                "client_request_id": str(uuid4()),
            },
        )
        memories = client.get(f"/api/v1/tenants/{tenant_id}/memories", headers=headers).json()[
            "memories"
        ]
        promoted = client.post(
            f"/api/v1/memories/{memories[0]['id']}/review",
            headers=headers,
            json={"tenant_id": str(tenant_id), "promote": True},
        )
        rejected = client.post(
            f"/api/v1/memories/{memories[1]['id']}/review",
            headers=headers,
            json={"tenant_id": str(tenant_id), "promote": False},
        )
        assert promoted.json()["state"] == "promoted"
        assert promoted.json()["reviewed_by"] is not None
        assert rejected.json()["state"] == "rejected"
        assert (
            client.post(
                f"/api/v1/memories/{memories[0]['id']}/review",
                headers=headers,
                json={"tenant_id": str(tenant_id), "promote": True},
            ).status_code
            == 409
        )


def test_candidate_can_replace_promoted_memory() -> None:
    tenant_id = uuid4()
    headers = _headers(tenant_id)
    with TestClient(create_app()) as client:
        conversation_id = client.post(
            "/api/v1/conversations",
            headers=headers,
            json={"tenant_id": str(tenant_id)},
        ).json()["conversation"]["id"]
        path = f"/api/v1/conversations/{conversation_id}/messages"
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I prefer old preference.",
                "client_request_id": str(uuid4()),
            },
        )
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I use new preference.",
                "client_request_id": str(uuid4()),
            },
        )
        memories = client.get(f"/api/v1/tenants/{tenant_id}/memories", headers=headers).json()[
            "memories"
        ]
        old = next(item for item in memories if item["state"] == "promoted")
        new = next(item for item in memories if item["state"] == "candidate")
        reviewed = client.post(
            f"/api/v1/memories/{new['id']}/review",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "promote": True,
                "replaces_memory_id": old["id"],
            },
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["contradiction_of"] == old["id"]
        remaining = client.get(f"/api/v1/tenants/{tenant_id}/memories", headers=headers).json()[
            "memories"
        ]
        assert [item["id"] for item in remaining] == [new["id"]]


async def test_vector_results_are_intersected_with_authorized_mariadb_records() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    foreign_memory = MemoryRecord(
        tenant_id=uuid4(),
        kind="fact",
        content="foreign secret",
        normalized_content="foreign secret",
        source_event_id=uuid4(),
        source_message_id=uuid4(),
        confidence=1,
        confirmed=True,
        state=MemoryState.PROMOTED,
    )
    compiler = MemoryContextCompiler(
        store, MaliciousVectorIndex(foreign_memory.id), "safe persona", max_tokens=200
    )
    briefing = await compiler.compile(tenant_id, "secret")
    assert "foreign secret" not in briefing.content
    assert briefing.source_memory_ids == ()


async def test_qdrant_query_contains_tenant_visibility_and_authorized_ids() -> None:
    tenant_id = uuid4()
    allowed_id = uuid4()
    unauthorized_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content)
        must = payload["filter"]["must"]
        assert {"key": "tenant_id", "match": {"value": str(tenant_id)}} in must
        assert {"key": "visibility", "match": {"value": "private"}} in must
        assert {"has_id": [str(allowed_id)]} in must
        return httpx.Response(
            200,
            json={"result": {"points": [{"id": str(unauthorized_id)}, {"id": str(allowed_id)}]}},
        )

    client = httpx.AsyncClient(
        base_url="http://qdrant.test", transport=httpx.MockTransport(handler)
    )
    index = QdrantVectorIndex("http://qdrant.test", "memories", client=client)
    ranked = await index.rank(tenant_id, "private", (allowed_id,), (0.1, 0.2), 5)
    assert ranked == (allowed_id,)
    await client.aclose()


async def test_qdrant_collection_is_created_when_missing() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        payload = __import__("json").loads(request.content)
        assert payload == {"vectors": {"size": 32, "distance": "Cosine"}}
        return httpx.Response(200, json={"result": True})

    client = httpx.AsyncClient(
        base_url="http://qdrant.test", transport=httpx.MockTransport(handler)
    )
    index = QdrantVectorIndex("http://qdrant.test", "memories", client=client)
    await index.ensure_ready()
    assert [request.method for request in requests] == ["GET", "PUT"]
    await client.aclose()
