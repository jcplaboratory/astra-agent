from uuid import uuid4

import httpx
from astra_ara_sdk import ARAClient
from astra_protocol import RegisterARARequest


async def test_ara_sdk_uses_plural_transport_route() -> None:
    seen_path = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_path
        seen_path = request.url.path
        return httpx.Response(201, json={})

    tenant_id = uuid4()
    ara_id = uuid4()
    http_client = httpx.AsyncClient(
        base_url="http://astra.test", transport=httpx.MockTransport(handler)
    )
    client = ARAClient("http://astra.test", tenant_id, ara_id, http_client)
    await client.register(
        RegisterARARequest(tenant_id=tenant_id, name="test-ara", runtime_version="1")
    )
    assert seen_path == "/api/v1/aras/register"
    await http_client.aclose()
