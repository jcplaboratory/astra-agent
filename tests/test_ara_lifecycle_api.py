from uuid import UUID, uuid4

from astra_agent import create_app
from astra_agent.artifacts import UploadTarget
from astra_agent.settings import Settings
from astra_domain import Artifact
from fastapi.testclient import TestClient


class FakeArtifactStore:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid

    def prepare_upload(
        self, tenant_id: UUID, task_id: UUID, name: str, media_type: str, sha256: str
    ) -> UploadTarget:
        return UploadTarget(
            f"tenants/{tenant_id}/tasks/{task_id}/object-{name}", "https://upload.test", 900
        )

    def verify(self, artifact: Artifact) -> None:
        if not self.valid:
            raise ValueError("artifact size does not match object storage")


def _headers(tenant_id: UUID, ara_id: UUID) -> dict[str, str]:
    return {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-ARA-ID": str(ara_id)}


def _leased_task(client: TestClient) -> tuple[UUID, UUID, UUID, UUID, dict[str, str]]:
    tenant_id = uuid4()
    ara_id = uuid4()
    user_id = uuid4()
    headers = _headers(tenant_id, ara_id)
    capability = {"kind": "command.execute", "scope": "pytest"}
    response = client.post(
        "/api/v1/aras/register",
        headers=headers,
        json={
            "tenant_id": str(tenant_id),
            "name": "ara",
            "runtime_version": "1",
            "capabilities": [capability],
        },
    )
    assert response.status_code == 201
    response = client.post(
        "/api/v1/tasks",
        headers={"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(user_id)},
        json={
            "task": {
                "tenant_id": str(tenant_id),
                "objective": "test",
                "deliverable_contract": "report",
                "required_capabilities": [capability],
            }
        },
    )
    assert response.status_code == 201
    task_id = UUID(response.json()["id"])
    response = client.post(
        "/api/v1/aras/lease",
        headers=headers,
        json={
            "tenant_id": str(tenant_id),
            "ara_id": str(ara_id),
            "duration_seconds": 60,
        },
    )
    assert response.status_code == 200
    return tenant_id, ara_id, task_id, UUID(response.json()["lease"]["id"]), headers


def test_ara_lifecycle_and_approval() -> None:
    user_id = uuid4()
    with TestClient(create_app()) as client:
        tenant_id, ara_id, task_id, lease_id, headers = _leased_task(client)
        bound = {
            "tenant_id": str(tenant_id),
            "ara_id": str(ara_id),
            "task_id": str(task_id),
            "lease_id": str(lease_id),
        }
        progress = client.post(
            "/api/v1/aras/events",
            headers=headers,
            json={
                **bound,
                "message": "running tests",
                "progress_percent": 50,
            },
        )
        assert progress.status_code == 202

        renewal = client.post(
            "/api/v1/aras/renew",
            headers=headers,
            json={
                **bound,
                "duration_seconds": 120,
            },
        )
        assert renewal.status_code == 200

        approval = client.post(
            "/api/v1/aras/approvals",
            headers=headers,
            json={
                **bound,
                "capability": {"kind": "command.execute", "scope": "pytest"},
                "reason": "Run the project test suite",
            },
        )
        assert approval.status_code == 201
        approval_id = approval.json()["approval"]["id"]
        blocked = client.post(
            "/api/v1/aras/complete",
            headers=headers,
            json={
                **bound,
                "result": "not approved",
                "artifacts": [],
            },
        )
        assert blocked.status_code == 409
        user_headers = {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(user_id)}
        decision = client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            headers=user_headers,
            json={"tenant_id": str(tenant_id), "granted": True},
        )
        assert decision.status_code == 200
        assert decision.json()["approval"]["state"] == "granted"
        assert (
            client.post(
                f"/api/v1/approvals/{approval_id}/decision",
                headers=user_headers,
                json={"tenant_id": str(tenant_id), "granted": True},
            ).status_code
            == 409
        )

        completion = client.post(
            "/api/v1/aras/complete",
            headers=headers,
            json={
                **bound,
                "result": "All tests passed",
                "artifacts": [],
            },
        )
        assert completion.status_code == 200
        assert completion.json()["state"] == "completed"
        assert (
            client.post(
                "/api/v1/aras/complete",
                headers=headers,
                json={
                    **bound,
                    "result": "duplicate",
                    "artifacts": [],
                },
            ).status_code
            == 409
        )


def test_wrong_ara_cannot_use_lease() -> None:
    with TestClient(create_app()) as client:
        tenant_id, ara_id, task_id, lease_id, _ = _leased_task(client)
        attacker_id = uuid4()
        response = client.post(
            "/api/v1/aras/events",
            headers=_headers(tenant_id, attacker_id),
            json={
                "tenant_id": str(tenant_id),
                "ara_id": str(attacker_id),
                "task_id": str(task_id),
                "lease_id": str(lease_id),
                "message": "spoofed",
            },
        )
        assert response.status_code == 404
        assert ara_id != attacker_id


def test_mtls_headers_require_trusted_proxy_secret() -> None:
    settings = Settings(auth_backend="mtls", trusted_proxy_secret="proxy-secret")
    tenant_id = uuid4()
    ara_id = uuid4()
    payload = {
        "tenant_id": str(tenant_id),
        "name": "ara",
        "runtime_version": "1",
        "capabilities": [],
    }
    certificate_headers = {
        "X-Client-Verify": "SUCCESS",
        "X-Astra-Cert-Tenant-ID": str(tenant_id),
        "X-Astra-Cert-ARA-ID": str(ara_id),
    }
    with TestClient(create_app(settings=settings)) as client:
        assert (
            client.post(
                "/api/v1/aras/register", headers=certificate_headers, json=payload
            ).status_code
            == 401
        )
        response = client.post(
            "/api/v1/aras/register",
            headers={**certificate_headers, "X-Astra-Proxy-Secret": "proxy-secret"},
            json=payload,
        )
        assert response.status_code == 201


def test_artifact_is_verified_before_completion() -> None:
    artifact_store = FakeArtifactStore(valid=False)
    with TestClient(create_app(artifact_store=artifact_store)) as client:
        tenant_id, ara_id, task_id, lease_id, headers = _leased_task(client)
        bound = {
            "tenant_id": str(tenant_id),
            "ara_id": str(ara_id),
            "task_id": str(task_id),
            "lease_id": str(lease_id),
        }
        sha256 = "a" * 64
        upload = client.post(
            "/api/v1/aras/artifacts/upload",
            headers=headers,
            json={**bound, "name": "report.txt", "media_type": "text/plain", "sha256": sha256},
        )
        assert upload.status_code == 200
        artifact = {
            "tenant_id": str(tenant_id),
            "task_id": str(task_id),
            "name": "report.txt",
            "media_type": "text/plain",
            "object_key": upload.json()["object_key"],
            "size_bytes": 6,
            "sha256": sha256,
        }
        blocked = client.post(
            "/api/v1/aras/complete",
            headers=headers,
            json={**bound, "result": "done", "artifacts": [artifact]},
        )
        assert blocked.status_code == 409
        artifact_store.valid = True
        approval = client.post(
            "/api/v1/aras/approvals",
            headers=headers,
            json={
                **bound,
                "capability": {"kind": "command.execute", "scope": "pytest"},
                "reason": "complete verified artifact task",
            },
        )
        user_headers = {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(uuid4())}
        assert (
            client.post(
                f"/api/v1/approvals/{approval.json()['approval']['id']}/decision",
                headers=user_headers,
                json={"tenant_id": str(tenant_id), "granted": True},
            ).status_code
            == 200
        )
        completion = client.post(
            "/api/v1/aras/complete",
            headers=headers,
            json={**bound, "result": "done", "artifacts": [artifact]},
        )
        assert completion.status_code == 200
