from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import jwt
from astra_agent import create_app
from astra_agent.auth import OIDCUserAuthenticator
from astra_agent.settings import Settings
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient


def _oidc_setup() -> tuple[Settings, OIDCUserAuthenticator, rsa.RSAPrivateKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    encryption_jwk = dict(public_jwk)
    encryption_jwk.update({"kid": "encryption-key", "use": "enc", "alg": "RSA-OAEP"})

    def jwks(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://idp.example/jwks"
        return httpx.Response(200, json={"keys": [encryption_jwk, public_jwk]})

    settings = Settings(
        user_auth_backend="oidc",
        oidc_issuer="https://idp.example",
        oidc_audience="astra-agent-api",
        oidc_jwks_url="https://idp.example/jwks",
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(jwks))
    return settings, OIDCUserAuthenticator(settings, client), private_key


def _token(
    private_key: rsa.RSAPrivateKey,
    tenant_id: str,
    *,
    audience: str = "astra-agent-api",
    expires_delta: timedelta = timedelta(minutes=5),
    roles: list[str] | None = None,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "https://idp.example",
            "aud": audience,
            "sub": "user-123",
            "tenant_id": tenant_id,
            "realm_access": {"roles": roles or []},
            "iat": now,
            "exp": now + expires_delta,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def test_oidc_token_authenticates_and_enforces_tenant() -> None:
    settings, authenticator, private_key = _oidc_setup()
    tenant_id = uuid4()
    other_tenant = uuid4()
    headers = {"Authorization": f"Bearer {_token(private_key, str(tenant_id))}"}
    with TestClient(create_app(settings=settings, user_authenticator=authenticator)) as client:
        assert client.get(f"/api/v1/tenants/{tenant_id}/tasks").status_code == 401
        assert client.get(f"/api/v1/tenants/{tenant_id}/tasks", headers=headers).status_code == 200
        assert (
            client.get(f"/api/v1/tenants/{other_tenant}/tasks", headers=headers).status_code == 403
        )
        task = client.post(
            "/api/v1/tasks",
            headers=headers,
            json={
                "task": {
                    "tenant_id": str(tenant_id),
                    "objective": "authenticated task",
                    "deliverable_contract": "report",
                }
            },
        )
        assert task.status_code == 201
        events = client.get(f"/api/v1/tenants/{tenant_id}/events", headers=headers).json()
        assert events[-1]["actor_type"] == "user"
        assert events[-1]["actor_id"] is not None


def test_oidc_rejects_bad_audience_and_expired_token() -> None:
    settings, authenticator, private_key = _oidc_setup()
    tenant_id = uuid4()
    with TestClient(create_app(settings=settings, user_authenticator=authenticator)) as client:
        path = f"/api/v1/tenants/{tenant_id}/tasks"
        bad_audience = _token(private_key, str(tenant_id), audience="wrong")
        expired = _token(private_key, str(tenant_id), expires_delta=timedelta(seconds=-1))
        assert (
            client.get(path, headers={"Authorization": f"Bearer {bad_audience}"}).status_code == 401
        )
        assert client.get(path, headers={"Authorization": f"Bearer {expired}"}).status_code == 401


def test_development_user_routes_are_not_anonymous_or_cross_tenant() -> None:
    tenant_id = uuid4()
    other_tenant = uuid4()
    headers = {"X-Astra-Tenant-ID": str(tenant_id), "X-Astra-User-ID": str(uuid4())}
    with TestClient(create_app()) as client:
        assert client.get(f"/api/v1/tenants/{tenant_id}/events").status_code == 401
        assert (
            client.get(f"/api/v1/tenants/{other_tenant}/events", headers=headers).status_code == 403
        )


def test_admin_routes_require_operator_role() -> None:
    settings, authenticator, private_key = _oidc_setup()
    tenant_id = uuid4()
    with TestClient(create_app(settings=settings, user_authenticator=authenticator)) as client:
        user_token = _token(private_key, str(tenant_id))
        operator_token = _token(private_key, str(tenant_id), roles=["platform_operator"])
        assert (
            client.get(
                "/api/v1/admin/overview", headers={"Authorization": f"Bearer {user_token}"}
            ).status_code
            == 403
        )
        response = client.get(
            "/api/v1/admin/overview", headers={"Authorization": f"Bearer {operator_token}"}
        )
        assert response.status_code == 200
        assert response.json()["tenant_id"] == str(tenant_id)
