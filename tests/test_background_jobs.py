import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from astra_domain import BackgroundJob, BackgroundJobKind, BackgroundJobState, JobError
from astra_runtime import InMemoryRuntimeStore


async def test_job_enqueue_is_idempotent_and_tenant_scoped() -> None:
    store = InMemoryRuntimeStore()
    source_id = uuid4()
    first = await store.enqueue_job(
        BackgroundJob(
            tenant_id=uuid4(), kind=BackgroundJobKind.MEMORY_EXTRACTION, source_id=source_id
        )
    )
    duplicate = await store.enqueue_job(
        BackgroundJob(
            tenant_id=first.tenant_id,
            kind=BackgroundJobKind.MEMORY_EXTRACTION,
            source_id=source_id,
        )
    )
    foreign = await store.enqueue_job(
        BackgroundJob(
            tenant_id=uuid4(), kind=BackgroundJobKind.MEMORY_EXTRACTION, source_id=source_id
        )
    )
    assert duplicate.id == first.id
    assert foreign.id != first.id
    assert await store.list_jobs(first.tenant_id) == (first,)


async def test_concurrent_claims_are_exclusive_and_failures_back_off() -> None:
    store = InMemoryRuntimeStore()
    tenant_id = uuid4()
    job = await store.enqueue_job(
        BackgroundJob(
            tenant_id=tenant_id, kind=BackgroundJobKind.MEMORY_EXTRACTION, source_id=uuid4()
        )
    )
    claims = await asyncio.gather(
        *(store.claim_job(uuid4(), datetime.now(UTC) + timedelta(minutes=1)) for _ in range(8))
    )
    claimed = next(item for item in claims if item is not None)
    assert sum(item is not None for item in claims) == 1
    assert claimed.id == job.id
    retried = await store.retry_job(
        tenant_id,
        job.id,
        claimed.lease_id,
        JobError(type="ConnectionError", message="qdrant unavailable"),
        datetime.now(UTC) + timedelta(seconds=10),
    )
    assert retried.state is BackgroundJobState.RETRY
    assert await store.claim_job(uuid4(), datetime.now(UTC) + timedelta(minutes=1)) is None
    attempts = await store.list_job_attempts(tenant_id, job.id)
    assert attempts[0].error is not None
