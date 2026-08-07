import time
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


async def test_pinned_promoted_memory_precedes_ranked_recall() -> None:
    tenant_id = uuid4()
    store = InMemoryRuntimeStore()
    pinned = MemoryRecord(
        tenant_id=tenant_id,
        kind="fact",
        content="Always use concise answers",
        normalized_content="always use concise answers",
        source_event_id=uuid4(),
        source_message_id=uuid4(),
        confidence=1,
        confirmed=True,
        pinned=True,
        state=MemoryState.PROMOTED,
    )
    relevant = pinned.model_copy(
        update={
            "id": uuid4(),
            "content": "The project is called Aurora",
            "normalized_content": "the project is called aurora",
            "pinned": False,
        }
    )
    await store.upsert_memory(pinned, ())
    await store.upsert_memory(relevant, ())

    compiler = MemoryContextCompiler(store, NullVectorIndex(), "persona", memory_limit=2)
    briefing = await compiler.compile(tenant_id, "What is the Aurora project?")

    assert briefing.content.index(pinned.content) < briefing.content.index(relevant.content)


def _headers(tenant_id: UUID) -> dict[str, str]:
    return {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(uuid4())}


def _wait_for_jobs(client: TestClient, tenant_id: UUID, headers: dict[str, str]) -> None:
    for _ in range(50):
        jobs = client.get(f"/api/v1/tenants/{tenant_id}/jobs", headers=headers).json()
        if jobs and all(item["state"] in {"completed", "failed"} for item in jobs):
            return
        time.sleep(0.02)
    raise AssertionError("background jobs did not finish")


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
        _wait_for_jobs(client, tenant_id, headers)
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
        _wait_for_jobs(client, tenant_id, headers)
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
        _wait_for_jobs(client, tenant_id, headers)
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I use candidate two.",
                "client_request_id": str(uuid4()),
            },
        )
        _wait_for_jobs(client, tenant_id, headers)
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
        _wait_for_jobs(client, tenant_id, headers)
        client.post(
            path,
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "content": "I use new preference.",
                "client_request_id": str(uuid4()),
            },
        )
        _wait_for_jobs(client, tenant_id, headers)
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


async def test_memory_ranking_audit_identifies_lexical_fallback() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    foreign_id = uuid4()
    compiler = MemoryContextCompiler(
        InMemoryRuntimeStore(),
        MaliciousVectorIndex(foreign_id),
        "safe persona",
        audit=lambda stage, payload: events.append((stage, payload)),
    )

    await compiler.compile(uuid4(), "secret")

    assert events == [
        (
            "memory.ranking",
            {
                "objective": "secret",
                "strategy": "lexical",
                "qdrant_available": True,
                "authorized_memory_ids": [],
                "qdrant_ranked_memory_ids": [str(foreign_id)],
                "selected_memory_ids": [],
                "pinned_memory_ids": [],
            },
        )
    ]


async def test_lexical_ranking_recalls_nightly_plants_memory_despite_qdrant_order() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    plants = MemoryRecord(
        tenant_id=tenant_id,
        kind="fact",
        content="Water the plants every night",
        normalized_content="water the plants every night",
        source_event_id=uuid4(),
        source_message_id=uuid4(),
        confidence=1,
        confirmed=True,
        state=MemoryState.PROMOTED,
    )
    unrelated = MemoryRecord(
        tenant_id=tenant_id,
        kind="fact",
        content="Prefers concise answers",
        normalized_content="prefers concise answers",
        source_event_id=uuid4(),
        source_message_id=uuid4(),
        confidence=1,
        confirmed=True,
        state=MemoryState.PROMOTED,
    )
    await store.upsert_memory(plants, ())
    await store.upsert_memory(unrelated, ())
    compiler = MemoryContextCompiler(
        store, MaliciousVectorIndex(unrelated.id), "safe persona", memory_limit=1
    )

    briefing = await compiler.compile(tenant_id, "What do I like to do every night?")

    assert briefing.source_memory_ids == (plants.id,)
    assert "Water the plants every night" in briefing.content


async def test_memory_context_instructs_model_to_use_approved_memory_directly() -> None:
    briefing = await MemoryContextCompiler(
        InMemoryRuntimeStore(),
        NullVectorIndex(),
        "Approved memory is authoritative. Do not use file tools for personal memory.",
    ).compile(uuid4(), "What do I like to do every night?")

    assert "Approved memory is authoritative" in briefing.content
    assert "Do not use file tools" in briefing.content


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
