# Astra Operator Console

## Purpose

The Astra Operator Console is the local web interface for observing and operating one tenant's
control-plane state. It is separate from the Textual TUI: the TUI is for conversations, while the
console is for operational review and explicitly authorized actions.

The browser never receives database, Docker, model-provider, object-store, or mTLS credentials.
It calls the FastAPI control plane, which validates the bearer token and applies tenant and role
authorization before every operation.

## Local startup

The console is included in the secure Compose stack and listens on `http://localhost:3000`.

```bash
export MARIADB_ROOT_PASSWORD='your-local-database-password'
export ASTRA_TRUSTED_PROXY_SECRET='generate-a-long-random-value'
export ASTRA_DOCKER_SOCKET="$(docker context inspect --format '{{.Endpoints.docker.Host}}' | sed 's|^unix://||')"
export ASTRA_SANDBOX_WORKSPACE_ROOT="$HOME/Downloads"
docker compose -f compose.yaml -f compose.secure.yaml up -d --build
```

Before opening the console, verify the control plane:

```bash
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:3000 >/dev/null
```

## Development access

The local Keycloak realm supplies a development account:

| Setting | Value |
| --- | --- |
| Realm | `astra-agent` |
| User | `astra-dev` |
| Password | `development-only` |
| OAuth client | `astra-tui` |
| Tenant | `00000000-0000-0000-0000-000000000001` |
| Role | `platform_operator` |

Generate a fresh access token each time it expires:

```bash
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
```

Paste `$ASTRA_ACCESS_TOKEN` into the console's sign-in form. Local access tokens normally expire in
five minutes. Do not use the development account, password grant, or tokens in production.

## Roles

Roles are Keycloak realm roles carried in the validated OIDC token. The API, not the console,
enforces them.

| Role | Console access |
| --- | --- |
| `platform_operator` | Read all operational data and execute console actions. |
| `tenant_admin` | Read all operational data and execute console actions for its tenant. |
| `auditor` | Read-only access. Mutating actions are denied. |

Every role remains tenant-scoped through the required `tenant_id` token claim.

## Production Keycloak Import

`deploy/keycloak-production-realm.json` now includes the three operator roles and an
`astra-console` public OIDC client. The client permits only authorization-code flow with PKCE
`S256`; direct password grants, implicit flow, and service accounts remain disabled.

After importing it, assign each operator the tenant UUID attribute and the minimum required realm
role. Before enabling the browser-login roadmap, configure the console client's exact HTTPS
redirect URI and matching web origin. The import deliberately leaves both lists empty because no
production hostname is safe to assume. Tokens issued to `astra-console` include the `tenant_id`
claim and `astra-agent` audience required by the API; production must set
`ASTRA_OIDC_AUDIENCE=astra-agent`.

## Workstreams

### Overview

Shows task, approval, ARA, and failed-job counts plus the model, vector, artifact, and sandbox
posture. Use **Refresh** to reload immediately; the console also polls while open.

### Approvals

Lists pending capability requests. **Grant** and **Deny** call the role-protected admin approval
endpoint. A granted local tool invocation wakes its waiting conversation turn. Review the requested
capability and reason before granting it.

### Remote agents

Lists ARAs registered to the tenant, including status, trust level, runtime version, and last seen
time. Registration and trust provisioning remain deployment/operator tasks; the console does not
issue certificates or accept arbitrary ARA identities.

### Memory

Lists candidate memories. **Promote** makes a candidate eligible for contextual retrieval and queues
vector synchronization. **Reject** prevents it from becoming active memory. These decisions are
persisted and audited by the control plane.

### Jobs

Shows durable memory extraction and vector synchronization jobs. Failed jobs retain their error and
retry information. Resolve the underlying dependency or configuration problem before restarting the
API; the in-process runner resumes durable work.

### Audit

Shows the recent tenant-scoped append-only audit event stream. It is for observability, not a place
to modify state.

### Artifacts

Lists authorized artifact metadata. **Download** asks the API to authorize the record in MariaDB
before it issues a short-lived object-store URL. The browser never chooses object keys directly.

## Current boundaries

The current console deliberately does not edit provider configuration, workspace roots, sandbox
configuration, Keycloak/OIDC settings, mTLS certificates, or secrets. Those values are currently
bootstrap/deployment configuration. Moving them into the console requires durable versioned
configuration records, migration support, approval/audit policy, and secret-manager references.

The local stack exposes a Docker socket only to create bounded local command sandboxes. It is
powerful host-level access and is appropriate only for development. Use a dedicated restricted
container runtime in production.

## UI Roadmap

The console has functional operational lists and core actions. The following work remains before it
is a complete platform-administration interface. Build in this order so every UI control has a
durable, enforced, auditable server-side behavior.

### 1. Browser OIDC session

Replace the pasted-token form with Keycloak authorization-code flow plus PKCE.

- Add an `astra-console` Keycloak public client with exact `http://localhost:3000` redirect URI,
  PKCE `S256`, the required `tenant_id` mapper, and API audience.
- Add Next.js login/callback/logout route handlers.
- Store access and refresh tokens only in an encrypted HttpOnly server session.
- Add a Next.js same-origin API proxy that attaches the server-side access token to Astra API calls.
- Retain pasted bearer tokens only behind an explicit development-only feature flag.

### 2. Task workstream

Add a task list and a task-detail drawer using `GET /api/v1/admin/tasks/{task_id}`.

- Show objective, context, required capabilities, target/completing ARA, lease state, result, and
  partial-failure status.
- Render task-scoped progress events as a chronological timeline with percentage and message.
- Display task artifacts and their authorized download actions.
- Add cancellation confirmation for leased tasks, explaining that cancellation is requested and
  observed by the ARA on heartbeat rather than being an immediate process kill.

### 3. Approval detail and confirmation

The approval queue needs a detail drawer before an operator acts.

- Render `capability.kind` and `capability.scope` from the structured payload, never the JavaScript
  object string.
- Display linked task/tool-invocation metadata and, for `run_command`, the bounded argv and trusted
  workspace scope.
- Add a confirmation dialog for grant and deny, including the actor identity and a clear irreversible
  decision warning.
- Keep action buttons disabled for `auditor` users, even though the API remains the final authority.

### 4. ARA lifecycle controls

The API now supports trust changes and permanent revocation; add the corresponding UI only with
explicit safeguards.

- Add ARA detail drawer with capabilities, health age, active lease/task, and audit history.
- Add a bounded trust-level selector for active ARAs.
- Add a destructive revoke dialog explaining that revocation sets trust to zero, expires active
  leases, and survives attempted ARA re-registration.
- Do not add an un-revoke button. Re-enrollment should be a separate certificate/identity process.

### 5. Job recovery details

The jobs table should become a recovery workflow.

- Add filters for failed, retrying, running, and completed jobs.
- Show immutable attempt history from `list_job_attempts`, including timestamps and structured errors.
- Add a requeue confirmation for failed jobs. Requeue resets the active job retry count but retains
  historic attempts and writes `job.requeued` to audit events.
- Redact memory-extraction payload content in browser responses; the console must not expose raw
  conversation content merely to show job diagnostics.

### 6. Memory provenance and curation

Expand the candidate-memory cards into a review drawer.

- Show source message ID, source event ID, kind, confidence, retention, visibility, and contradiction
  target.
- Link to the source conversation only after an authorized conversation-detail API exists.
- Support selecting a replacement promoted memory when resolving a contradiction.
- Add filters for candidate, promoted, rejected, and deleted memories plus review history.

### 7. Audit usability

- Add event-type, actor, task, and time-window filters.
- Add event detail JSON with sensitive fields redacted server-side.
- Add pagination/cursor support before retaining long tenant histories in the browser.
- Export only server-generated, tenant-authorized audit reports.

### 8. Versioned tenant configuration

Do not add general settings forms over environment variables. First introduce a dedicated durable,
versioned configuration record and audited change requests.

Safe first fields may include a tenant display name and bounded non-security presentation preferences.
Do not put workspace paths, capability grants, Docker/Podman settings, provider URLs, OIDC settings,
artifact credentials, model API keys, or mTLS material in tenant configuration.

For provider/sandbox/secret administration, use platform-operator-only pages that edit versioned
references to a secret manager such as Vault. Never return raw secret values after creation.

### 9. Frontend engineering

- Split the current single console page into route-level workstreams and reusable data-table, drawer,
  dialog, status-pill, and empty-state components.
- Generate TypeScript API contracts from FastAPI OpenAPI rather than maintaining loose
  `Record<string, unknown>` response shapes.
- Add TanStack Query (or equivalent) for polling, cache invalidation, optimistic-state avoidance, and
  mutation error handling.
- Add Playwright coverage for sign-in, auditor read-only behavior, approval confirmation, ARA revoke,
  job requeue, memory review, and artifact download authorization.
- Preserve the Bonjour-derived visual system: app rail, operations rail, segmented workstream tabs,
  coral active states, soft neutral surfaces, compact tables, and mobile rail collapse.

## API surface

The console uses these OIDC-protected API endpoints:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/admin/me` | Validates operator identity and roles. |
| `GET /api/v1/admin/overview` | Returns tenant operational counts and runtime posture. |
| `GET /api/v1/admin/operations` | Returns tenant tasks, approvals, ARAs, jobs, memories, events, and artifacts. |
| `POST /api/v1/admin/approvals/{id}/decision` | Grants or denies an approval. |
| `POST /api/v1/admin/tasks/{id}/cancel` | Requests task cancellation. |
| `POST /api/v1/admin/memories/{id}/review` | Promotes or rejects a memory candidate. |

All admin endpoints require an operator role. Mutating endpoints require `platform_operator` or
`tenant_admin`.
