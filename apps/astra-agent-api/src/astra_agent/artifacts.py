from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

import boto3
from astra_domain import Artifact
from botocore.exceptions import ClientError

from astra_agent.settings import Settings


@dataclass(frozen=True)
class UploadTarget:
    object_key: str
    upload_url: str
    expires_in_seconds: int


class ArtifactStore(Protocol):
    def prepare_upload(
        self,
        tenant_id: UUID,
        task_id: UUID,
        name: str,
        media_type: str,
        sha256: str,
    ) -> UploadTarget: ...

    def verify(self, artifact: Artifact) -> None: ...


class S3ArtifactStore:
    def __init__(self, settings: Settings) -> None:
        if not settings.artifact_bucket:
            raise RuntimeError("ASTRA_ARTIFACT_BUCKET is required for artifact uploads")
        self._bucket = settings.artifact_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.artifact_endpoint_url,
            aws_access_key_id=settings.artifact_access_key,
            aws_secret_access_key=settings.artifact_secret_key,
            region_name=settings.artifact_region,
        )

    def prepare_upload(
        self,
        tenant_id: UUID,
        task_id: UUID,
        name: str,
        media_type: str,
        sha256: str,
    ) -> UploadTarget:
        safe_name = quote(name.replace("/", "_"), safe="._-")
        key = f"tenants/{tenant_id}/tasks/{task_id}/{uuid4()}-{safe_name}"
        expires = 900
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": media_type,
                "Metadata": {"sha256": sha256},
            },
            ExpiresIn=expires,
        )
        return UploadTarget(key, url, expires)

    def verify(self, artifact: Artifact) -> None:
        expected_prefix = f"tenants/{artifact.tenant_id}/tasks/{artifact.task_id}/"
        if not artifact.object_key.startswith(expected_prefix):
            raise ValueError("artifact object key is outside the task scope")
        try:
            metadata = self._client.head_object(Bucket=self._bucket, Key=artifact.object_key)
        except ClientError as error:
            raise ValueError("artifact object does not exist") from error
        if metadata.get("ContentLength") != artifact.size_bytes:
            raise ValueError("artifact size does not match object storage")
        if metadata.get("ContentType") != artifact.media_type:
            raise ValueError("artifact media type does not match object storage")
        if metadata.get("Metadata", {}).get("sha256") != artifact.sha256:
            raise ValueError("artifact SHA-256 does not match object metadata")
