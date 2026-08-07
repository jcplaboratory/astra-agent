import asyncio
from uuid import UUID

import httpx
from astra_protocol import (
    ApprovalRequest,
    ApprovalResponse,
    ARAEventRequest,
    CancelTaskRequest,
    CompleteTaskRequest,
    FailTaskRequest,
    HeartbeatRequest,
    HeartbeatResponse,
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

    async def _post(self, path: str, payload: dict[str, object]) -> httpx.Response:
        for attempt in range(3):
            try:
                response = await self._client.post(path, json=payload)
                if response.status_code < 500 or attempt == 2:
                    return response
            except httpx.TransportError:
                if attempt == 2:
                    raise
            await asyncio.sleep(2**attempt)
        raise RuntimeError("unreachable")

    async def register(self, request: RegisterARARequest) -> None:
        response = await self._post("/api/v1/aras/register", request.model_dump(mode="json"))
        response.raise_for_status()

    async def lease(self, duration_seconds: int = 60) -> LeaseResponse | None:
        try:
            request = LeaseRequest(
                tenant_id=self.tenant_id,
                ara_id=self.ara_id,
                duration_seconds=duration_seconds,
            )
            response = await self._post("/api/v1/aras/lease", request.model_dump(mode="json"))
            if response.status_code == 204:
                return None
            response.raise_for_status()
            return LeaseResponse.model_validate(response.json())
        except Exception:
            return None

    async def progress(self, request: ARAEventRequest) -> None:
        response = await self._post("/api/v1/aras/events", request.model_dump(mode="json"))
        response.raise_for_status()

    async def heartbeat(self, request: HeartbeatRequest) -> HeartbeatResponse:
        response = await self._post("/api/v1/aras/heartbeat", request.model_dump(mode="json"))
        response.raise_for_status()
        return HeartbeatResponse.model_validate(response.json())

    async def renew(self, request: RenewLeaseRequest) -> LeaseResponse:
        response = await self._post("/api/v1/aras/renew", request.model_dump(mode="json"))
        response.raise_for_status()
        return LeaseResponse.model_validate(response.json())

    async def complete(self, request: CompleteTaskRequest) -> TaskLifecycleResponse:
        response = await self._post("/api/v1/aras/complete", request.model_dump(mode="json"))
        response.raise_for_status()
        return TaskLifecycleResponse.model_validate(response.json())

    async def cancel(self, request: CancelTaskRequest) -> TaskLifecycleResponse:
        response = await self._post("/api/v1/aras/cancel", request.model_dump(mode="json"))
        response.raise_for_status()
        return TaskLifecycleResponse.model_validate(response.json())

    async def fail(self, request: FailTaskRequest) -> TaskLifecycleResponse:
        response = await self._post("/api/v1/aras/fail", request.model_dump(mode="json"))
        response.raise_for_status()
        return TaskLifecycleResponse.model_validate(response.json())

    async def request_approval(self, request: ApprovalRequest) -> ApprovalResponse:
        response = await self._post("/api/v1/aras/approvals", request.model_dump(mode="json"))
        response.raise_for_status()
        return ApprovalResponse.model_validate(response.json())

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
