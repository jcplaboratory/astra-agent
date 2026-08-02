import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from astra_domain import (
    ActorType,
    Approval,
    ApprovalState,
    AuditEvent,
    Capability,
    CapabilityKind,
    Conversation,
    ConversationMessage,
    EventType,
    MemoryKind,
    MemoryRecord,
    MemoryState,
    MessageRole,
    MigrationBatch,
    PersonaCore,
    RemoteAgent,
    Task,
    TaskState,
)
from astra_runtime import LifecycleConflictError, MariaDBRuntimeStore, create_schema
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.skipif(
    not os.getenv("ASTRA_TEST_DATABASE_URL"), reason="MariaDB test URL not configured"
)
async def test_mariadb_registration_and_leasing() -> None:
    engine = create_async_engine(os.environ["ASTRA_TEST_DATABASE_URL"], pool_pre_ping=True)
    await create_schema(engine)
    store = MariaDBRuntimeStore(engine)
    tenant_id = uuid4()
    ara = RemoteAgent(tenant_id=tenant_id, name="db-ara", runtime_version="1")
    await store.register_ara(
        ara,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.ARA_REGISTERED,
            actor_type=ActorType.ARA,
            actor_id=ara.id,
        ),
    )
    task = Task(tenant_id=tenant_id, objective="database test", deliverable_contract="report")
    await store.add_task(
        task,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.TASK_CREATED,
            actor_type=ActorType.COORDINATOR,
            task_id=task.id,
        ),
    )
    result = await store.lease_task(tenant_id, ara.id, datetime.now(UTC) + timedelta(minutes=1))
    assert result is not None
    assert result[0].id == task.id
    assert len(await store.list_events(tenant_id)) == 3
    await store.close()


@pytest.mark.skipif(
    not os.getenv("ASTRA_TEST_DATABASE_URL"), reason="MariaDB test URL not configured"
)
async def test_mariadb_ara_lifecycle_is_atomic() -> None:
    engine = create_async_engine(os.environ["ASTRA_TEST_DATABASE_URL"], pool_pre_ping=True)
    store = MariaDBRuntimeStore(engine)
    tenant_id = uuid4()
    capability = Capability(kind=CapabilityKind.COMMAND_EXECUTE, scope="pytest")
    ara = RemoteAgent(
        tenant_id=tenant_id,
        name="lifecycle-ara",
        runtime_version="1",
        capabilities=(capability,),
    )
    await store.register_ara(
        ara,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.ARA_REGISTERED,
            actor_type=ActorType.ARA,
            actor_id=ara.id,
        ),
    )
    task = Task(
        tenant_id=tenant_id,
        objective="lifecycle",
        deliverable_contract="report",
        required_capabilities=(capability,),
    )
    await store.add_task(
        task,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.TASK_CREATED,
            actor_type=ActorType.COORDINATOR,
            task_id=task.id,
        ),
    )
    leased = await store.lease_task(tenant_id, ara.id, datetime.now(UTC) + timedelta(minutes=1))
    assert leased is not None
    _, lease = leased
    await store.record_progress(tenant_id, ara.id, task.id, lease.id, "working", 25)
    renewed = await store.renew_lease(
        tenant_id, ara.id, task.id, lease.id, datetime.now(UTC) + timedelta(minutes=2)
    )
    assert renewed.expires_at > lease.expires_at
    approval = Approval(
        tenant_id=tenant_id,
        task_id=task.id,
        capability=capability,
        requested_by=ara.id,
        reason="run tests",
    )
    await store.request_approval(tenant_id, ara.id, task.id, lease.id, approval)
    decided = await store.decide_approval(tenant_id, approval.id, ApprovalState.GRANTED, uuid4())
    assert decided.state is ApprovalState.GRANTED
    completed = await store.finish_task(
        tenant_id, ara.id, task.id, lease.id, TaskState.COMPLETED, "done"
    )
    assert completed.state is TaskState.COMPLETED
    with pytest.raises(LifecycleConflictError):
        await store.finish_task(
            tenant_id, ara.id, task.id, lease.id, TaskState.COMPLETED, "duplicate"
        )
    await store.close()


@pytest.mark.skipif(
    not os.getenv("ASTRA_TEST_DATABASE_URL"), reason="MariaDB test URL not configured"
)
async def test_mariadb_conversation_persistence() -> None:
    engine = create_async_engine(os.environ["ASTRA_TEST_DATABASE_URL"], pool_pre_ping=True)
    store = MariaDBRuntimeStore(engine)
    try:
        tenant_id = uuid4()
        conversation = Conversation(tenant_id=tenant_id, user_id=uuid4(), title="Database chat")
        await store.create_conversation(
            conversation,
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.CONVERSATION_CREATED,
                actor_type=ActorType.USER,
                actor_id=conversation.user_id,
            ),
        )
        message = ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="persist me",
        )
        await store.append_message(
            message,
            (
                AuditEvent(
                    tenant_id=tenant_id,
                    event_type=EventType.CONVERSATION_RECEIVED,
                    actor_type=ActorType.USER,
                    actor_id=conversation.user_id,
                ),
            ),
        )
        second_message = ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.ASSISTANT,
            content="second in same second",
            created_at=message.created_at,
        )
        await store.append_message(second_message, ())
        assert await store.get_conversation(tenant_id, conversation.id) is not None
        messages = await store.list_messages(tenant_id, conversation.id)
        assert len(messages) == 2
        assert messages[0].model_dump(exclude={"created_at"}) == message.model_dump(
            exclude={"created_at"}
        )
        assert messages[1].id == second_message.id
        assert await store.get_conversation(uuid4(), conversation.id) is None
    finally:
        await store.close()


@pytest.mark.skipif(
    not os.getenv("ASTRA_TEST_DATABASE_URL"), reason="MariaDB test URL not configured"
)
async def test_mariadb_memory_lifecycle() -> None:
    engine = create_async_engine(os.environ["ASTRA_TEST_DATABASE_URL"], pool_pre_ping=True)
    store = MariaDBRuntimeStore(engine)
    try:
        tenant_id = uuid4()
        conversation = Conversation(tenant_id=tenant_id, user_id=uuid4())
        await store.create_conversation(
            conversation,
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.CONVERSATION_CREATED,
                actor_type=ActorType.USER,
                actor_id=conversation.user_id,
            ),
        )
        message = ConversationMessage(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            role=MessageRole.USER,
            content="remember that MariaDB is authoritative",
        )
        event = AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.CONVERSATION_RECEIVED,
            actor_type=ActorType.USER,
            actor_id=conversation.user_id,
        )
        await store.append_message(message, (event,))
        memory = MemoryRecord(
            tenant_id=tenant_id,
            kind=MemoryKind.FACT,
            content="MariaDB is authoritative",
            normalized_content="mariadb is authoritative",
            source_event_id=event.id,
            source_message_id=message.id,
            confidence=0.99,
            confirmed=True,
            state=MemoryState.PROMOTED,
        )
        stored = await store.upsert_memory(memory, ())
        duplicate = await store.upsert_memory(memory.model_copy(update={"id": uuid4()}), ())
        assert duplicate.id == stored.id
        assert await store.get_memory(tenant_id, stored.id) is not None
        assert len(await store.list_memories(tenant_id, include_candidates=False)) == 1
        candidate = memory.model_copy(
            update={
                "id": uuid4(),
                "content": "MariaDB remains authoritative",
                "normalized_content": "mariadb remains authoritative",
                "state": MemoryState.CANDIDATE,
                "confirmed": False,
            }
        )
        await store.upsert_memory(candidate, ())
        reviewed = await store.review_memory(
            tenant_id,
            candidate.id,
            True,
            conversation.user_id,
            replaces_memory_id=stored.id,
        )
        assert reviewed.state is MemoryState.PROMOTED
        assert reviewed.contradiction_of == stored.id
        assert reviewed.reviewed_by == conversation.user_id
        assert (await store.get_memory(tenant_id, stored.id)).state is MemoryState.DELETED  # type: ignore[union-attr]
        deleted = await store.delete_memory(tenant_id, reviewed.id, conversation.user_id)
        assert deleted.state is MemoryState.DELETED
        assert await store.list_memories(tenant_id) == ()
        assert await store.get_memory(uuid4(), stored.id) is None
    finally:
        await store.close()


@pytest.mark.skipif(
    not os.getenv("ASTRA_TEST_DATABASE_URL"), reason="MariaDB test URL not configured"
)
async def test_mariadb_activation_promotes_only_batch_candidates() -> None:
    engine = create_async_engine(os.environ["ASTRA_TEST_DATABASE_URL"], pool_pre_ping=True)
    store = MariaDBRuntimeStore(engine)
    try:
        tenant_id, actor_id = uuid4(), uuid4()
        batch = MigrationBatch(
            tenant_id=tenant_id,
            source_system="hermes_holographic",
            source_database_fingerprint="a" * 64,
        )
        candidate = MemoryRecord(
            tenant_id=tenant_id,
            kind=MemoryKind.FACT,
            content="Batch candidate",
            normalized_content="batch candidate",
            source_event_id=uuid4(),
            source_message_id=uuid4(),
            confidence=0.7,
            import_batch_id=batch.id,
            source_system=batch.source_system,
            source_database_fingerprint=batch.source_database_fingerprint,
            source_external_id="candidate",
        )
        await store.stage_migration_batch(batch, (candidate,), actor_id)
        other = candidate.model_copy(
            update={
                "id": uuid4(),
                "content": "Other batch candidate",
                "normalized_content": "other batch candidate",
                "import_batch_id": uuid4(),
                "source_external_id": "other-candidate",
            }
        )
        await store.upsert_memory(other, ())
        active = await store.activate_migration_batch(
            tenant_id,
            batch.id,
            actor_id,
            PersonaCore(
                values="v",
                boundaries="b",
                tone="t",
                initiative="i",
                emotional_range="e",
                disagreement="d",
            ),
        )
        promoted = await store.get_memory(tenant_id, candidate.id)
        assert active.state == "active"
        assert promoted is not None
        assert promoted.state is MemoryState.PROMOTED
        assert promoted.confirmed is True
        assert promoted.reviewed_at is not None
        assert promoted.reviewed_by == actor_id
        assert (await store.get_memory(tenant_id, other.id)).state is MemoryState.CANDIDATE  # type: ignore[union-attr]
        events = await store.list_events(tenant_id)
        assert any(
            event.event_type is EventType.MEMORY_PROMOTED
            and event.payload["memory_id"] == str(candidate.id)
            for event in events
        )
    finally:
        await store.close()
