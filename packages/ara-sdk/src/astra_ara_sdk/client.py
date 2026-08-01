from uuid import UUID

import httpx
from astra_protocol import (
    ApprovalRequest,
    ApprovalResponse,
    ARAEventRequest,
    CancelTaskRequest,
    CompleteTaskRequest,
    LeaseRequest,
    LeaseResponse,
    RegisterARARequest,
    RenewLeaseRequest,
    TaskLifecycleResponse,
)


class ARAClient:
    """Development client; production transport must use an mTLS-configured httpx client."""

    def __init__(
        self,
        base_url: str,
        tenant_id: UUID,
        ara_id: UUID,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            headers={"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-ARA-ID": str(ara_id)},
        )
        self._owns_client = client is None
        self.tenant_id = tenant_id
        self.ara_id = ara_id

    async def register(self, request: RegisterARARequest) -> None:
        response = await self._client.post(
            "/api/v1/aras/register", json=request.model_dump(mode="json")
        )
        response.raise_for_status()

    async def lease(self, duration_seconds: int = 60) -> LeaseResponse | None:
        request = LeaseRequest(
            tenant_id=self.tenant_id,
            ara_id=self.ara_id,
            duration_seconds=duration_seconds,
        )
        response = await self._client.post(
            "/api/v1/aras/lease", json=request.model_dump(mode="json")
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return LeaseResponse.model_validate(response.json())

    async def progress(self, request: ARAEventRequest) -> None:
        response = await self._client.post(
            "/api/v1/aras/events", json=request.model_dump(mode="json")
        )
        response.raise_for_status()

    async def renew(self, request: RenewLeaseRequest) -> LeaseResponse:
        response = await self._client.post(
            "/api/v1/aras/renew", json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return LeaseResponse.model_validate(response.json())

    async def complete(self, request: CompleteTaskRequest) -> TaskLifecycleResponse:
        response = await self._client.post(
            "/api/v1/aras/complete", json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return TaskLifecycleResponse.model_validate(response.json())

    async def cancel(self, request: CancelTaskRequest) -> TaskLifecycleResponse:
        response = await self._client.post(
            "/api/v1/aras/cancel", json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return TaskLifecycleResponse.model_validate(response.json())

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        response = await self._client.post(
            "/api/v1/aras/approvals", json=request.model_dump(mode="json")
        )
        response.raise_for_status()
        return ApprovalResponse.model_validate(response.json())

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
