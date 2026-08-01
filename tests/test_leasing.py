import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from astra_domain import (
    ActorType,
    AuditEvent,
    Capability,
    CapabilityKind,
    EventType,
    RemoteAgent,
    Task,
)
from astra_runtime import InMemoryRuntimeStore


async def test_task_is_leased_exclusively_and_audited() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    capability = Capability(kind=CapabilityKind.FILE_READ, scope="/repo")
    aras = [
        RemoteAgent(
            tenant_id=tenant_id, name=str(i), runtime_version="1", capabilities=(capability,)
        )
        for i in range(2)
    ]
    for ara in aras:
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
        objective="inspect",
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

    expires_at = datetime.now(UTC) + timedelta(minutes=1)
    results = await asyncio.gather(
        *(store.lease_task(tenant_id, ara.id, expires_at) for ara in aras)
    )
    assert sum(result is not None for result in results) == 1
    assert [event.event_type for event in await store.list_events(tenant_id)].count(
        EventType.TASK_LEASED
    ) == 1


async def test_ara_cannot_lease_across_tenants() -> None:
    store = InMemoryRuntimeStore()
    ara_tenant = uuid4()
    task_tenant = uuid4()
    ara = RemoteAgent(tenant_id=ara_tenant, name="ara", runtime_version="1")
    await store.register_ara(
        ara,
        AuditEvent(
            tenant_id=ara_tenant,
            event_type=EventType.ARA_REGISTERED,
            actor_type=ActorType.ARA,
            actor_id=ara.id,
        ),
    )
    task = Task(tenant_id=task_tenant, objective="inspect", deliverable_contract="report")
    await store.add_task(
        task,
        AuditEvent(
            tenant_id=task_tenant,
            event_type=EventType.TASK_CREATED,
            actor_type=ActorType.COORDINATOR,
            task_id=task.id,
        ),
    )
    result = await store.lease_task(task_tenant, ara.id, datetime.now(UTC) + timedelta(minutes=1))
    assert result is None


async def test_expired_lease_can_be_reclaimed() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    aras = [
        RemoteAgent(tenant_id=tenant_id, name=str(index), runtime_version="1") for index in range(2)
    ]
    for ara in aras:
        await store.register_ara(
            ara,
            AuditEvent(
                tenant_id=tenant_id,
                event_type=EventType.ARA_REGISTERED,
                actor_type=ActorType.ARA,
                actor_id=ara.id,
            ),
        )
    task = Task(tenant_id=tenant_id, objective="inspect", deliverable_contract="report")
    await store.add_task(
        task,
        AuditEvent(
            tenant_id=tenant_id,
            event_type=EventType.TASK_CREATED,
            actor_type=ActorType.COORDINATOR,
            task_id=task.id,
        ),
    )
    expired_at = datetime.now(UTC) + timedelta(milliseconds=10)
    assert await store.lease_task(tenant_id, aras[0].id, expired_at) is not None
    await asyncio.sleep(0.02)
    reclaimed = await store.lease_task(
        tenant_id, aras[1].id, datetime.now(UTC) + timedelta(minutes=1)
    )
    assert reclaimed is not None
    assert reclaimed[1].ara_id == aras[1].id
