# Astra Agent Continuation Brief

You are continuing implementation of **Astra Agent**, a hosted Distributed Enriched-Persona
Agent (**D.E.P.A.**), in `/Users/cedric/Repos/depa`.

Read and follow:

- `/Users/cedric/Repos/depa/AGENTS.md`
- `/Users/cedric/Repos/depa/README.md`
- `/Users/cedric/Repos/depa/sources/distributed-enriched-persona-agent-mvp-spec.md`

Treat everything under `/Users/cedric/Repos/depa/sources/` as immutable reference material.
Do not edit, rename, move, or delete it.

## Product identity

- Product: **Astra Agent**.
- Architecture: Distributed Enriched-Persona Agent (**D.E.P.A.**).
- Remote execution nodes: **Astra Remote Agents (ARA)**.
- Python namespaces use `astra_*`; the ARA SDK uses `astra_ara_sdk`.
- Commands are `astra-agent`, `astra-tui`, and `ara`.
- Configuration uses `ASTRA_*` and `ASTRA_ARA_*`.
- Transport headers use `X-Astra-*`.
- ARA routes use `/api/v1/aras/*`.
- Do not restore legacy Depa/worker compatibility aliases.

## Fixed architecture

- Python 3.12+ `uv` monorepo.
- FastAPI Astra Agent control plane.
- Textual/Rich TUI.
- MariaDB is authoritative for state and authorization.
- Qdrant ranks only records already authorized by MariaDB.
- S3-compatible storage holds artifacts.
- OpenRouter is the sole v1 main-model provider.
- A swappable local model handles memory extraction and classification.
- ARAs authenticate with mTLS and pull bounded leases.
- Capabilities are explicit and default-deny.
- No recursive ARA delegation.
- No PostgreSQL, pgvector, browser automation, scheduling, plugin marketplace, or extra user
  channels in this MVP.

## Current implemented baseline

The following is complete and tested. Do not rebuild it from scratch:

- Typed tenant-owned domain, protocol, policy, runtime, memory, model, and ARA SDK packages.
- In-memory and transactional MariaDB persistence with Alembic migrations.
- OIDC user authentication and trusted-ingress mTLS ARA identity.
- Versioned FastAPI routes for conversations, tasks, ARAs, approvals, audit, memory, and artifacts.
- Multi-turn TUI conversations with automatic local Keycloak login for development.
- OpenRouter chat using `deepseek/deepseek-v4-pro` through an adapter.
- Bounded persona/history/memory context compilation.
- Structured memory extraction, candidates, promotion/rejection, contradiction replacement,
  provenance, deletion, and MariaDB-authorized Qdrant ranking.
- Atomic ARA registration, leasing, renewal, progress, approval, completion, cancellation, lease
  expiry, and task result persistence.
- A real read-only repository ARA that returns file and line evidence.
- Deterministic planning for explicit repository inspection requests and synthesis of ARA results.
- Presigned S3/MinIO uploads with object scope, size, media type, and SHA-256 verification.
- Secure local Compose stack with MariaDB, Qdrant, MinIO, Keycloak, Astra Agent, and nginx mTLS.
- Optional Ollama profile for local memory extraction.
- Fresh local infrastructure uses database `astra_agent`, collection `astra_memories`, bucket
  `astra-artifacts`, and table `remote_agents`.

The current full test suite passes. Re-run it before and after substantial work rather than
assuming this statement remains true.

## Next milestone: reliable asynchronous operations

Implement this milestone before broadening product scope.

1. Add a tenant-owned, versioned `PersonaProfile` with an immutable authored core for values,
   boundaries, tone, initiative level, emotional range, and disagreement style. Store learned
   adaptations separately; memory extraction must never modify the authored core.
2. Add authenticated tenant-scoped persona read, update, and revert endpoints. Compile only the
   active persona version into bounded context and record persona changes in the audit trail.
3. Add a durable background-job model and MariaDB queue for memory extraction, embedding/index
   synchronization, and retryable maintenance work.
4. Persist job attempts, state, next-attempt time, bounded exponential backoff, terminal failure,
   tenant ownership, source record, and structured error details.
5. Ensure raw conversations remain successful when local-model or Qdrant processing fails.
6. Add an Astra Agent background runner that safely claims jobs with transactional locking and
   supports graceful shutdown.
7. Make memory extraction idempotent by source message and make Qdrant synchronization
   recoverable after partial failure.
8. Add an optional local-model context reranking/compression adapter. Keep deterministic bounded
   compilation as the fallback and preserve the configured context-token budget.
9. Add authenticated, tenant-scoped artifact metadata and download endpoints using short-lived
   presigned GET URLs.
10. Authorize every artifact through MariaDB before generating a URL; never trust an object key
   supplied by the caller and never use S3 existence as authorization.
11. Add artifact deletion/retention behavior with append-only audit events.
12. Improve ARA resilience: heartbeat/offline status, lease renewal during long execution,
   cancellation observation, bounded retries, and structured failure reporting.
13. Surface background failures, ARA health, artifact downloads, and active persona version in
    the TUI without blocking
   conversation input.

## Following milestone: richer orchestration

After asynchronous reliability is solid:

1. Replace keyword-only delegation with a typed planner decision contract behind the main-model
   adapter.
2. Validate every plan against policy before creating tasks.
3. Select ARAs by tenant, capability, health, trust, and availability.
4. Support bounded parallel sibling tasks while preserving the no-recursive-delegation rule.
5. Synthesize multiple structured ARA results with explicit provenance and partial-failure
   handling.
6. Expand read-only specialist ARAs only when their capability and deliverable contracts are
   explicit and tested.

## Production hardening track

Keep local development usable while adding:

- Structured logs with request, tenant, conversation, task, lease, ARA, and job correlation IDs.
- Metrics and readiness checks for MariaDB, Qdrant, S3, OIDC/JWKS, model providers, and queue lag.
- Rate limits, request-size limits, timeouts, and model/token/cost accounting.
- Secret-manager integration and documented key/certificate rotation.
- Managed PKI and revocation behavior for ARA certificates.
- Backups, restore verification, migration rollback guidance, and retention policies.
- Network policy preventing direct access around the mTLS ingress.
- HTTPS for user-facing OIDC and Astra Agent traffic.

The bundled Keycloak credentials, generated CA, and development secrets are local-demo assets,
not production security controls.

## Engineering constraints

- Inspect existing contracts and tests before editing.
- Make the smallest correct change and preserve established package boundaries.
- Keep modules typed and use explicit protocols for persistence, models, vectors, artifacts,
  background jobs, and ARA transport.
- `tenant_id` is mandatory on durable user-owned data, jobs, task messages, and artifacts.
- MariaDB is always the authorization source of truth.
- Qdrant payload filters must include tenant and visibility metadata, but are defense in depth only.
- Persist raw user input before optional model, memory, vector, or ARA work.
- The authored persona core is user/admin-controlled and immutable to automatic memory or model
  updates; learned adaptations must be attributable, inspectable, and reversible.
- Do not expose secrets in source, logs, tests, documentation, or final responses.
- Do not add compatibility shims for the previous project name.
- Do not weaken TLS/OIDC verification to make local tests pass.
- Treat writes, commands, network, credentials, and external messaging as denied or approval-gated.
- Add adversarial tenant-isolation, concurrency, retry, and partial-failure tests for new behavior.

## Verification

At minimum run:

```bash
uv sync --all-packages
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest -q
```

For infrastructure changes, also run the relevant live MariaDB, Qdrant, MinIO, OIDC, mTLS, ARA,
and TUI smoke tests described in `README.md`. Report precisely what passed, what remains optional,
and any production-only work that cannot be validated locally.

## Definition of done for the next milestone

- The active persona is tenant-owned, versioned, auditable, and bounded in compiled context;
  automatic memory processing cannot modify its authored core.
- Conversation latency does not depend on memory extraction or Qdrant availability.
- Failed memory/vector work is durable, visible, retryable, and idempotent.
- Local-model context compression is optional, bounded, and has a deterministic fallback.
- Restarting Astra Agent does not lose or duplicate queued work.
- Artifact downloads are short-lived and authorized through MariaDB.
- ARAs renew leases, report failures, observe cancellation, and become offline when heartbeats stop.
- The TUI exposes task/job/ARA failure state without disabling chat.
- In-memory, MariaDB, API, concurrency, tenant-isolation, and live-service tests pass.
- README setup and operational instructions match the tested commands.
