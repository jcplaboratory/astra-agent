import asyncio

import httpx
from astra_ara_sdk import ARAClient
from astra_domain import Capability, CapabilityKind
from astra_protocol import (
    ARAEventRequest,
    CancelTaskRequest,
    CompleteTaskRequest,
    FailTaskRequest,
    HeartbeatRequest,
    RegisterARARequest,
    RenewLeaseRequest,
)

from astra_ara.host_executor import HostCommandExecutor
from astra_ara.settings import HostARASettings


async def serve(settings: HostARASettings) -> None:
    transport = None
    if settings.client_certificate and settings.client_key:
        ssl_context = __import__("ssl").create_default_context(
            cafile=str(settings.ca_certificate) if settings.ca_certificate else None
        )
        ssl_context.load_cert_chain(settings.client_certificate, settings.client_key)
        transport = httpx.AsyncHTTPTransport(verify=ssl_context)
    http_client = (
        httpx.AsyncClient(base_url=settings.agent_url, transport=transport)
        if transport is not None
        else None
    )
    client = ARAClient(settings.agent_url, settings.tenant_id, settings.ara_id, http_client)
    executor = HostCommandExecutor(settings)
    capability = Capability(kind=CapabilityKind.COMMAND_EXECUTE_HOST, scope="host")
    await client.register(
        RegisterARARequest(
            tenant_id=settings.tenant_id,
            name=settings.name,
            runtime_version="0.1.0-host",
            capabilities=(capability,),
        )
    )
    try:
        while True:
            leased = await client.lease(120)
            if leased is None:
                if settings.once:
                    return
                await asyncio.sleep(settings.poll_seconds)
                continue
            bound = {
                "tenant_id": settings.tenant_id,
                "ara_id": settings.ara_id,
                "task_id": leased.task.id,
                "lease_id": leased.lease.id,
            }
            await client.progress(
                ARAEventRequest(**bound, message="Executing host command", progress_percent=10)
            )
            execution = asyncio.create_task(asyncio.to_thread(executor.execute, leased.task))
            try:
                while not execution.done():
                    await asyncio.sleep(30)
                    health = await client.heartbeat(HeartbeatRequest(**bound))
                    if health.cancellation_requested:
                        execution.cancel()
                        await client.cancel(
                            CancelTaskRequest(
                                **bound, reason="Cancellation requested by control plane"
                            )
                        )
                        break
                    renewed = await client.renew(RenewLeaseRequest(**bound, duration_seconds=120))
                    bound["lease_id"] = renewed.lease.id
                if not execution.cancelled():
                    result = await execution
                    await client.complete(CompleteTaskRequest(**bound, result=result))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await client.fail(
                    FailTaskRequest(**bound, error=f"{type(error).__name__}: {error}")
                )
            if settings.once:
                return
    finally:
        await client.close()
        if http_client is not None:
            await http_client.aclose()


def run() -> None:
    asyncio.run(serve(HostARASettings()))
