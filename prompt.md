You are implementing the MVP for a hosted, distributed agent platform in `/Users/cedric/Repos/depa`.

Read and follow:

- `/Users/cedric/Repos/depa/AGENTS.md`
- `/Users/cedric/Repos/depa/sources/distributed-enriched-persona-agent-mvp-spec.md`

Do not modify anything under `/Users/cedric/Repos/depa/sources/`.

## Product goal

Build a hosted coordinator for an enriched-persona agent. A TUI user can converse with the agent; the coordinator compiles compact persona/memory context, delegates bounded work to mTLS-authenticated remote workers, and records auditable events.

## Fixed MVP decisions

- Python 3.12+ monorepo.
- FastAPI for the hosted coordinator and worker control plane.
- Textual/Rich for the TUI.
- MariaDB is the authoritative database.
- Qdrant is used only for vector retrieval.
- S3-compatible object storage is for artifacts.
- OpenRouter is the sole main-model provider in v1, behind an adapter.
- A swappable local-model adapter handles memory extraction, ranking, and context compilation.
- Workers authenticate through mTLS and pull bounded task leases from the coordinator.
- Default-deny capability policy; no implicit write, command, network, credential, or external-message access.
- No recursive worker delegation.
- No PostgreSQL, pgvector, plugin marketplace, multi-channel integrations, browser automation, or scheduling in this MVP.

## First implementation milestone

Create a runnable, well-structured foundation—not the entire product.

Implement:

1. Python workspace/package configuration and developer setup.
2. Shared typed domain models for tenants, tasks, leases, workers, capabilities, approvals, events, persona, and memory records.
3. A FastAPI coordinator with health checks and versioned API routing.
4. Worker registration and task-lease endpoints with an authentication abstraction prepared for mTLS.
5. An append-only audit-event interface with a MariaDB-backed implementation boundary.
6. A simple in-memory implementation for local development so the API can run before infrastructure is configured.
7. A Textual TUI that connects to the coordinator health endpoint and displays a minimal conversation/task screen.
8. Configuration through typed environment settings, with safe defaults and no secrets committed.
9. Focused tests for domain validation, task leasing, and policy defaults.
10. Clear README instructions for local setup and the next development milestone.

Use these root directories:

```text
apps/
  coordinator-api/
  tui/
  worker/
packages/
  domain/
  protocol/
  runtime/
  memory/
  policy/
  model-providers/
  worker-sdk/
```

## Engineering requirements

- Start by inspecting the repository and presenting a concise implementation plan.
- Preserve the specification’s boundaries; do not add unrelated frameworks or integrations.
- Keep modules small and typed.
- Prefer explicit interfaces/protocols around persistence, models, vectors, artifacts, and worker transport.
- Treat `tenant_id` as mandatory on all durable, user-owned data and task messages.
- Design Qdrant filters to include tenant and visibility metadata, but never treat Qdrant filtering as the authorization source of truth.
- Do not pretend mTLS or MariaDB integrations are complete if they are only scaffolded; label placeholders clearly.
- Run the relevant tests and report what passes, what remains stubbed, and the recommended next milestone.

Begin with the foundation milestone above. Do not attempt to implement full memory intelligence, OpenRouter chat, or actual remote execution until the shared contracts, task lifecycle, policy boundary, and TUI-to-coordinator path are solid.