<p align="center">
  <img src="docs/assets/astra-agent.png" alt="Astra Agent" width="320">
</p>

<h1 align="center">Astra Agent</h1>

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

### Interactive installer

For a local single-host installation, run:

```bash
bash install.sh
```

This is a production installer and must be run as `root`. It collects every controller setting,
including Keycloak base URL, realm, client audience, JWKS endpoint, tenant claim, mTLS ingress,
OpenRouter, S3 artifacts, Qdrant, local-model processing, delegated ARAs, tenant workspaces, and
sandbox limits. It selects a local or Docker controller, an existing MariaDB instance or a new
private MariaDB container, and runs Qdrant in Docker. Provide a directory containing production
`server.crt`, `server.key`, and `ca.crt` files for the ARA mTLS ingress.

The installer writes a root-only environment file and Compose definition to `/etc/astra-agent`
(override with `ASTRA_INSTALL_DIR`), starts the Dockerized services, and installs the `astra`
launcher in `/usr/bin`. It deliberately leaves user-facing HTTPS termination to the
operator's gateway. For an existing database, enter a SQLAlchemy URL reachable by the selected
controller: a Docker controller cannot use `127.0.0.1` to reach a database running on the host.

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

Upgrade one migration at a time in production and verify a backup/restore before deployment. The
current milestone adds persona profiles, durable background jobs, and artifact/ARA lifecycle
tables through revisions `0010` to `0013`; use `uv run alembic current` to confirm the deployed
revision. Do not use schema-on-startup as a migration substitute.

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

The main model produces a strict typed delegation plan before task persistence. Policy permits only
the read-only repository capability and evidence deliverable, then atomically assigns up to
`ASTRA_DELEGATION_MAX_SIBLINGS` (default `2`) distinct healthy, trusted ARAs. Targeted leases are
claimable only by their selected ARA; manually created tasks remain normally leaseable. Final
answers include each sibling's task and ARA provenance, including transparent partial failures.

## Tenant Workspaces And Sandbox

Local model tools are disabled unless the tenant has a trusted workspace mapping. Configure each
tenant with an absolute, existing, non-symlink root and explicit grants in `ASTRA_TENANT_WORKSPACES`:

```json
{"tenant-uuid":{"root":"/absolute/path/to/workspace","grants":[{"kind":"file.read","scope":"workspace"}]}}
```

`run_command` additionally requires `command.execute:workspace` and a sandbox configuration. Set
both `ASTRA_SANDBOX_EXECUTABLE` to an absolute `docker` or `podman` executable and
`ASTRA_SANDBOX_IMAGE` to the approved image. Commands run with no network, a read-only workspace,
resource limits, and an approval gate unless policy pre-authorizes the capability.

## Memory

### Hermes Holographic import

Hermes imports are local, explicit, and read-only. The hosted API never accepts a filesystem path.
Dry-run an explicit SQLite database and/or profile files locally:

```bash
uv run astra-import-hermes --tenant <tenant-uuid> \
  --database /path/to/memory_store.db --user /path/to/USER.md \
  --memory /path/to/MEMORY.md --soul /path/to/SOUL.md --dry-run
```

The SQLite importer accepts only its documented layout, uses `mode=ro&immutable=1`, and excludes HRR
vectors and FTS data. The Wave 2 Markdown importer deterministically reads headings and bullets as
inert data; it never interprets credentials, endpoints, tools, allowlists, or instructions. Every
`USER.md`/`MEMORY.md` record is a review candidate and cannot modify the persona.

Staging is never implied by a dry run. Configure `ASTRA_DATABASE_URL` and explicitly replace
`--dry-run` with `--stage` to write a tenant-scoped staged batch. The command never activates a
batch. Review and activation remain separate authenticated operations; rollback soft-deletes the
batch records and queues vector deletion.

Local retrieval ranks authorized MariaDB records without extra infrastructure. Set
`ASTRA_MEMORY_BACKEND=qdrant` to use the `astra_memories` Qdrant collection. Qdrant is never the
authorization source of truth.

Set `ASTRA_MEMORY_EXTRACTOR_BACKEND=local_model` to use an OpenAI-compatible local model for strict
JSON extraction. Malformed output or model unavailability falls back to deterministic extraction.

Set `ASTRA_CONTEXT_COMPRESSOR_BACKEND=local_model` to optionally rerank/compress compiled context
with that same local model. The deterministic compiler remains the fallback on failure or empty
output, and `ASTRA_PERSONA_MAX_TOKENS` remains a hard context budget in both paths.

```bash
export ASTRA_MEMORY_EXTRACTOR_BACKEND=local_model
export ASTRA_LOCAL_MODEL_NAME=qwen2.5:3b
docker compose --profile local-llm -f compose.yaml -f compose.secure.yaml up -d ollama ollama-init
docker compose -f compose.yaml -f compose.secure.yaml up -d --force-recreate astra-agent
```

For an Astra Agent process running on the host, set `ASTRA_LOCAL_MODEL_URL=http://127.0.0.1:11434/v1`.

## Operations

Conversation requests persist raw input and enqueue memory/vector work; they do not wait for the
local model or Qdrant. The in-process runner claims MariaDB jobs transactionally and retries
failures with bounded exponential backoff. Inspect failures through `GET /api/v1/tenants/{tenant_id}/jobs`
or the TUI; restart Astra Agent to resume durable queued work after resolving the dependency.

ARAs heartbeat while executing, renew leases, observe cancellation from heartbeat responses, and
report structured task failures. An ARA is marked offline after its heartbeat expires; inspect
health with `GET /api/v1/tenants/{tenant_id}/aras`. Keep the ARA process running separately from
the control plane and rotate its mTLS certificate before expiry.

Artifact listings contain metadata only. Request `POST /api/v1/artifacts/{artifact_id}/download`
after selecting an authorized artifact to generate a short-lived GET URL. The server authorizes
the artifact through MariaDB before signing; object keys are never caller input. Deletion retains
an append-only audit event and applies the configured retention window.

Sandboxed local commands require both a tenant workspace grant and `ASTRA_SANDBOX_EXECUTABLE` plus
`ASTRA_SANDBOX_IMAGE`. The sandbox mounts the workspace read-only with no network and resource
limits; do not grant `command.execute:workspace` unless the tenant policy and approval flow permit
the specific command.

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

### Connect to running secure stack

The simplest development connection uses the bundled local login:

```bash
ASTRA_TUI_DEV_LOGIN=true ASTRA_AGENT_URL=http://127.0.0.1:8000 uv run astra-tui
```

Alternatively, export the bearer token shown below as `ASTRA_ACCESS_TOKEN` before running the TUI.
The token carries the tenant claim. `ASTRA_TENANT_ID` is only needed when using unauthenticated
development headers, not with bearer-token authentication.

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
