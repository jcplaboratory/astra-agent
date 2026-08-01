import os
from uuid import uuid4

import boto3
import pytest
from astra_agent.artifacts import S3ArtifactStore
from astra_agent.settings import Settings
from astra_domain import Artifact, MemoryRecord, MemoryState
from astra_memory import QdrantVectorIndex, deterministic_embedding


@pytest.mark.skipif(
    not os.getenv("ASTRA_TEST_S3_ENDPOINT"), reason="S3 test endpoint not configured"
)
def test_s3_artifact_integrity_verification() -> None:
    endpoint = os.environ["ASTRA_TEST_S3_ENDPOINT"]
    access_key = os.environ["ASTRA_TEST_S3_ACCESS_KEY"]
    secret_key = os.environ["ASTRA_TEST_S3_SECRET_KEY"]
    bucket = f"astra-test-{uuid4()}"
    settings = Settings(
        artifact_bucket=bucket,
        artifact_endpoint_url=endpoint,
        artifact_access_key=access_key,
        artifact_secret_key=secret_key,
    )
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
    )
    client.create_bucket(Bucket=bucket)
    tenant_id = uuid4()
    task_id = uuid4()
    content = b"verified artifact"
    sha256 = __import__("hashlib").sha256(content).hexdigest()
    key = f"tenants/{tenant_id}/tasks/{task_id}/{uuid4()}-report.txt"
    try:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType="text/plain",
            Metadata={"sha256": sha256},
        )
        artifact = Artifact(
            tenant_id=tenant_id,
            task_id=task_id,
            name="report.txt",
            media_type="text/plain",
            object_key=key,
            size_bytes=len(content),
            sha256=sha256,
        )
        store = S3ArtifactStore(settings)
        store.verify(artifact)
        with pytest.raises(ValueError, match="size"):
            store.verify(artifact.model_copy(update={"size_bytes": len(content) + 1}))
        with pytest.raises(ValueError, match="scope"):
            store.verify(artifact.model_copy(update={"object_key": f"other/{key}"}))
    finally:
        client.delete_object(Bucket=bucket, Key=key)
        client.delete_bucket(Bucket=bucket)


@pytest.mark.skipif(not os.getenv("ASTRA_TEST_QDRANT_URL"), reason="Qdrant test URL not configured")
async def test_qdrant_collection_upsert_rank_and_delete() -> None:
    collection = f"astra_test_{uuid4().hex}"
    index = QdrantVectorIndex(os.environ["ASTRA_TEST_QDRANT_URL"], collection)
    tenant_id = uuid4()
    memory = MemoryRecord(
        tenant_id=tenant_id,
        kind="fact",
        content="Qdrant integration memory",
        normalized_content="qdrant integration memory",
        source_event_id=uuid4(),
        source_message_id=uuid4(),
        confidence=1,
        confirmed=True,
        state=MemoryState.PROMOTED,
    )
    try:
        await index.ensure_ready()
        vector = deterministic_embedding(memory.content)
        await index.upsert(memory, vector)
        ranked = await index.rank(tenant_id, "private", (memory.id,), vector, 5)
        assert ranked == (memory.id,)
        await index.delete(memory.id)
        assert await index.rank(tenant_id, "private", (memory.id,), vector, 5) == ()
    finally:
        await index.close()
