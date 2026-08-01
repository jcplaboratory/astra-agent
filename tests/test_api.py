from uuid import uuid4

from astra_agent import create_app
from fastapi.testclient import TestClient


def test_health_registration_and_leasing() -> None:
    tenant_id = uuid4()
    ara_id = uuid4()
    user_id = uuid4()
    headers = {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-ARA-ID": str(ara_id)}
    user_headers = {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(user_id)}
    with TestClient(create_app()) as client:
        health = client.get("/health")
        assert health.json() == {"status": "ok", "version": "0.1.0", "persistence": "memory"}

        registration = client.post(
            "/api/v1/aras/register",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "name": "code-ara",
                "runtime_version": "0.1",
                "capabilities": [{"kind": "file.read", "scope": "/repo"}],
            },
        )
        assert registration.status_code == 201

        task = client.post(
            "/api/v1/tasks",
            headers=user_headers,
            json={
                "task": {
                    "tenant_id": str(tenant_id),
                    "objective": "Inspect authentication",
                    "deliverable_contract": "Structured report",
                    "required_capabilities": [{"kind": "file.read", "scope": "/repo"}],
                }
            },
        )
        assert task.status_code == 201

        lease = client.post(
            "/api/v1/aras/lease",
            headers=headers,
            json={
                "tenant_id": str(tenant_id),
                "ara_id": str(ara_id),
                "duration_seconds": 60,
            },
        )
        assert lease.status_code == 200
        assert lease.json()["task"]["state"] == "leased"

        assert (
            client.post(
                "/api/v1/aras/lease",
                headers=headers,
                json={
                    "tenant_id": str(tenant_id),
                    "ara_id": str(ara_id),
                    "duration_seconds": 60,
                },
            ).status_code
            == 204
        )
