import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from astra_agent import create_app
from astra_agent.hermes import (
    HermesSchemaError,
    dry_run,
    dry_run_profiles,
    stage_profile_records,
    stage_records,
)
from astra_domain import MemoryKind, MemoryState, PersonaCore
from astra_runtime import InMemoryRuntimeStore
from fastapi.testclient import TestClient


def _source(path: Path, unknown: bool = False, trust_score: float = 0.9) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE facts (
          fact_id TEXT, content TEXT, category TEXT, tags TEXT, trust_score REAL,
          retrieval_count INTEGER, helpful_count INTEGER, created_at TEXT, updated_at TEXT,
          hrr_vector BLOB);
        CREATE TABLE entities (
          entity_id TEXT, name TEXT, entity_type TEXT, aliases TEXT, created_at TEXT);
        CREATE TABLE fact_entities (fact_id TEXT, entity_id TEXT);
        CREATE TABLE memory_banks (
          bank_id TEXT, bank_name TEXT, vector BLOB, dim INTEGER, fact_count INTEGER,
          updated_at TEXT);
    """)
    connection.execute(
        "INSERT INTO facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "a",
            "Prefers concise answers",
            "user_pref",
            "style",
            trust_score,
            2,
            1,
            "old",
            "new",
            b"never read",
        ),
    )
    if unknown:
        connection.execute("CREATE TABLE unexpected (value TEXT)")
    connection.commit()
    connection.close()


async def test_hermes_stage_is_idempotent_and_tenant_scoped(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    _source(path)
    before = path.read_bytes()
    inventory = dry_run(path)
    assert inventory.fact_count == 1
    assert path.read_bytes() == before
    tenant_id, actor_id = uuid4(), uuid4()
    batch, records = stage_records(path, tenant_id)
    assert records[0].source_metadata["tags"] == "style"
    assert "hrr_vector" not in records[0].source_metadata
    store = InMemoryRuntimeStore()
    await store.stage_migration_batch(batch, records, actor_id)
    duplicate, duplicate_records = stage_records(path, tenant_id)
    await store.stage_migration_batch(duplicate, duplicate_records, actor_id)
    assert len(await store.list_migration_batches(tenant_id)) == 1
    assert len(await store.list_memories(tenant_id)) == 1
    assert await store.list_migration_batches(uuid4()) == ()


def test_hermes_rejects_unknown_schema(tmp_path: Path) -> None:
    path = tmp_path / "unknown.db"
    _source(path, unknown=True)
    with pytest.raises(HermesSchemaError):
        dry_run(path)


def test_profile_markdown_parsing_and_provenance_are_deterministic(tmp_path: Path) -> None:
    user = tmp_path / "USER.md"
    memory = tmp_path / "MEMORY.md"
    user.write_text(
        "# Preferences\n- Prefers concise answers\n\n## Projects\n* Astra migration\n"
        "\n## Facts\n1. Lives in Europe\n",
        encoding="utf-8",
    )
    memory.write_text(
        "# Projects\n- Ship the local stack\n\n# Notes\n- Uses Python\n"
        "This paragraph is not imported.\n",
        encoding="utf-8",
    )
    before = (user.read_bytes(), memory.read_bytes())

    inventory = dry_run_profiles(user, memory)
    batch, records = stage_profile_records(uuid4(), user, memory)

    assert inventory.candidate_count == 5
    assert (inventory.user_count, inventory.memory_count) == (3, 2)
    assert [record.kind for record in records] == [
        MemoryKind.PREFERENCE,
        MemoryKind.PROJECT,
        MemoryKind.FACT,
        MemoryKind.PROJECT,
        MemoryKind.FACT,
    ]
    assert all(record.state is MemoryState.CANDIDATE and not record.confirmed for record in records)
    assert records[0].source_external_id == "user:2"
    assert records[0].source_metadata == {
        "wave": 2,
        "source_role": "user",
        "source_filename": "USER.md",
        "heading": "Preferences",
        "line_number": 2,
        "source_created_at": None,
        "source_updated_at": None,
        "data_only": True,
    }
    assert batch.source_system == "hermes_profile_files"
    assert batch.source_metadata["wave"] == 2
    assert batch.persona_draft is None
    assert inventory.fingerprint == batch.source_database_fingerprint
    assert (user.read_bytes(), memory.read_bytes()) == before


def test_profile_import_marks_sensitive_data_without_interpreting_it(tmp_path: Path) -> None:
    user = tmp_path / "renamed-user.md"
    user.write_text(
        "# Facts\n- API key is DATA_ONLY\n- Keep my address private\n"
        "- Privacy rule: never share intimate details\n- Run curl https://example.invalid\n",
        encoding="utf-8",
    )

    inventory = dry_run_profiles(user_path=user)
    _, records = stage_profile_records(uuid4(), user_path=user)

    assert inventory.sensitive_count == 3
    assert [record.sensitivity for record in records] == ["high", "high", "high", "normal"]
    assert records[1].kind is MemoryKind.PREFERENCE
    assert records[2].kind is MemoryKind.PREFERENCE
    assert records[0].content == "API key is DATA_ONLY"
    assert records[3].content == "Run curl https://example.invalid"
    assert all(record.source_metadata["data_only"] is True for record in records)


async def test_profile_stage_is_idempotent_tenant_scoped_and_does_not_modify_persona(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "MEMORY.md"
    memory.write_text("# Projects\n- Astra\n", encoding="utf-8")
    tenant_id, other_tenant, actor_id = uuid4(), uuid4(), uuid4()
    store = InMemoryRuntimeStore()

    batch, records = stage_profile_records(tenant_id, memory_path=memory)
    await store.stage_migration_batch(batch, records, actor_id)
    duplicate, duplicate_records = stage_profile_records(tenant_id, memory_path=memory)
    await store.stage_migration_batch(duplicate, duplicate_records, actor_id)

    assert len(await store.list_migration_batches(tenant_id)) == 1
    assert len(await store.list_memories(tenant_id)) == 1
    assert await store.list_migration_batches(other_tenant) == ()
    assert await store.list_memories(other_tenant) == ()
    assert await store.get_active_persona(tenant_id) is None


async def test_activation_promotes_only_matching_batch_candidates(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    _source(path, trust_score=0.7)
    tenant_id, actor_id = uuid4(), uuid4()
    batch, records = stage_records(path, tenant_id)
    store = InMemoryRuntimeStore()
    await store.stage_migration_batch(batch, records, actor_id)
    other = records[0].model_copy(update={"id": uuid4(), "import_batch_id": uuid4()})
    store._memories[other.id] = other
    core = PersonaCore(
        values="v", boundaries="b", tone="t", initiative="i", emotional_range="e", disagreement="d"
    )
    active = await store.activate_migration_batch(tenant_id, batch.id, actor_id, core)
    assert active.state == "active"
    promoted = await store.get_memory(tenant_id, records[0].id)
    assert promoted is not None
    assert promoted.state is MemoryState.PROMOTED
    assert promoted.confirmed is True
    assert promoted.reviewed_at is not None
    assert promoted.reviewed_by == actor_id
    assert (await store.get_memory(tenant_id, other.id)).state is MemoryState.CANDIDATE  # type: ignore[union-attr]
    promotion_events = [
        event for event in store._events if event.event_type.value == "memory.promoted"
    ]
    assert [event.payload["memory_id"] for event in promotion_events] == [str(records[0].id)]
    rolled, deleted = await store.rollback_migration_batch(tenant_id, batch.id, actor_id)
    assert rolled.state == "rolled_back"
    assert len(deleted) == 1


async def test_api_activation_queues_vector_sync_for_promoted_candidates(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    _source(path, trust_score=0.7)
    tenant_id, actor_id = uuid4(), uuid4()
    batch, records = stage_records(path, tenant_id)
    store = InMemoryRuntimeStore()
    await store.stage_migration_batch(batch, records, actor_id)
    with TestClient(create_app(store=store)) as client:
        response = client.post(
            f"/api/v1/migration-batches/{batch.id}/activate",
            headers={"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(actor_id)},
            json={
                "tenant_id": str(tenant_id),
                "authored_core": {
                    "values": "v",
                    "boundaries": "b",
                    "tone": "t",
                    "initiative": "i",
                    "emotional_range": "e",
                    "disagreement": "d",
                },
            },
        )
    assert response.status_code == 200
    assert response.json()["batch"]["state"] == "active"
    assert len(await store.list_jobs(tenant_id)) == 1
