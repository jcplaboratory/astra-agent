# Astra Agent

Astra Agent is a hosted, multi-tenant Distributed Enriched-Persona Agent (D.E.P.A.). The
Astra Agent control plane owns conversation, task, memory, policy, and audit state. Authenticated
Astra Remote Agents (ARA) pull bounded leases and operate with explicitly scoped capabilities.

The MVP provides authenticated multi-turn conversations, bounded persona and memory context,
development and OpenRouter model adapters, repository-inspection delegation to a read-only ARA,
default-deny policy, approvals, auditable task lifecycles, MariaDB persistence, Qdrant retrieval,
S3-compatible artifacts, and a Textual TUI.

High-level architecture, memory, and product differentiation documents are available in
[`docs/`](docs/README.md).

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Docker Desktop or Docker Engine with Compose
- OpenSSL, curl, and `uuidgen`
- Ports `8000`, `8080`, `8443`, `3306`, `6333`, `9000`, and `9001` available

The in-memory development mode requires only Python and `uv`. The complete secure local stack
uses Docker and the remaining tools.

## Setup

```bash
uv sync --all-packages
uv run astra-agent
```

In another terminal, run the TUI:

```bash
uv run astra-tui
```

API documentation is available at `http://127.0.0.1:8000/docs`; health is available at
`http://127.0.0.1:8000/health`.

## MariaDB

```bash
export MARIADB_ROOT_PASSWORD='choose-a-local-password'
docker compose up -d mariadb
export ASTRA_DATABASE_URL='mysql+aiomysql://root:choose-a-local-password@127.0.0.1:3306/astra_agent?charset=utf8mb4'
uv run alembic upgrade head
export ASTRA_PERSISTENCE_BACKEND=mariadb
uv run astra-agent
```

Astra Agent creates schema implicitly only when `ASTRA_CREATE_SCHEMA_ON_STARTUP=true`; Alembic is
the normal schema-management path. Run the MariaDB integration test explicitly with:

```bash
ASTRA_TEST_DATABASE_URL="$ASTRA_DATABASE_URL" uv run pytest tests/test_mariadb.py -q
```

## Development

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q
```

ARA registration and leasing use `X-Astra-Tenant-ID` and `X-Astra-ARA-ID` when
`ASTRA_AUTH_BACKEND=development_headers`. These development headers are not a security boundary.
With `ASTRA_AUTH_BACKEND=mtls`, Astra Agent accepts ARA identity only from a trusted ingress that
supplies `X-Client-Verify`, `X-Astra-Cert-Tenant-ID`, `X-Astra-Cert-ARA-ID`, and the secret
configured in `ASTRA_TRUSTED_PROXY_SECRET`. The Astra Agent API must not be directly reachable
around that ingress.

User routes use `X-Astra-Tenant-ID` and `X-Astra-User-ID` in development. Production deployments
set `ASTRA_USER_AUTH_BACKEND=oidc` plus issuer, audience, and JWKS URL. The TUI uses
`ASTRA_ACCESS_TOKEN` for OIDC or the development tenant and user variables.

## Conversations And ARA

The default `ASTRA_MODEL_BACKEND=development` is deterministic and requires no secret. To use
OpenRouter:

```bash
export ASTRA_MODEL_BACKEND=openrouter
export ASTRA_OPENROUTER_API_KEY='your-key'
export ASTRA_OPENROUTER_MODEL='deepseek/deepseek-v4-pro'
uv run astra-agent
```

Repository inspection is delegated to a registered ARA with only `file.read:repository`. Run the
development read-only repository ARA with:

```bash
export ASTRA_ARA_TENANT_ID='tenant-uuid'
export ASTRA_ARA_ID='ara-uuid'
export ASTRA_ARA_REPOSITORY_ROOT='/absolute/path/to/repository'
uv run ara
```

The ARA executes no commands and uses no network tools. It reads bounded text files beneath the
configured root and reports path and line evidence through the lease protocol.

## Memory

Local retrieval ranks authorized MariaDB records without extra infrastructure. Set
`ASTRA_MEMORY_BACKEND=qdrant` to use the `astra_memories` Qdrant collection. Qdrant is never the
authorization source of truth.

Set `ASTRA_MEMORY_EXTRACTOR_BACKEND=local_model` to use an OpenAI-compatible local model for strict
JSON extraction. Malformed output or model unavailability falls back to deterministic extraction.

```bash
export ASTRA_MEMORY_EXTRACTOR_BACKEND=local_model
export ASTRA_LOCAL_MODEL_NAME=qwen2.5:3b
docker compose --profile local-llm -f compose.yaml -f compose.secure.yaml up -d ollama ollama-init
docker compose -f compose.yaml -f compose.secure.yaml up -d --force-recreate astra-agent
```

For an Astra Agent process running on the host, set `ASTRA_LOCAL_MODEL_URL=http://127.0.0.1:11434/v1`.

## Secure Local Stack

The secure profile provides Keycloak OIDC and nginx mTLS for local development. It is not
production PKI or secret management.

```bash
uv sync --all-packages
export MARIADB_ROOT_PASSWORD='your-local-database-password'
export ASTRA_TRUSTED_PROXY_SECRET='generate-a-long-random-value'
export ASTRA_TENANT_ID='00000000-0000-0000-0000-000000000001'
export ASTRA_ARA_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
sh deploy/generate-astra-dev-certs.sh "$ASTRA_TENANT_ID" "$ASTRA_ARA_ID"
docker compose -f compose.yaml -f compose.secure.yaml up -d --build
```

Verify the services and obtain a development token:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8080/realms/astra-agent/.well-known/openid-configuration >/dev/null
export ASTRA_ACCESS_TOKEN="$(
  curl -fsS -X POST \
    http://localhost:8080/realms/astra-agent/protocol/openid-connect/token \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'client_id=astra-tui' \
    --data-urlencode 'username=astra-dev' \
    --data-urlencode 'password=development-only' \
    --data-urlencode 'grant_type=password' \
  | uv run python -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
)"
export ASTRA_AGENT_URL='http://localhost:8000'
```

Alternatively, the bundled secure local stack supports automatic development login. Put this
in the ignored `.env` file and run `uv run astra-tui`:

```bash
ASTRA_TUI_DEV_LOGIN=true
```

This uses the bundled `astra-dev` account and is not for production. Without a token,
development user ID, or enabled local login, the TUI message field stays disabled and displays
an authentication-required explanation.

Run the ARA in a second terminal:

```bash
export ASTRA_ARA_TENANT_ID='00000000-0000-0000-0000-000000000001'
export ASTRA_ARA_ID="$(
  openssl x509 -in deploy/tls/ara.crt -noout -subject \
  | sed -E 's/.*CN *= *([^,]+).*/\1/'
)"
export ASTRA_ARA_AGENT_URL='https://localhost:8443'
export ASTRA_ARA_REPOSITORY_ROOT='/absolute/path/to/repository'
export ASTRA_ARA_CLIENT_CERTIFICATE="$PWD/deploy/tls/ara.crt"
export ASTRA_ARA_CLIENT_KEY="$PWD/deploy/tls/ara.key"
export ASTRA_ARA_CA_CERTIFICATE="$PWD/deploy/tls/ca.crt"
uv run ara
```

Then run `uv run astra-tui`. The generated ARA certificate encodes tenant ID in `OU` and ARA ID
in `CN`. Regenerate certificates with `deploy/generate-astra-dev-certs.sh`, then restart ingress
with `docker compose -f compose.yaml -f compose.secure.yaml restart ara-ingress`.

Stop without deleting data using `docker compose -f compose.yaml -f compose.secure.yaml down`.
Reset local data using `docker compose -f compose.yaml -f compose.secure.yaml down -v`, then remove
`deploy/tls` and regenerate the certificates.

## Workspace Boundaries

- `packages/domain`: tenant-owned entities and lifecycle values
- `packages/protocol`: Astra Agent and ARA request/response contracts
- `packages/runtime`: atomic in-memory and MariaDB persistence
- `packages/policy`: capability and approval decisions
- `packages/memory`: context compiler interface
- `packages/model-providers`: main and local model interfaces
- `packages/ara-sdk`: ARA transport client
- `apps/astra-agent-api`: FastAPI control plane
- `apps/astra-tui`: conversation, task, approval, and memory interface
- `apps/ara`: bounded read-only repository inspection ARA

## Production Deployment

The repository provides a secure local installation, not a turnkey production deployment.
Production requires managed OIDC and PKI, secret rotation, TLS for user traffic, network isolation
that prevents ingress bypass, durable backups, monitoring, and deployment-specific infrastructure.
