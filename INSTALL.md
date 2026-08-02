# Production Installation Guide

This document describes how to deploy the implemented Astra Agent control plane and one or more Astra Remote Agents (ARAs) in production. It is a reference deployment guide, not turnkey production infrastructure: the repository's Compose files intentionally contain development Keycloak, MinIO credentials, and generated certificates. Replace those components with managed equivalents and do not expose the bundled development stack to the Internet.

## 1. Plan the deployment

Deploy these components in separate trust zones:

| Component | Purpose | Production requirement |
| --- | --- | --- |
| Astra Agent API | Control plane, policy, audit, and orchestration | Private service reachable by the user HTTPS gateway and ARA mTLS ingress only |
| MariaDB | Authoritative state and authorization data | Persistent storage, encrypted backups, restore tests, and a dedicated least-privilege database account |
| OIDC provider | User authentication | Managed or otherwise production-operated provider issuing RS256 or ES256 JWTs with a tenant UUID claim |
| ARA mTLS ingress | Authenticates ARAs and forwards verified identity | TLS termination with client-certificate verification; only component allowed to reach ARA API routes |
| S3-compatible object store | Optional artifact bytes | Private bucket and credentials restricted to the configured bucket |
| Qdrant | Optional semantic memory ranking | Private service; MariaDB remains the authorization source of truth |
| OpenRouter | Optional main model provider | Store API key in a secret manager |
| Local OpenAI-compatible model | Optional extraction/context compression | Private service reachable only from the API |
| ARA | Read-only repository inspection | Separate process/host with a restricted repository root and its own client certificate |

Do not expose the API directly to ARAs. The API trusts `X-Client-Verify`, certificate-identity headers, and `X-Astra-Proxy-Secret` only when they originate from the trusted mTLS ingress. Strip those headers at every other proxy boundary. Do not use `development_headers`, the bundled Keycloak realm, `ASTRA_TUI_DEV_LOGIN`, generated development certificates, or default MinIO credentials in production.

## 2. Provision external services

1. Create a MariaDB database for Astra Agent and a database account with access only to that database. Use a connection string compatible with SQLAlchemy `aiomysql`, for example:

   ```text
   mysql+aiomysql://astra_agent:URL_ENCODED_PASSWORD@mariadb.internal:3306/astra_agent?charset=utf8mb4
   ```

2. Configure a production OIDC client. Access tokens must contain `exp`, `iss`, `aud`, `sub`, and a tenant claim containing a UUID. The implementation accepts only `RS256` and `ES256` signing keys from JWKS.
3. Issue and rotate a private CA, an ingress server certificate, and one client certificate per ARA. The ARA certificate subject must encode the tenant UUID in `OU` and the ARA UUID in `CN`.
4. If artifacts are needed, create a private S3 bucket and a credential limited to that bucket. Astra Agent generates task-scoped object keys and presigned URLs.
5. If semantic retrieval is needed, provision Qdrant and create or allow Astra Agent to initialize the configured collection.
6. Set up encrypted database backups, object-store versioning/retention as appropriate, alerting, centralized logs, and an exercised database restore procedure before rollout.

## 3. Build and publish the API image

The included Dockerfile builds the API image using Python 3.12 and the locked `uv` dependencies:

```bash
docker build -t registry.example.com/astra-agent:0.1.0 .
docker push registry.example.com/astra-agent:0.1.0
```

Use an immutable image digest in your deployment system. The image listens on port `8000`; run it behind a user-facing HTTPS gateway. Keep port `8000` private from ARA networks except for the mTLS ingress.

## 4. Configure the API

Provide configuration as environment variables through your orchestrator and store credentials in its secret manager. The API loads `.env` when present, but production deployments should not bake or mount an unprotected `.env` file into the image.

Start from this minimum production configuration:

```dotenv
ASTRA_ENVIRONMENT=production
ASTRA_PERSISTENCE_BACKEND=mariadb
ASTRA_DATABASE_URL=mysql+aiomysql://astra_agent:URL_ENCODED_PASSWORD@mariadb.internal:3306/astra_agent?charset=utf8mb4
ASTRA_AUTH_BACKEND=mtls
ASTRA_TRUSTED_PROXY_SECRET=LONG_RANDOM_SHARED_SECRET
ASTRA_USER_AUTH_BACKEND=oidc
ASTRA_OIDC_ISSUER=https://identity.example.com
ASTRA_OIDC_AUDIENCE=astra-agent
ASTRA_OIDC_JWKS_URL=https://identity.example.com/.well-known/jwks.json
ASTRA_OIDC_TENANT_CLAIM=tenant_id
ASTRA_MODEL_BACKEND=openrouter
ASTRA_OPENROUTER_API_KEY=SECRET
ASTRA_OPENROUTER_MODEL=openai/gpt-4.1-mini
```

`ASTRA_DATABASE_URL` is mandatory with the MariaDB backend. The three OIDC endpoint settings are mandatory with `ASTRA_USER_AUTH_BACKEND=oidc`. `ASTRA_OPENROUTER_API_KEY` is mandatory with `ASTRA_MODEL_BACKEND=openrouter`.

Apply schema migrations as a separately controlled release step. Take and verify a backup first; upgrade one revision at a time in production:

```bash
ASTRA_DATABASE_URL='mysql+aiomysql://...' uv run alembic upgrade +1
uv run alembic current
```

Do not set `ASTRA_CREATE_SCHEMA_ON_STARTUP=true` as a production migration mechanism. Start the API only after the expected migration revision has been applied. A basic readiness smoke check is:

```bash
curl --fail --silent https://api.example.com/health
```

## 5. Deploy the ARA mTLS ingress

Deploy nginx or an equivalent proxy that:

1. Listens on a TLS endpoint reachable by ARAs.
2. Requires and validates client certificates against the ARA CA.
3. Extracts `OU` as the tenant UUID and `CN` as the ARA UUID.
4. Proxies only `/api/v1/aras/` to the private API.
5. Sets `X-Client-Verify: SUCCESS`, `X-Astra-Cert-Tenant-ID`, `X-Astra-Cert-ARA-ID`, and `X-Astra-Proxy-Secret` after certificate verification.
6. Does not accept these identity headers from clients, and prevents all other paths from reaching the API through this listener.

`deploy/nginx-mtls.conf.template` is a development reference for these rules. Supply production certificates and the same `ASTRA_TRUSTED_PROXY_SECRET` to the ingress and API through a secret manager. Protect the proxy-to-API network path so another workload cannot forge the headers.

## 6. Run each ARA

Install the workspace packages on the ARA host with Python 3.12+ and `uv`, or build an equivalent package image:

```bash
uv sync --all-packages
```

Give the ARA a dedicated client certificate and configure it with an existing, absolute repository root. Run it separately from the control plane:

```dotenv
ASTRA_ARA_AGENT_URL=https://ara-ingress.example.com
ASTRA_ARA_TENANT_ID=TENANT_UUID
ASTRA_ARA_ID=ARA_UUID
ASTRA_ARA_NAME=repository-ara
ASTRA_ARA_REPOSITORY_ROOT=/srv/repositories/project
ASTRA_ARA_CLIENT_CERTIFICATE=/run/secrets/ara.crt
ASTRA_ARA_CLIENT_KEY=/run/secrets/ara.key
ASTRA_ARA_CA_CERTIFICATE=/run/secrets/ara-ca.crt
```

Then start it with `uv run ara`. The ARA only performs bounded read-only inspection under its configured root; it does not execute commands or use network tools beyond its API connection. Keep its certificate and private key readable only by the ARA process and rotate certificates before expiry.

## 7. Verify the rollout

1. Confirm the API health endpoint through the user gateway.
2. Obtain a real OIDC access token and confirm its issuer, audience, expiry, subject, and tenant UUID claim match the API configuration.
3. Confirm the API cannot be reached directly from the ARA network and that a connection without a valid client certificate is rejected by ingress.
4. Start an ARA and verify it appears through `GET /api/v1/tenants/{tenant_id}/aras` as an authenticated user of that tenant.
5. Send a test conversation and verify MariaDB persists it. If enabled, verify an artifact upload/download and Qdrant retrieval.
6. Run a restore test and validate the restored migration revision with `uv run alembic current`.

## API Configuration Reference

All API variables use the `ASTRA_` prefix. Values not listed as required have the shown default. Boolean values are parsed by Pydantic settings; use explicit `true` or `false`.

| Variable | Default / valid values | Purpose |
| --- | --- | --- |
| `ASTRA_ENVIRONMENT` | `development` | Deployment label; currently not used to change runtime behavior. Set `production` for operational clarity. |
| `ASTRA_PERSISTENCE_BACKEND` | `memory`; `memory` or `mariadb` | Persistence backend. Production requires `mariadb`. |
| `ASTRA_DATABASE_URL` | unset | MariaDB SQLAlchemy URL; required for `mariadb`. |
| `ASTRA_CREATE_SCHEMA_ON_STARTUP` | `false` | Creates schema at API startup. Leave `false` in production; use Alembic. |
| `ASTRA_LEASE_DURATION_SECONDS` | `60`; 10-900 | ARA task lease duration. |
| `ASTRA_AUTH_BACKEND` | `development_headers`; `development_headers` or `mtls` | ARA authentication backend. Production requires `mtls`. |
| `ASTRA_TRUSTED_PROXY_SECRET` | unset | Shared secret required by trusted mTLS ingress requests. |
| `ASTRA_USER_AUTH_BACKEND` | `development_headers`; `development_headers` or `oidc` | User authentication backend. Production requires `oidc`. |
| `ASTRA_OIDC_ISSUER` | unset | Required OIDC issuer URL. |
| `ASTRA_OIDC_AUDIENCE` | unset | Required token audience. |
| `ASTRA_OIDC_JWKS_URL` | unset | Required signing-key JWKS URL. |
| `ASTRA_OIDC_TENANT_CLAIM` | `tenant_id` | JWT claim containing the tenant UUID. |
| `ASTRA_OIDC_JWKS_CACHE_SECONDS` | `300`; 30-86400 | JWKS cache lifetime. |
| `ASTRA_MODEL_BACKEND` | `development`; `development` or `openrouter` | Main conversation model. Production should use `openrouter`. |
| `ASTRA_OPENROUTER_API_KEY` | unset | Required when using `openrouter`. |
| `ASTRA_OPENROUTER_MODEL` | `openai/gpt-4.1-mini` | OpenRouter model identifier. |
| `ASTRA_OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter-compatible API base URL. |
| `ASTRA_PERSONA_KERNEL` | Built-in direct/accurate/user-control prompt | Stable persona text included in context. |
| `ASTRA_PERSONA_MAX_TOKENS` | `800`; 100-2000 | Hard persona and compiled-context budget. |
| `ASTRA_CONVERSATION_HISTORY_MESSAGES` | `12`; 2-50 | Recent-message window supplied to the model. |
| `ASTRA_MEMORY_BACKEND` | `local`; `local` or `qdrant` | Authorized lexical retrieval only, or lexical plus Qdrant ranking. |
| `ASTRA_MEMORY_MAX_RECORDS` | `8`; 1-50 | Maximum memories included in compiled context. |
| `ASTRA_QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant endpoint when enabled. |
| `ASTRA_QDRANT_API_KEY` | unset | Qdrant API key, when required by the service. |
| `ASTRA_QDRANT_COLLECTION` | `astra_memories` | Qdrant collection name. |
| `ASTRA_MEMORY_EXTRACTOR_BACKEND` | `deterministic`; `deterministic` or `local_model` | Memory extraction method. Model failures fall back to deterministic extraction. |
| `ASTRA_CONTEXT_COMPRESSOR_BACKEND` | `deterministic`; `deterministic` or `local_model` | Optional context reranking/compression. Empty or failed model output falls back to deterministic compilation. |
| `ASTRA_LOCAL_MODEL_URL` | `http://127.0.0.1:11434/v1` | OpenAI-compatible local-model endpoint. |
| `ASTRA_LOCAL_MODEL_NAME` | `qwen2.5:3b` | Local-model identifier. |
| `ASTRA_LOCAL_MODEL_API_KEY` | unset | Optional local-model credential. |
| `ASTRA_DELEGATION_ENABLED` | `true` | Enables bounded ARA delegation. |
| `ASTRA_DELEGATION_MAX_SIBLINGS` | `2`; 1-8 | Maximum distinct eligible ARAs selected for a delegated request. |
| `ASTRA_DELEGATION_WAIT_SECONDS` | `30`; 1-300 | Time a turn waits for delegated task completion. |
| `ASTRA_DELEGATION_POLL_SECONDS` | `0.25`; 0.05-5 | Delegation completion polling interval. |
| `ASTRA_TENANT_WORKSPACES` | `{}` | JSON map of tenant UUID to a trusted local workspace and grants; see below. |
| `ASTRA_TOOL_READ_MAX_BYTES` | `65536`; 1024-10485760 | Maximum bytes returned by local file reads. |
| `ASTRA_TOOL_SEARCH_MAX_FILE_BYTES` | `65536`; 1024-10485760 | Largest local file eligible for search. |
| `ASTRA_TOOL_SEARCH_MAX_FILES` | `200`; 1-10000 | Maximum local files searched. |
| `ASTRA_TOOL_SEARCH_MAX_MATCHES` | `100`; 1-10000 | Maximum local search matches. |
| `ASTRA_TOOL_SEARCH_MAX_OUTPUT_BYTES` | `65536`; 1024-10485760 | Maximum local search output. |
| `ASTRA_SANDBOX_EXECUTABLE` | unset | Absolute executable path named `docker` or `podman`; must be configured with image. |
| `ASTRA_SANDBOX_IMAGE` | unset | Approved sandbox image; must be configured with executable. |
| `ASTRA_SANDBOX_TIMEOUT_SECONDS` | `30`; 1-600 | Command sandbox timeout. |
| `ASTRA_SANDBOX_MAX_OUTPUT_BYTES` | `65536`; 1024-10485760 | Command sandbox output limit. |
| `ASTRA_SANDBOX_MEMORY_LIMIT` | `512m` | Container memory limit syntax accepted by Docker/Podman. |
| `ASTRA_SANDBOX_CPU_LIMIT` | `1`; greater than 0 to 64 | Container CPU limit. |
| `ASTRA_SANDBOX_PIDS_LIMIT` | `128`; 1-4096 | Container PID limit. |
| `ASTRA_ARTIFACT_BUCKET` | unset | Enables S3 artifact storage when set. |
| `ASTRA_ARTIFACT_ENDPOINT_URL` | unset | Optional S3-compatible endpoint URL. |
| `ASTRA_ARTIFACT_ACCESS_KEY` | unset | Optional S3 access key. |
| `ASTRA_ARTIFACT_SECRET_KEY` | unset | Optional S3 secret key. |
| `ASTRA_ARTIFACT_REGION` | `us-east-1` | S3 region. |

### Tenant workspaces and command sandbox

Local tools are disabled unless a tenant is explicitly mapped to a workspace. `ASTRA_TENANT_WORKSPACES` is JSON, for example:

```json
{
  "00000000-0000-0000-0000-000000000001": {
    "root": "/srv/astra-workspaces/tenant-1",
    "grants": [
      {"kind": "file.read", "scope": "workspace"},
      {"kind": "command.execute", "scope": "workspace"}
    ]
  }
}
```

Each root must be an existing absolute non-symlink directory. Roots must be unique and cannot be nested. Configure `ASTRA_SANDBOX_EXECUTABLE` and `ASTRA_SANDBOX_IMAGE` together. Commands run with a read-only workspace, no network, resource limits, and policy/approval controls; do not grant `command.execute:workspace` unless it is intended.

## ARA Configuration Reference

ARA variables use the `ASTRA_ARA_` prefix, except `ASTRA_ARA_ID` is intentionally shared with the API naming convention.

| Variable | Default / valid values | Purpose |
| --- | --- | --- |
| `ASTRA_ARA_AGENT_URL` | `http://127.0.0.1:8000` | API or, in production, mTLS ingress URL. |
| `ASTRA_ARA_TENANT_ID` | required UUID | Tenant served by this ARA. |
| `ASTRA_ARA_ID` | required UUID | ARA identity; must match the client certificate `CN`. |
| `ASTRA_ARA_NAME` | `repository-ara` | Display name during registration. |
| `ASTRA_ARA_REPOSITORY_ROOT` | required path | Repository root to inspect. Use an absolute restricted path. |
| `ASTRA_ARA_POLL_SECONDS` | `1`; 0.1-60 | Lease polling interval. |
| `ASTRA_ARA_ONCE` | `false` | Exit after one polling cycle; useful for diagnostics, not normal service. |
| `ASTRA_ARA_CLIENT_CERTIFICATE` | unset | PEM client certificate for mTLS. |
| `ASTRA_ARA_CLIENT_KEY` | unset | PEM private key for mTLS. |
| `ASTRA_ARA_CA_CERTIFICATE` | unset | PEM CA certificate used to verify ingress. |

## TUI Configuration Reference

The TUI is optional and has no browser UI. It uses the following `ASTRA_` variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `ASTRA_AGENT_URL` | `http://127.0.0.1:8000` | User-facing HTTPS API URL in production. |
| `ASTRA_ACCESS_TOKEN` | unset | OIDC bearer token for API requests. |
| `ASTRA_CONVERSATION_ID` | unset | Existing conversation UUID to open. |
| `ASTRA_TENANT_ID` | unset | Development-header tenant UUID; do not use for production OIDC identity. |
| `ASTRA_USER_ID` | unset | Development-header user UUID; do not use for production OIDC identity. |
| `ASTRA_TUI_DEV_LOGIN` | `false` | Development-only password-grant login. Keep `false` in production. |
| `ASTRA_TUI_OIDC_TOKEN_URL` | local Keycloak URL | Development-only automatic-login endpoint. |
| `ASTRA_TUI_OIDC_CLIENT_ID` | `astra-tui` | Development-only automatic-login client ID. |
| `ASTRA_TUI_OIDC_USERNAME` | `astra-dev` | Development-only automatic-login username. |
| `ASTRA_TUI_OIDC_PASSWORD` | `development-only` | Development-only automatic-login password. |

## Compose-only Development Variables

These are consumed by the repository Compose files, not by the API settings model. They are not production configuration defaults:

| Variable | Used by | Notes |
| --- | --- | --- |
| `MARIADB_ROOT_PASSWORD` | MariaDB and secure Compose API URL | Required by `compose.yaml`; do not use a root account for production API access. |
| `MINIO_ROOT_USER` | MinIO and `minio-init` | Defaults to `astra-development`; replace with managed object-storage credentials. |
| `MINIO_ROOT_PASSWORD` | MinIO and `minio-init` | Defaults to an insecure development value; never use in production. |
| `KEYCLOAK_ADMIN_PASSWORD` | Development Keycloak | Defaults to `development-only`; do not deploy this Keycloak configuration. |

The test-only variables `ASTRA_TEST_DATABASE_URL`, `ASTRA_TEST_S3_ENDPOINT`, `ASTRA_TEST_S3_ACCESS_KEY`, `ASTRA_TEST_S3_SECRET_KEY`, and `ASTRA_TEST_QDRANT_URL` are used only by integration tests and must not be supplied to production workloads.
