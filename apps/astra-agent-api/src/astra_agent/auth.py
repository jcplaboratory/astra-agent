import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Annotated, Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import jwt
from fastapi import Header, HTTPException, Request, status
from jwt import InvalidTokenError, PyJWK

from astra_agent.settings import Settings


@dataclass(frozen=True)
class ARAPrincipal:
    tenant_id: UUID
    ara_id: UUID


class ARAAuthenticator(Protocol):
    async def authenticate(self) -> ARAPrincipal: ...


@dataclass(frozen=True)
class UserPrincipal:
    tenant_id: UUID
    user_id: UUID
    subject: str
    roles: frozenset[str] = frozenset()


class UserAuthenticator(Protocol):
    async def authenticate(self, request: Request) -> UserPrincipal: ...

    async def close(self) -> None: ...


async def development_ara_principal(
    tenant_id: Annotated[UUID | None, Header(alias="X-Astra-Tenant-ID")] = None,
    ara_id: Annotated[UUID | None, Header(alias="X-Astra-ARA-ID")] = None,
) -> ARAPrincipal:
    """Development-only identity injection; never use as a production trust boundary."""
    if tenant_id is None or ara_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="development ARA identity headers are required",
        )
    return ARAPrincipal(tenant_id=tenant_id, ara_id=ara_id)


async def trusted_mtls_ara_principal(request: Request) -> ARAPrincipal:
    settings = request.app.state.settings
    expected_secret = settings.trusted_proxy_secret
    supplied_secret = request.headers.get("X-Astra-Proxy-Secret")
    if not expected_secret or supplied_secret != expected_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "untrusted mTLS ingress")
    if request.headers.get("X-Client-Verify") != "SUCCESS":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "client certificate was not verified")
    try:
        return ARAPrincipal(
            tenant_id=UUID(request.headers["X-Astra-Cert-Tenant-ID"]),
            ara_id=UUID(request.headers["X-Astra-Cert-ARA-ID"]),
        )
    except (KeyError, ValueError) as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid certificate identity") from error


async def development_user_principal(
    tenant_id: Annotated[UUID | None, Header(alias="X-Astra-Tenant-ID")] = None,
    user_id: Annotated[UUID | None, Header(alias="X-Astra-User-ID")] = None,
) -> UserPrincipal:
    if tenant_id is None or user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "development user headers are required")
    return UserPrincipal(
        tenant_id=tenant_id,
        user_id=user_id,
        subject=str(user_id),
        roles=frozenset({"platform_operator"}),
    )


class DevelopmentUserAuthenticator:
    async def authenticate(self, request: Request) -> UserPrincipal:
        try:
            tenant_id = UUID(request.headers["X-Astra-Tenant-ID"])
            user_id = UUID(request.headers["X-Astra-User-ID"])
        except (KeyError, ValueError) as error:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "development user headers are required"
            ) from error
        return UserPrincipal(
            tenant_id=tenant_id,
            user_id=user_id,
            subject=str(user_id),
            roles=frozenset({"platform_operator"}),
        )

    async def close(self) -> None:
        return None


class OIDCUserAuthenticator:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.oidc_issuer or not settings.oidc_audience or not settings.oidc_jwks_url:
            raise RuntimeError(
                "ASTRA_OIDC_ISSUER, ASTRA_OIDC_AUDIENCE, and ASTRA_OIDC_JWKS_URL are required"
            )
        self._issuer = settings.oidc_issuer.rstrip("/")
        self._audience = settings.oidc_audience
        self._jwks_url = settings.oidc_jwks_url
        self._tenant_claim = settings.oidc_tenant_claim
        self._cache_seconds = settings.oidc_jwks_cache_seconds
        self._client = client or httpx.AsyncClient(timeout=5)
        self._owns_client = client is None
        self._keys: dict[str, PyJWK] = {}
        self._cache_expires_at = 0.0
        self._lock = asyncio.Lock()

    async def _refresh_keys(self) -> None:
        async with self._lock:
            if self._keys and monotonic() < self._cache_expires_at:
                return
            try:
                response = await self._client.get(self._jwks_url)
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
                keys = {
                    item["kid"]: PyJWK.from_dict(item)
                    for item in payload.get("keys", [])
                    if isinstance(item, dict)
                    and isinstance(item.get("kid"), str)
                    and item.get("use", "sig") == "sig"
                    and item.get("alg") in {"RS256", "ES256"}
                }
            except (httpx.HTTPError, ValueError, KeyError) as error:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC signing keys unavailable"
                ) from error
            if not keys:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "OIDC signing keys unavailable"
                )
            self._keys = keys
            self._cache_expires_at = monotonic() + self._cache_seconds

    async def authenticate(self, request: Request) -> UserPrincipal:
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bearer token is required")
        try:
            header = jwt.get_unverified_header(token)
            key_id = header.get("kid")
            if not isinstance(key_id, str):
                raise InvalidTokenError("token has no key id")
            if key_id not in self._keys or monotonic() >= self._cache_expires_at:
                await self._refresh_keys()
            key = self._keys.get(key_id)
            if key is None:
                self._cache_expires_at = 0
                await self._refresh_keys()
                key = self._keys.get(key_id)
            if key is None or key.algorithm_name not in {"RS256", "ES256"}:
                raise InvalidTokenError("unsupported signing key")
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[key.algorithm_name],
                issuer=self._issuer,
                audience=self._audience,
                options={"require": ["exp", "iss", "aud", "sub", self._tenant_claim]},
            )
            subject = claims["sub"]
            tenant_id = UUID(claims[self._tenant_claim])
            if not isinstance(subject, str) or not subject:
                raise InvalidTokenError("invalid subject")
            realm_access = claims.get("realm_access", {})
            roles = realm_access.get("roles", []) if isinstance(realm_access, dict) else []
            if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
                raise InvalidTokenError("invalid role claims")
        except (InvalidTokenError, KeyError, TypeError, ValueError) as error:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid bearer token") from error
        return UserPrincipal(
            tenant_id=tenant_id,
            user_id=uuid5(NAMESPACE_URL, f"{self._issuer}:{subject}"),
            subject=subject,
            roles=frozenset(roles),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
